"""The Kinboard integration.

Kinboard is a self-hosted family dashboard. This integration makes it a
first-class participant in Home Assistant: it publishes what the family has on
today as entities, accepts service calls to add shopping items and tasks, and
forwards Kinboard's own events onto the bus so automations can react to them.

Contract: RFC-001 in the Kinboard repository.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from .api import KinboardAuthError, KinboardClient, KinboardError
from .const import (
    CONF_BASE_URL,
    CONF_TOKEN,
    DOMAIN,
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

    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not reg_entry.original_name:
            continue

        untouched = f"{reg_entry.domain}.{slugify(f'{entry.title} {reg_entry.original_name}')}"
        if reg_entry.entity_id != untouched:
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
        if entry_id:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                raise HomeAssistantError(f"Unknown Kinboard entry: {entry_id}")
        elif len(entries) > 1:
            raise HomeAssistantError(
                "More than one Kinboard family is configured — pass entry_id to "
                "say which one this call is for."
            )
        else:
            entry = entries[0]

        coordinator: KinboardCoordinator = entry.runtime_data
        payload = {k: v for k, v in call.data.items() if k != "entry_id"}
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
        except KinboardError as err:
            raise HomeAssistantError(f"Kinboard call {call.service} failed: {err}") from err

        # A write may change what the sensors report, so refresh rather than
        # leaving HA showing stale counts until the next poll.
        await coordinator.async_request_refresh()

    for service in ALL_SERVICES:
        hass.services.async_register(
            DOMAIN, service, _handle, schema=vol.Schema({}, extra=vol.ALLOW_EXTRA)
        )
