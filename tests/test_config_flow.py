"""Setup must finish in the UI, and every failure must say which one it is.

RFC-001 section 10. The four errors below need four different actions from the
user, and telling them apart is the whole reason this flow has more than one
branch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.kinboard.api import (
    KinboardAuthError,
    KinboardConnectionError,
    KinboardVersionError,
)
from custom_components.kinboard.const import CONF_BASE_URL, CONF_TOKEN, DOMAIN

from .conftest import FAMILY_ID, INFO

USER_INPUT = {CONF_BASE_URL: "http://kinboard.test", CONF_TOKEN: "kbi_test"}


def _patch_info(**kwargs):
    """Patch the client the config flow builds, not the one __init__ builds."""
    client = AsyncMock()
    client.async_get_info = AsyncMock(**kwargs)
    return patch("custom_components.kinboard.config_flow.KinboardClient", return_value=client)


async def _submit(hass, **kwargs):
    with _patch_info(**kwargs):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )


async def test_happy_path_creates_one_entry_per_family(hass):
    with patch("custom_components.kinboard.async_setup_entry", return_value=True):
        result = await _submit(hass, return_value=INFO)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Testfamilie"
    # Keyed on the family, not the URL: the same family over Tailscale and over
    # the LAN is one instance, not two.
    assert result["result"].unique_id == FAMILY_ID


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("1.9.0", True),
        ("1.9.1", True),
        ("2.0.0", True),
        # The regression. Semver sorts a pre-release before its release, so a
        # naive comparison rejected release candidates of the very version
        # being required — and the people running an rc are the testers.
        ("1.9.0-rc.1", True),
        ("1.9.0-beta.2", True),
        ("1.8.4", False),
        ("1.0.0", False),
    ],
)
async def test_version_gate(hass, version, accepted):
    with patch("custom_components.kinboard.async_setup_entry", return_value=True):
        result = await _submit(hass, return_value={**INFO, "version": version})
    if accepted:
        assert result["type"] is FlowResultType.CREATE_ENTRY
    else:
        assert result["errors"] == {"base": "unsupported_version"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (KinboardAuthError("nope"), "invalid_auth"),
        (KinboardConnectionError("down"), "cannot_connect"),
        (KinboardVersionError("old"), "unsupported_version"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_each_failure_gets_its_own_message(hass, error, expected):
    result = await _submit(hass, side_effect=error)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_a_token_that_cannot_read_is_refused_up_front(hass):
    """Better now than as a mystery empty dashboard later."""
    result = await _submit(hass, return_value={**INFO, "scopes": ["shopping:write"]})
    assert result["errors"] == {"base": "missing_scope"}


async def test_write_scopes_are_optional(hass):
    """Publishing entities into HA without granting any write is legitimate."""
    with patch("custom_components.kinboard.async_setup_entry", return_value=True):
        result = await _submit(hass, return_value={**INFO, "scopes": ["family:read"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_family_cannot_be_added_twice(hass, config_entry):
    config_entry.add_to_hass(hass)
    result = await _submit(hass, return_value=INFO)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
