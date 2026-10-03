"""A doorbell ringing puts its camera on Kinboard's wall displays.

The rule for "rang" is tested transition by transition, because both ways of
getting it wrong are bad: a missed ring is the feature not working, and a
phantom one takes over every screen in the house with nobody at the door.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import ANY, patch

import pytest
from homeassistant.core import State
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.kinboard.api import (
    KinboardAuthError,
    KinboardConnectionError,
    KinboardRateLimitError,
    KinboardRequestError,
    KinboardVersionError,
)
from custom_components.kinboard.const import DOMAIN
from custom_components.kinboard.diagnostics import async_get_config_entry_diagnostics
from custom_components.kinboard.doorbell import is_ring

BELL = "binary_sensor.front_door_ding"
OTHER_BELL = "event.back_door_doorbell"

CAMERAS = [
    {"id": "cam-front", "name": "Front door", "doorbell_entity_id": BELL},
    {"id": "cam-garden", "name": "Garden", "doorbell_entity_id": None},
]


def _ago(seconds: float) -> str:
    return (dt_util.utcnow() - timedelta(seconds=seconds)).isoformat()


# -- the rule -----------------------------------------------------------------


def _s(entity_id: str, state: str | None) -> State | None:
    return None if state is None else State(entity_id, state)


@pytest.mark.parametrize(
    ("old", "new", "rings"),
    [
        pytest.param("off", "on", True, id="off to on"),
        pytest.param("on", "off", False, id="on to off"),
        pytest.param("on", "on", False, id="attribute change while on"),
        pytest.param(None, "on", False, id="startup or restore"),
        pytest.param("unavailable", "on", False, id="back online already on"),
        pytest.param("unknown", "on", False, id="initialising"),
        pytest.param("off", "unavailable", False, id="dropping off"),
        pytest.param("off", None, False, id="removed"),
    ],
)
def test_binary_sensor_rings_only_off_to_on(old, new, rings):
    entity = "binary_sensor.door"
    assert is_ring(_s(entity, old), _s(entity, new)) is rings


@pytest.mark.parametrize("domain", ["event", "button", "input_button"])
@pytest.mark.parametrize(
    ("old", "new", "rings"),
    [
        pytest.param("PREV", "NOW", True, id="new press"),
        pytest.param("unknown", "NOW", True, id="first press ever"),
        pytest.param(None, "NOW", False, id="startup or restore"),
        pytest.param("unavailable", "NOW", False, id="back online"),
        pytest.param("unavailable", "PREV", False, id="back online with old press"),
        pytest.param("PREV", "unavailable", False, id="dropping off"),
        pytest.param("PREV", "unknown", False, id="cleared"),
        pytest.param("NOW", "NOW", False, id="unchanged"),
        pytest.param("PREV", "STALE", False, id="an old timestamp replayed"),
        pytest.param("unknown", "STALE", False, id="an old timestamp out of unknown"),
        pytest.param("PREV", "garbage", False, id="not a timestamp"),
    ],
)
def test_timestamp_entities_ring_on_a_new_recent_press(domain, old, new, rings):
    now = dt_util.utcnow().isoformat()
    values = {"NOW": now, "PREV": _ago(3600), "STALE": _ago(600)}
    entity = f"{domain}.door"
    old_state = _s(entity, values.get(old, old))
    new_state = _s(entity, values.get(new, new))
    assert is_ring(old_state, new_state) is rings


def test_other_domains_never_ring():
    assert not is_ring(State("sensor.door", "1"), State("sensor.door", "2"))


# -- the watcher in a running Home Assistant ---------------------------------


@pytest.fixture
def clock():
    """The debounce clock, moved by hand."""
    now = [1000.0]
    with patch("custom_components.kinboard.doorbell._monotonic", side_effect=lambda: now[0]):
        yield now


@pytest.fixture
async def doorbells(hass, config_entry, mock_client, clock):
    """An entry whose Kinboard has one camera with a doorbell."""
    mock_client.async_get_cameras.return_value = CAMERAS
    # The bell exists, and is off, before the integration starts.
    hass.states.async_set(BELL, "off")
    hass.states.async_set(OTHER_BELL, "unknown")
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry


async def _set(hass, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _ring(hass, entity_id: str = BELL) -> None:
    await _set(hass, entity_id, "off")
    await _set(hass, entity_id, "on")


def _shown(mock_client) -> list[str]:
    return [c.args[0] for c in mock_client.async_show_camera.await_args_list]


async def test_a_ring_shows_its_camera_with_the_servers_default_duration(hass, doorbells, mock_client):
    await _set(hass, BELL, "on")

    mock_client.async_show_camera.assert_awaited_once_with("cam-front")


async def test_the_state_a_bell_has_at_startup_is_not_a_ring(hass, config_entry, mock_client, clock):
    """A bell that is already `on` when Home Assistant starts did not just ring."""
    mock_client.async_get_cameras.return_value = CAMERAS
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    await _set(hass, BELL, "on")  # first state the entity ever reports
    await _set(hass, BELL, "unavailable")
    await _set(hass, BELL, "on")  # back online, still on

    mock_client.async_show_camera.assert_not_awaited()


async def test_an_event_doorbell_rings_on_its_first_press(hass, config_entry, mock_client, clock):
    mock_client.async_get_cameras.return_value = [
        {"id": "cam-back", "name": "Back door", "doorbell_entity_id": OTHER_BELL}
    ]
    hass.states.async_set(OTHER_BELL, "unknown")
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    await _set(hass, OTHER_BELL, dt_util.utcnow().isoformat())

    mock_client.async_show_camera.assert_awaited_once_with("cam-back")


async def test_a_bouncing_bell_shows_the_camera_once_per_ten_seconds(hass, doorbells, mock_client, clock):
    await _ring(hass)
    clock[0] += 3
    await _ring(hass)
    clock[0] += 6.9  # 9.9 s after the first
    await _ring(hass)
    assert _shown(mock_client) == ["cam-front"]

    clock[0] += 0.2  # 10.1 s after the first
    await _ring(hass)
    assert _shown(mock_client) == ["cam-front", "cam-front"]


async def test_the_debounce_is_per_bell(hass, config_entry, mock_client, clock):
    mock_client.async_get_cameras.return_value = [
        *CAMERAS,
        {"id": "cam-back", "name": "Back door", "doorbell_entity_id": "binary_sensor.back"},
    ]
    hass.states.async_set(BELL, "off")
    hass.states.async_set("binary_sensor.back", "off")
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    await _ring(hass, BELL)
    await _ring(hass, "binary_sensor.back")

    assert _shown(mock_client) == ["cam-front", "cam-back"]


async def test_other_entities_are_not_watched(hass, doorbells, mock_client):
    await _ring(hass, "binary_sensor.kitchen_window")
    mock_client.async_show_camera.assert_not_awaited()


async def test_a_changed_mapping_is_picked_up_on_the_next_poll(hass, doorbells, mock_client, clock):
    """The old bell stops ringing the camera, the new one starts — no restart."""
    hass.states.async_set("binary_sensor.side_gate", "off")
    mock_client.async_get_cameras.return_value = [
        {"id": "cam-front", "name": "Front door", "doorbell_entity_id": "binary_sensor.side_gate"},
    ]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()
    assert mock_client.async_get_cameras.await_count == 2

    await _ring(hass, BELL)
    mock_client.async_show_camera.assert_not_awaited()

    await _ring(hass, "binary_sensor.side_gate")
    mock_client.async_show_camera.assert_awaited_once_with("cam-front")


async def test_a_failed_poll_keeps_the_doorbell_working(hass, doorbells, mock_client):
    mock_client.async_get_cameras.side_effect = KinboardConnectionError("down")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()

    await _ring(hass)
    mock_client.async_show_camera.assert_awaited_once_with("cam-front")


async def test_unloading_stops_listening(hass, doorbells, mock_client):
    assert await hass.config_entries.async_unload(doorbells.entry_id)
    await hass.async_block_till_done()

    await _ring(hass)
    mock_client.async_show_camera.assert_not_awaited()


async def test_a_bell_that_is_not_a_bell_is_not_watched(hass, config_entry, mock_client, caplog):
    mock_client.async_get_cameras.return_value = [
        {"id": "cam-front", "name": "Front door", "doorbell_entity_id": "light.porch"},
    ]
    hass.states.async_set("light.porch", "off")
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    await _ring(hass, "light.porch")
    mock_client.async_show_camera.assert_not_awaited()
    assert "light.porch" in caplog.text


# -- what Kinboard answers ----------------------------------------------------


def _our_warnings(caplog) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.startswith("custom_components.kinboard")
    ]


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"show_camera_forbidden_{entry.entry_id}")


async def test_a_missing_scope_raises_a_repair_that_clears_on_success(
    hass, doorbells, mock_client, clock, caplog
):
    mock_client.async_show_camera.side_effect = KinboardAuthError("nope", status=403)

    await _ring(hass)
    clock[0] += 11
    await _ring(hass)

    issue = _issue(hass, doorbells)
    assert issue is not None
    assert issue.translation_key == "show_camera_forbidden"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "announcements:write" in r.message]
    assert len(warnings) == 1, "one warning per entry, not one per ring"

    mock_client.async_show_camera.side_effect = None
    clock[0] += 11
    await _ring(hass)
    assert _issue(hass, doorbells) is None


async def test_a_dead_token_is_left_to_reauth(hass, doorbells, mock_client):
    """401 is the coordinator's business; the repair is about a missing permission."""
    mock_client.async_show_camera.side_effect = KinboardAuthError("nope", status=401)
    await _ring(hass)
    assert _issue(hass, doorbells) is None


