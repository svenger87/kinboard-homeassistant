"""Shared fixtures.

The client is mocked rather than the HTTP layer for everything above `api.py`,
and the HTTP layer is exercised directly in `test_api.py`. That split keeps the
entity tests about entity behaviour instead of about JSON plumbing, while still
leaving the wire format covered exactly once.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kinboard.const import CONF_BASE_URL, CONF_TOKEN, DOMAIN

FAMILY_ID = "11111111-2222-3333-4444-555555555555"

INFO: dict[str, Any] = {
    "version": "1.9.0",
    "family_id": FAMILY_ID,
    "family_name": "Testfamilie",
    "scopes": ["family:read", "events:read", "shopping:write", "tasks:write"],
}

# Shaped like the real endpoint, including the two shapes a sensor has to cope
# with: an object carrying `state` plus detail, and a bare scalar.
SUMMARY: dict[str, Any] = {
    # Field-for-field what the live endpoint returns. Inventing a tidier shape
    # here would have hidden the `start`/`start_at` mismatch instead of
    # catching it.
    "next_family_event": {
        "state": "Jonas Physio",
        "id": "evt-1",
        "title": "Jonas Physio",
        "start_at": "2026-08-09T15:00:00+02:00",
        "location": "Praxis",
        "person_id": "child-1",
        "minutes_remaining": 90,
    },
    "events_today": {"state": 2, "events": ["Jonas Physio", "Elternabend"]},
    "shopping_items": 3,
    "tasks_due": {"state": 1, "open": 1, "overdue": 0},
    "tasks_overdue": 0,
    "birthdays_upcoming": {
        "state": "Lena Weber",
        "name": "Lena Weber",
        "days_remaining": 12,
        "date": "2026-08-21",
    },
    "school_tomorrow": {
        "state": "Mia",
        "children": ["Mia"],
        "count": 1,
        "first_lesson": "Mathe",
    },
    # Null on purpose: nobody has planned a meal, which is a real state and
    # must survive as "unknown" rather than becoming 0 or "".
    "meal_today": {"state": None, "meal": None, "recipe_id": None},
    "meal_tomorrow": {"state": None, "meal": None, "recipe_id": None},
    "display_mode": None,
    "attention_required": False,
    "pocket_money": [
        {"person_id": "child-1", "name": "Mia", "balance": 12.5, "currency": "EUR"},
    ],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Without this, Home Assistant refuses to load anything from custom_components."""
    yield


@pytest.fixture
def mock_client() -> AsyncMock:
    """A stand-in for KinboardClient with every call stubbed."""
    client = AsyncMock()
    client.base_url = "http://kinboard.test"
    client.async_get_info.return_value = INFO
    client.async_get_summary.return_value = SUMMARY
    client.async_get_events.return_value = []
    client.async_get_calendar_events.return_value = []
    client.async_get_list.return_value = []
    return client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Testfamilie",
        unique_id=FAMILY_ID,
        data={CONF_BASE_URL: "http://kinboard.test", CONF_TOKEN: "kbi_test"},
    )


@pytest.fixture
async def setup_integration(hass, config_entry, mock_client):
    """A fully set-up entry with the client mocked out."""
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.kinboard.KinboardClient", return_value=mock_client
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry
