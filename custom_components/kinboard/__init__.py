"""The Kinboard integration.

Kinboard is a self-hosted family dashboard. This integration makes it a
first-class participant in Home Assistant: it publishes what the family has on
today as entities, accepts service calls to add shopping items and tasks, and
forwards Kinboard's own events onto the bus so automations can react to them.

Contract: RFC-001 in the Kinboard repository.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from .api import KinboardAuthError, KinboardClient, KinboardError, KinboardRequestError
from .const import (
    CONF_BASE_URL,
    CONF_TOKEN,
    DOMAIN,
    SENSOR_POCKET_MONEY_PREFIX,
    SERVICE_ACTIVATE_CONTEXT,
    SERVICE_ADD_POCKET_MONEY,
    SERVICE_ADD_SHOPPING_ITEM,
    SERVICE_CREATE_NOTE,
    SERVICE_CREATE_TASK,
    SERVICE_DISMISS_ATTENTION,
    SERVICE_REFRESH_INTEGRATION,
    SERVICE_SHOW_ANNOUNCEMENT,
)
from .coordinator import KinboardCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.TODO,
]

ALL_SERVICES = (
    SERVICE_ADD_SHOPPING_ITEM,
    SERVICE_CREATE_TASK,
    SERVICE_CREATE_NOTE,
    SERVICE_SHOW_ANNOUNCEMENT,
    SERVICE_ACTIVATE_CONTEXT,
    SERVICE_DISMISS_ATTENTION,
    SERVICE_ADD_POCKET_MONEY,
    SERVICE_REFRESH_INTEGRATION,
)

# Plain alias rather than PEP 695 `type ... = ...`: that form needs Python
# 3.12, which Home Assistant has but many contributors' local interpreters do
# not, and it makes the file unparseable to any tooling below 3.12 for no gain.
KinboardConfigEntry = ConfigEntry[KinboardCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: KinboardConfigEntry) -> bool:
    """Set up one Kinboard family from a config entry."""
    client = KinboardClient(
        async_get_clientsession(hass), entry.data[CONF_BASE_URL], entry.data[CONF_TOKEN]
    )
    coordinator = KinboardCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    _async_adopt_contract_entity_ids(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


def _async_adopt_contract_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move entities created before the device was renamed onto the contract ids.

    Home Assistant derives an entity_id once, at first registration, and then
    keeps it forever — so renaming the device fixes new installs and leaves
    existing ones on `sensor.<family>_next_birthday`. Those are exactly the
    installs whose owners are most likely to be copying an example automation.

    Only ids that still look auto-generated are touched. If the current id is
    not what this integration would itself have produced from the family name,
    somebody renamed it deliberately, and their automations point at it.
    """
    registry = er.async_get(hass)
    family = slugify(entry.title)

    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not reg_entry.original_name:
            continue

        object_id = reg_entry.entity_id.split(".", 1)[1]

        # Two generations of auto-generated id are recognised.
        #
        # The current one is "<family> <entity name>". The other predates the
        # entity names existing at all: without an `entity:` block in
        # strings.json every entity fell back to the device name, so a family
        # called Weber got sensor.weber, sensor.weber_2, sensor.weber_3 —
        # ids nobody could write an automation against, and the exact state
        # the first real install is still in. Fixing the names later did not
        # move them, because an id is minted once and then kept forever.
        generated = {slugify(f"{entry.title} {reg_entry.original_name}"), family}
        numbered = re.fullmatch(rf"{re.escape(family)}_\d+", object_id) is not None

        if object_id not in generated and not numbered:
            continue

        wanted = f"{reg_entry.domain}.{slugify(f'Kinboard {reg_entry.original_name}')}"
        if wanted == reg_entry.entity_id or registry.async_get(wanted) is not None:
            # Already right, or the name is taken — a rename onto an occupied
            # id would fail, and stealing it would break whatever holds it.
            continue

        _LOGGER.info("Renaming %s to %s to match the published contract", reg_entry.entity_id, wanted)
        registry.async_update_entity(reg_entry.entity_id, new_entity_id=wanted)