async def test_a_kinboard_without_cameras_turns_the_feature_off_quietly(
    hass, config_entry, mock_client, caplog
):
    mock_client.async_get_cameras.side_effect = KinboardVersionError("404")
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()

    watcher = config_entry.runtime_data.doorbells
    assert watcher.supported is False
    assert watcher.mapping == {}
    assert not _our_warnings(caplog)
    assert caplog.text.count("has no camera list") == 1


async def test_an_unknown_camera_is_a_warning_naming_both(hass, doorbells, mock_client, caplog):
    mock_client.async_show_camera.side_effect = KinboardRequestError('no camera "cam-front"')
    await _ring(hass)
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert BELL in warning.message and "cam-front" in warning.message


async def test_rate_limiting_is_not_a_warning(hass, doorbells, mock_client, caplog):
    mock_client.async_show_camera.side_effect = KinboardRateLimitError("429", retry_after=60)
    await _ring(hass)
    assert not _our_warnings(caplog)


async def test_an_unreachable_kinboard_is_a_warning_not_a_crash(hass, doorbells, mock_client, clock, caplog):
    mock_client.async_show_camera.side_effect = KinboardConnectionError("timeout")
    await _ring(hass)
    assert any(r.levelno == logging.WARNING and BELL in r.message for r in caplog.records)

    mock_client.async_show_camera.side_effect = None
    clock[0] += 11
    await _ring(hass)
    assert mock_client.async_show_camera.await_count == 2


