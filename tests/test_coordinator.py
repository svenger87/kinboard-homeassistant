"""Polling and the event cursor.

The cursor is the part worth testing hardest. A missed poll costs nothing —
the next one fixes it. A missed event is gone for good, so the ordering
guarantee ("dispatch, then advance") is a correctness property, not a detail.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState

from custom_components.kinboard.api import KinboardAuthError, KinboardConnectionError
from custom_components.kinboard.const import DOMAIN


def _event(event_id: int, event_type: str = "kinboard_task_completed", **payload):
    return {
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload or {"task_id": "t1"},
    }


async def test_known_events_reach_the_bus(hass, config_entry, mock_client):
    """An automation triggers on `platform: event`, so the type is fired verbatim."""
    seen = []
    hass.bus.async_listen("kinboard_task_completed", lambda e: seen.append(e.data))

    mock_client.async_get_events.return_value = {"events": [_event(1, task_id="t-42")], "has_more": False}
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert seen == [{"task_id": "t-42"}]


async def test_an_unknown_event_type_is_skipped_but_still_advances_the_cursor(
    hass, config_entry, mock_client
):
    """A newer Kinboard may emit events this version has never heard of.

    Ignoring them is right. Refusing to move past them would wedge the cursor
    permanently and stop every later event — which would make any Kinboard
    upgrade a breaking one.
    """
    mock_client.async_get_events.return_value = {"events": [
        _event(7, "kinboard_something_from_the_future")
    ], "has_more": False}
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    mock_client.async_get_events.reset_mock()
    mock_client.async_get_events.return_value = {"events": [], "has_more": False}
    await coordinator.async_refresh()

    mock_client.async_get_events.assert_awaited_once_with(after_id=7)


async def test_the_cursor_resumes_rather_than_replays(hass, config_entry, mock_client):
    """RFC-001 section 7: a restart of either system loses nothing."""
    mock_client.async_get_events.return_value = {"events": [_event(3), _event(5), _event(4)], "has_more": False}
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    mock_client.async_get_events.reset_mock()
    mock_client.async_get_events.return_value = {"events": [], "has_more": False}
    await coordinator.async_refresh()

    # The highest id seen, not the last one in the list.
    mock_client.async_get_events.assert_awaited_once_with(after_id=5)


async def test_a_failed_event_fetch_leaves_entities_alone(hass, config_entry, mock_client):
    """Entity state is still good and the cursor has not moved, so nothing is lost."""
    mock_client.async_get_events.side_effect = KinboardConnectionError("flaky")
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.last_update_success


async def test_a_dead_token_asks_for_reauth_instead_of_looping(
    hass, config_entry, mock_client
):
    """Kinboard tokens are designed to be rotated, so this is a routine path."""
    mock_client.async_get_summary.side_effect = KinboardAuthError("revoked")
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )
