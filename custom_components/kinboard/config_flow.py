"""Config and reauth flows.

RFC-001 section 10 requires setup to complete entirely in the UI with no YAML,
and requires connection failures and token expiry to be legible. That second
part is most of the code here: the difference between "wrong address",
"rejected token", "too old" and "missing scope" is exactly what a user cannot
work out for themselves, so each gets its own message.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from awesomeversion import AwesomeVersion

from .api import (
    KinboardAuthError,
    KinboardClient,
    KinboardConnectionError,
    KinboardVersionError,
)
from .const import CONF_BASE_URL, CONF_TOKEN, DOMAIN, MIN_KINBOARD_VERSION

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): str,
        vol.Required(CONF_TOKEN): str,
    }
)

# Reading is the floor: without it there is nothing to show. Write scopes are
# reported as a warning, not an error, because an install that only publishes
# entities to HA is a legitimate configuration.
REQUIRED_SCOPES = {"family:read"}


class KinboardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Connect Home Assistant to a Kinboard instance."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry_data: dict[str, Any] | None = None

    async def _validate(self, base_url: str, token: str) -> tuple[dict[str, Any] | None, str | None]:
        """Return (info, error_key). Exactly one is None."""
        session = async_get_clientsession(self.hass)
        client = KinboardClient(session, base_url, token)
        try:
            info = await client.async_get_info()
        except KinboardAuthError:
            return None, "invalid_auth"
        except KinboardVersionError:
            return None, "unsupported_version"
        except KinboardConnectionError:
            return None, "cannot_connect"
        except Exception:  # noqa: BLE001 — surfaced as "unknown", and logged
            _LOGGER.exception("Unexpected error validating the Kinboard connection")
            return None, "unknown"

        version = info.get("version")
        if version and AwesomeVersion(version) < AwesomeVersion(MIN_KINBOARD_VERSION):
            return None, "unsupported_version"

        scopes = set(info.get("scopes") or [])
        if not REQUIRED_SCOPES.issubset(scopes):
            return None, "missing_scope"

        return info, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            info, error = await self._validate(
                user_input[CONF_BASE_URL], user_input[CONF_TOKEN]
            )
            if error:
                errors["base"] = error
            else:
                assert info is not None
                # One config entry per family, not per URL: the same family
                # reached over Tailscale and over the LAN is one instance.
                await self.async_set_unique_id(info["family_id"])
                self._abort_if_unique_id_configured(updates=dict(user_input))
                return self.async_create_entry(
                    title=info.get("family_name") or "Kinboard",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Token was revoked or rotated. Kinboard tokens are designed to be
        rotated, so this is a routine path, not an exceptional one."""
        self._reauth_entry_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._reauth_entry_data is not None
        base_url = self._reauth_entry_data[CONF_BASE_URL]

        if user_input is not None:
            info, error = await self._validate(base_url, user_input[CONF_TOKEN])
            if error:
                errors["base"] = error
            else:
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            description_placeholders={"base_url": base_url},
            errors=errors,
        )