async def async_unload_entry(hass: HomeAssistant, entry: KinboardConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        # Services are registered per-domain, not per-entry, so they are only
        # removed once the last Kinboard family goes away.
        for service in ALL_SERVICES:
            hass.services.async_remove(DOMAIN, service)
    return unloaded


# Services whose arguments the integration fills in or translates before they
# go to Kinboard. Everything else is passed through as given.
SERVICE_SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_ADD_POCKET_MONEY: vol.Schema(
        {
            vol.Optional("entry_id"): cv.string,
            vol.Optional("entity_id"): cv.entity_id,
            vol.Optional("person_id"): cv.string,
            # Coerced because a YAML automation can just as easily send "2.50"
            # as 2.50, and Kinboard only accepts a JSON number.
            vol.Required("amount"): vol.Coerce(float),
            vol.Optional("reason"): cv.string,
        },
        extra=vol.ALLOW_EXTRA,
    ),
    SERVICE_DISMISS_ATTENTION: vol.Schema(
        {
            vol.Optional("entry_id"): cv.string,
            vol.Optional("attention_id"): cv.string,
        },
        extra=vol.ALLOW_EXTRA,
    ),
}

# Appended when Kinboard refuses one of the two services that a Kinboard
# release before svenger87/kinboard#309 could not accept from Home Assistant
# at all: it read other field names than RFC-001 gives, so every call was a
# 400. The payload here is the RFC's and stays so; the hint is what tells
# somebody it is the server that needs the update.
_OLD_SERVER_HINT = (
    " If Kinboard says a field is required that this call did send, Kinboard "
    "itself is older than the fix for this service (svenger87/kinboard#309) "
    "and needs updating."
)


def _pocket_money_entry_id(hass: HomeAssistant, entity_id: str) -> str:
    """The config entry a pocket-money sensor belongs to, validating it is one."""
    reg_entry = er.async_get(hass).async_get(entity_id)
    if (
        reg_entry is None
        or reg_entry.platform != DOMAIN
        or reg_entry.domain != "sensor"
        or reg_entry.config_entry_id is None
        or f"_{SENSOR_POCKET_MONEY_PREFIX}_" not in (reg_entry.unique_id or "")
    ):
        raise ServiceValidationError(
            f"{entity_id} is not a Kinboard pocket money sensor. Pick one of the "
            "sensors named \"<child> pocket money\"."
        )
    return reg_entry.config_entry_id


def _pocket_money_person_id(hass: HomeAssistant, entry: ConfigEntry, entity_id: str) -> str:
    """Map a pocket-money sensor to the Kinboard person it shows.

    Read back from the entity registry's unique_id, which the sensor builds as
    "<family>_pocket_money_<person_id>". The registry rather than the live
    entity object, so this still answers while the sensor is unavailable — a
    child whose purse did not come back in the last poll can still be paid.
    """
    reg_entry = er.async_get(hass).async_get(entity_id)
    prefix = f"{entry.unique_id}_{SENSOR_POCKET_MONEY_PREFIX}_"
    if reg_entry is None or not (reg_entry.unique_id or "").startswith(prefix):
        raise ServiceValidationError(
            f"{entity_id} belongs to a different Kinboard family than the one this "
            "call is for."
        )
    return reg_entry.unique_id[len(prefix):]


def _add_pocket_money_payload(
    hass: HomeAssistant, entry: ConfigEntry, data: dict[str, Any]
) -> dict[str, Any]:
    entity_id = data.get("entity_id")
    person_id = data.get("person_id")
    if bool(entity_id) == bool(person_id):
        raise ServiceValidationError(
            "add_pocket_money needs exactly one of entity_id (the child's pocket "
            "money sensor) or person_id."
        )
    amount = data["amount"]
    if amount == 0:
        raise ServiceValidationError("add_pocket_money needs a non-zero amount.")

    payload = {k: v for k, v in data.items() if k != "entity_id"}
    if entity_id:
        payload["person_id"] = _pocket_money_person_id(hass, entry, entity_id)
    return payload