# -- diagnostics ----------------------------------------------------------------


async def test_diagnostics_show_the_mapping(hass, doorbells):
    diag = await async_get_config_entry_diagnostics(hass, doorbells)
    assert diag["doorbells"] == {
        "supported": True,
        "listening": True,
        "mapping": {BELL: "cam-front"},
    }
    assert "kbi_test" not in str(diag)


# -- the action -----------------------------------------------------------------


async def test_the_action_sends_camera_duration_and_screens(hass, setup_integration, mock_client):
    await hass.services.async_call(
        DOMAIN,
        "show_camera",
        {"camera": "Front door", "duration": 30.0, "target_devices": ["Kitchen", " "]},
        blocking=True,
    )
    mock_client.async_show_camera.assert_awaited_once_with(
        "Front door", duration=30, target_devices=["Kitchen"], idempotency_key=ANY
    )
    assert isinstance(mock_client.async_show_camera.await_args.kwargs["duration"], int)


async def test_the_action_with_only_a_camera(hass, setup_integration, mock_client):
    await hass.services.async_call(DOMAIN, "show_camera", {"camera": "cam-1"}, blocking=True)
    mock_client.async_show_camera.assert_awaited_once_with(
        "cam-1", duration=None, target_devices=None, idempotency_key=ANY
    )


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({}, id="no camera"),
        pytest.param({"camera": "  "}, id="blank camera"),
        pytest.param({"camera": "x", "duration": 4}, id="too short"),
        pytest.param({"camera": "x", "duration": 301}, id="too long"),
        pytest.param({"camera": "x", "durration": 30}, id="misspelt field"),
    ],
)
async def test_the_action_refuses_what_kinboard_would(hass, setup_integration, mock_client, data):
    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "show_camera", data, blocking=True)
    mock_client.async_show_camera.assert_not_awaited()


async def test_the_action_says_when_it_is_rate_limited(hass, setup_integration, mock_client):
    from homeassistant.exceptions import HomeAssistantError

    mock_client.async_show_camera.side_effect = KinboardRateLimitError("429", retry_after=42)
    with pytest.raises(HomeAssistantError, match="rate limiting.*42 s"):
        await hass.services.async_call(DOMAIN, "show_camera", {"camera": "x"}, blocking=True)


async def test_the_action_names_the_missing_scope(hass, setup_integration, mock_client):
    from homeassistant.exceptions import HomeAssistantError

    mock_client.async_show_camera.side_effect = KinboardAuthError("403", status=403)
    with pytest.raises(HomeAssistantError, match="announcements:write"):
        await hass.services.async_call(DOMAIN, "show_camera", {"camera": "x"}, blocking=True)
