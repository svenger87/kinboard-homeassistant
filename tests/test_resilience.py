"""RFC-001 section 7 acceptance criteria, as tests rather than a one-off check.

Three promises are made to anyone writing an automation:

* a restart of either system loses nothing
* no event is delivered twice
* a token cannot be used outside the scopes it was granted

Each is only worth anything if it keeps being true, so each is pinned here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from custom_components.kinboard.api import KinboardAuthError, KinboardConnectionError
from custom_components.kinboard.const import DOMAIN, STORAGE_KEY_CURSOR


def _event(event_id: int, event_type: str = "kinboard_task_completed"):
    return {"event_id": event_id, "event_type": event_type, "payload": {"n": event_id}}


def _collect(hass) -> list[int]:
    """Record delivered event ids, in order.

    Decorated with @callback so Home Assistant runs the listener inline on the
    event loop. A plain function is handed to the executor instead, and the
    appends then race — which looks exactly like the integration delivering
    events out of order.
    """
    seen: list[int] = []

    @callback
    def _listen(event):
        seen.append(event.data["n"])

    hass.bus.async_listen("kinboard_task_completed", _listen)
    return seen


async def _setup(hass, entry, client):
    with patch("custom_components.kinboard.KinboardClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


# -- a restart of either system loses nothing -----------------------------


async def test_home_assistant_restart_resumes_from_the_cursor(
    hass, config_entry, mock_client, hass_storage
):
    """The cursor is persisted, so a restart resumes instead of replaying.

    Replaying would re-fire every event an automation had already acted on —
    for a household that means the lights flashing again for a task ticked
    yesterday.
    """
    seen = _collect(hass)

    config_entry.add_to_hass(hass)
    mock_client.async_get_events.return_value = {"events": [_event(1), _event(2)], "has_more": False}
    await _setup(hass, config_entry, mock_client)
    assert seen == [1, 2]

    # The cursor reached disk, which is what survives the process going away.
    stored = hass_storage[f"{STORAGE_KEY_CURSOR}.{config_entry.entry_id}"]
    assert stored["data"] == {"after_id": 2}

    # Home Assistant restarting: the entry is torn down and set up again while
    # the stored cursor stays where it is.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.async_get_events.reset_mock()
    mock_client.async_get_events.return_value = {"events": [], "has_more": False}
    await _setup(hass, config_entry, mock_client)

    mock_client.async_get_events.assert_awaited_with(after_id=2)
    assert seen == [1, 2], "an event was replayed after the restart"


async def test_kinboard_restart_recovers_without_intervention(
    hass, config_entry, mock_client
):
    """Kinboard going away is a poll failure, not a broken configuration.

    Entities go unavailable and come back. Requiring the user to reload the
    integration after every Kinboard update would make updating Kinboard a
    two-system chore.
    """
    config_entry.add_to_hass(hass)
    await _setup(hass, config_entry, mock_client)
    coordinator = config_entry.runtime_data
    assert coordinator.last_update_success

    mock_client.async_get_summary.side_effect = KinboardConnectionError("restarting")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success

    mock_client.async_get_summary.side_effect = None
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert config_entry.state is ConfigEntryState.LOADED


async def test_events_missed_while_kinboard_was_down_are_not_lost(
    hass, config_entry, mock_client
):
    """The cursor does not advance on a failed fetch, so the next poll
    collects everything that happened in between."""
    seen = _collect(hass)

    config_entry.add_to_hass(hass)
    mock_client.async_get_events.return_value = {"events": [_event(1)], "has_more": False}
    await _setup(hass, config_entry, mock_client)

    coordinator = config_entry.runtime_data
    mock_client.async_get_events.side_effect = KinboardConnectionError("down")
    await coordinator.async_refresh()

    mock_client.async_get_events.side_effect = None
    mock_client.async_get_events.return_value = {"events": [_event(2), _event(3)], "has_more": False}
    await coordinator.async_refresh()

    assert seen == [1, 2, 3]
    mock_client.async_get_events.assert_awaited_with(after_id=1)


# -- no duplicate events --------------------------------------------------


async def test_an_event_is_never_delivered_twice(hass, config_entry, mock_client):
    """A server that re-sends what we already have must not re-fire it.

    The cursor is sent on every request, so this is really a check that it is
    advanced and used — but the failure it guards against is an automation
    running twice, which is the kind of bug a household notices and cannot
    diagnose.
    """
    seen = _collect(hass)

    config_entry.add_to_hass(hass)
    mock_client.async_get_events.return_value = {"events": [_event(1), _event(2)], "has_more": False}
    await _setup(hass, config_entry, mock_client)

    coordinator = config_entry.runtime_data
    for _ in range(3):
        await coordinator.async_refresh()

    assert seen == [1, 2]
    # Every later request asked for events after the highest id already seen.
    assert all(
        call.kwargs["after_id"] == 2
        for call in mock_client.async_get_events.await_args_list[1:]
    )


async def test_the_cursor_never_goes_backwards(hass, config_entry, mock_client):
    """Out-of-order ids in one batch must not lower it.

    Taking the last id rather than the highest would re-deliver everything
    between them on the next poll.
    """
    config_entry.add_to_hass(hass)
    mock_client.async_get_events.return_value = {"events": [_event(9), _event(4), _event(7)], "has_more": False}
    await _setup(hass, config_entry, mock_client)

    coordinator = config_entry.runtime_data
    mock_client.async_get_events.return_value = {"events": [], "has_more": False}
    await coordinator.async_refresh()

    mock_client.async_get_events.assert_awaited_with(after_id=9)


async def test_a_server_that_resends_is_not_believed(hass, config_entry, mock_client):
    """The real defence, independent of the server filtering correctly.

    Kinboard filters on `id > after`, so this should never happen. But the
    promise is to every automation a household has written, and it should not
    rest on the other side staying correct forever — a doubled event flashes
    the lights twice and cannot be diagnosed from the outside.
    """
    seen = _collect(hass)

    config_entry.add_to_hass(hass)
    mock_client.async_get_events.return_value = {"events": [_event(1), _event(2)], "has_more": False}
    await _setup(hass, config_entry, mock_client)
    assert seen == [1, 2]

    # A misbehaving server hands back the same page, plus something genuinely new.
    coordinator = config_entry.runtime_data
    mock_client.async_get_events.return_value = {
        "events": [_event(1), _event(2), _event(3)],
        "has_more": False,
    }
    await coordinator.async_refresh()

    assert seen == [1, 2, 3], "an already-delivered event was fired again"


async def test_a_backlog_is_drained_in_one_cycle(hass, config_entry, mock_client):
    """`has_more` means "come back immediately", and now we do.

    Fetching one page per poll meant that after a few hours of Kinboard being
    unreachable a household caught up at 100 events a minute — the events were
    not lost, but an automation could fire an hour after the thing it reacts to.
    """
    seen = _collect(hass)

    pages = [
        {"events": [_event(1), _event(2)], "has_more": True},
        {"events": [_event(3), _event(4)], "has_more": True},
        {"events": [_event(5)], "has_more": False},
    ]
    mock_client.async_get_events.side_effect = list(pages)

    config_entry.add_to_hass(hass)
    await _setup(hass, config_entry, mock_client)

    assert seen == [1, 2, 3, 4, 5]
    assert mock_client.async_get_events.await_count == 3


async def test_draining_stops_when_a_page_adds_nothing(hass, config_entry, mock_client):
    """A server stuck on `has_more: true` must not spin the loop forever."""
    config_entry.add_to_hass(hass)
    # Always the same already-seen page, always claiming there is more.
    mock_client.async_get_events.return_value = {"events": [_event(1)], "has_more": True}
    await _setup(hass, config_entry, mock_client)

    coordinator = config_entry.runtime_data
    mock_client.async_get_events.reset_mock()
    await coordinator.async_refresh()

    # One request, which advanced nothing, and then it gave up.
    assert mock_client.async_get_events.await_count == 1


# -- a write cannot act outside its scopes --------------------------------


async def test_a_service_call_outside_its_scope_fails_loudly(
    hass, setup_integration, mock_client
):
    """Kinboard rejects it; Home Assistant has to say so rather than swallow it.

    A silent no-op is the worst outcome — the automation looks like it ran and
    the shopping item never appears.
    """
    mock_client.async_call_service.side_effect = KinboardAuthError("missing scope tasks:write")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "create_task", {"title": "Müll rausbringen"}, blocking=True
        )

    # The message has to name the likely cause; "403" sends people to the
    # wrong place entirely.
    assert "scope" in str(err.value).lower()


async def test_a_scope_failure_does_not_take_the_integration_down(
    hass, setup_integration, mock_client
):
    """One refused call is a permissions problem, not a broken connection."""
    mock_client.async_call_service.side_effect = KinboardAuthError("nope")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "create_note", {"text": "hallo"}, blocking=True
        )

    assert setup_integration.state is ConfigEntryState.LOADED
    assert setup_integration.runtime_data.last_update_success