def _dismiss_attention_payload(
    coordinator: KinboardCoordinator, data: dict[str, Any]
) -> dict[str, Any]:
    if data.get("attention_id"):
        return dict(data)

    # Without an id, dismiss what the board is showing on top — the item the
    # binary sensor's `top` attribute names. Read from the last poll rather
    # than a fresh one: that is what the household last saw, and an
    # automation reacting to the sensor is reacting to exactly this data.
    attention = (coordinator.data or {}).get("attention")
    attention = attention if isinstance(attention, dict) else {}
    top_key = attention.get("top_key")
    if top_key:
        return {**data, "attention_id": top_key}
    if attention.get("count") and "top_key" not in attention:
        raise ServiceValidationError(
            "This Kinboard does not say which attention item is on top, so there is "
            "nothing to dismiss by default. Pass attention_id, or update Kinboard."
        )
    raise ServiceValidationError("Nothing is outstanding on Kinboard, so there is nothing to dismiss.")


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the services from RFC-001 section 5.2, once per domain."""
    if hass.services.has_service(DOMAIN, SERVICE_ADD_SHOPPING_ITEM):
        return

    async def _handle(call: ServiceCall) -> None:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("No Kinboard instance is configured.")

        # Services are domain-level but a household could in principle have
        # more than one family configured. Target explicitly when ambiguous
        # rather than silently picking one.
        entry_id = call.data.get("entry_id")
        if (
            not entry_id
            and call.service == SERVICE_ADD_POCKET_MONEY
            and call.data.get("entity_id")
            and not call.data.get("person_id")
        ):
            # The picked child already says which family this is for.
            entry_id = _pocket_money_entry_id(hass, call.data["entity_id"])
        if entry_id:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                raise HomeAssistantError(f"Unknown Kinboard entry: {entry_id}")
            if entry not in entries:
                # Now reachable without naming an entry, through a picked child
                # whose family failed to set up; it has no client to call with.
                raise HomeAssistantError(f"Kinboard family {entry.title} is not loaded.")
        elif len(entries) > 1:
            raise HomeAssistantError(
                "More than one Kinboard family is configured — pass entry_id to "
                "say which one this call is for."
            )
        else:
            entry = entries[0]

        coordinator: KinboardCoordinator = entry.runtime_data
        payload = {k: v for k, v in call.data.items() if k != "entry_id"}
        if call.service == SERVICE_ADD_POCKET_MONEY:
            payload = _add_pocket_money_payload(hass, entry, payload)
        elif call.service == SERVICE_DISMISS_ATTENTION:
            payload = _dismiss_attention_payload(coordinator, payload)
        try:
            await coordinator.client.async_call_service(
                call.service, payload, idempotency_key=str(uuid.uuid4())
            )
        except KinboardAuthError as err:
            # Most often a scope the token does not carry. Say so, rather than
            # letting "403" reach the user.
            raise HomeAssistantError(
                f"Kinboard rejected {call.service}: the integration token may "
                f"lack the required scope ({err})"
            ) from err
        except KinboardRequestError as err:
            hint = (
                _OLD_SERVER_HINT
                if call.service in (SERVICE_ADD_POCKET_MONEY, SERVICE_DISMISS_ATTENTION)
                else ""
            )
            raise HomeAssistantError(f"Kinboard refused {call.service}: {err}.{hint}") from err
        except KinboardError as err:
            raise HomeAssistantError(f"Kinboard call {call.service} failed: {err}") from err

        # A write may change what the sensors report, so refresh rather than
        # leaving HA showing stale counts until the next poll.
        await coordinator.async_request_refresh()

    for service in ALL_SERVICES:
        hass.services.async_register(
            DOMAIN,
            service,
            _handle,
            schema=SERVICE_SCHEMAS.get(service, vol.Schema({}, extra=vol.ALLOW_EXTRA)),
        )
