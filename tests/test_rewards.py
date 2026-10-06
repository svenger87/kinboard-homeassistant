"""Points, creatures and rewards from Kinboard, and asking for a reward.

Three things are pinned here. What the sensors show, read from GET /rewards.
That an older Kinboard without the endpoint just has no such sensors, the way
it has no doorbells. And that `request_reward` sends exactly what it was
given to the one endpoint that asks -- it never approves anything, Kinboard
has no way for it to -- and turns every refusal into words a person can act on.
"""

from __future__ import annotations

import logging
from copy import deepcopy

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from custom_components.kinboard.api import (
    KinboardAuthError,
    KinboardConnectionError,
    KinboardRateLimitError,
    KinboardRequestError,
    KinboardVersionError,
)
from custom_components.kinboard.const import DOMAIN
from custom_components.kinboard.diagnostics import async_get_config_entry_diagnostics

from .conftest import FAMILY_ID, REWARDS

REWARD_KEYS = ("points_child-1", "creature_stage_child-1", "points_child-2", "creature_stage_child-2", "reward_requests")


def _entity_id(hass, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{FAMILY_ID}_{key}")


def _state(hass, key: str):
    entity_id = _entity_id(hass, key)
    assert entity_id, f"no sensor registered for {key}"
    return hass.states.get(entity_id)


async def _poll(hass, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


# -- the sensors -------------------------------------------------------------


async def test_points_per_child_with_how_they_came_about(hass, setup_integration):
    mia = _state(hass, "points_child-1")
    assert mia.state == "110"
    assert mia.attributes["unit_of_measurement"] == "points"
    assert mia.attributes["earned"] == 130
    assert mia.attributes["owed"] == 0
    assert mia.attributes["pending"] == 50
    assert mia.attributes["available"] == 60
    assert mia.attributes["next_stage_threshold"] == 150
    assert mia.attributes["next_stage_unit"] == "points"
    assert "Mia" in mia.attributes["friendly_name"]


async def test_a_creature_growing_with_money_gives_its_threshold_in_the_currency(hass, setup_integration):
    ben = _state(hass, "points_child-2")
    assert ben.attributes["next_stage_threshold"] == 20.0
    assert ben.attributes["next_stage_unit"] == "EUR"


async def test_the_creature_sensor_names_the_stage(hass, setup_integration):
    """A wall card reads "Hatchling", not "2"; the number is an attribute."""
    creature = _state(hass, "creature_stage_child-1")
    assert creature.state == "Hatchling"
    assert creature.attributes["species"] == "dragon"
    assert creature.attributes["stage"] == 2
    assert creature.attributes["grows_with"] == "points"
    assert creature.attributes["next_stage"] == "Lizard"
    assert "Mia" in creature.attributes["friendly_name"]


async def test_the_family_sensor_counts_the_waiting_requests_and_lists_them(hass, setup_integration):
    requests = _state(hass, "reward_requests")
    assert requests.state == "1"
    assert requests.attributes["requests"] == [
        {
            "id": "req-1",
            "child": "Mia",
            "person_id": "child-1",
            "reward": "Eine Stunde Minecraft",
            "icon": "🎮",
            "cost_points": 50,
            "requested_at": "2026-10-06T08:00:00Z",
        }
    ]


async def test_the_documented_ids(hass, setup_integration):
    """The README names these; a copied automation has to find them."""
    assert _entity_id(hass, "reward_requests") == "sensor.kinboard_reward_requests"
    assert _entity_id(hass, "points_child-1") == "sensor.kinboard_mia_points"
    assert _entity_id(hass, "creature_stage_child-1") == "sensor.kinboard_mia_creature"


async def test_the_request_list_stays_bounded_and_out_of_the_recorder(hass, setup_integration):
    from custom_components.kinboard.sensor import KinboardRewardRequests

    assert "requests" in KinboardRewardRequests._unrecorded_attributes
    coordinator = setup_integration.runtime_data
    many = deepcopy(REWARDS)
    many["pending"] = [{**REWARDS["pending"][0], "id": f"req-{i}"} for i in range(35)]
    coordinator.client.async_get_rewards.return_value = many
    await _poll(hass, setup_integration)

    state = _state(hass, "reward_requests")
    assert state.state == "35"
    assert len(state.attributes["requests"]) == 20


async def test_nothing_of_the_creature_beyond_species_and_stage_reaches_home_assistant(
    hass, config_entry, mock_client
):
    """Kinboard never sends the name a child gave their creature. If a server
    ever did, the attributes are a list of their own, so it still would not
    get through."""
    leaky = deepcopy(REWARDS)
    for child in leaky["children"]:
        child["creature"]["name"] = "Funkel"
        child["creature"]["look"] = {"name": "Funkel", "body": "#FF8A5B"}
    mock_client.async_get_rewards.return_value = leaky
    config_entry.add_to_hass(hass)
    from unittest.mock import patch

    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    for key in REWARD_KEYS:
        state = _state(hass, key)
        assert "Funkel" not in str(state.state)
        assert "Funkel" not in str(dict(state.attributes)), key
        assert "#FF8A5B" not in str(dict(state.attributes)), key


async def test_a_child_whose_creature_is_switched_off_goes_unavailable(hass, setup_integration):
    """Not frozen at the last value: the row is gone from Kinboard's answer."""
    coordinator = setup_integration.runtime_data
    fewer = deepcopy(REWARDS)
    fewer["children"] = fewer["children"][:1]
    coordinator.client.async_get_rewards.return_value = fewer
    await _poll(hass, setup_integration)

    assert _state(hass, "points_child-2").state == "unavailable"
    assert _state(hass, "creature_stage_child-2").state == "unavailable"
    assert _state(hass, "points_child-1").state == "110"


# -- an older Kinboard -------------------------------------------------------


async def _setup_with(hass, config_entry, mock_client):
    from unittest.mock import patch

    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_an_older_kinboard_has_no_reward_sensors_and_nothing_complains(
    hass, config_entry, mock_client, caplog
):
    mock_client.async_get_rewards.side_effect = KinboardVersionError("/rewards not found")
    caplog.set_level(logging.INFO)
    await _setup_with(hass, config_entry, mock_client)

    assert config_entry.state is ConfigEntryState.LOADED
    for key in REWARD_KEYS:
        assert _entity_id(hass, key) is None, key
    # Everything else is there as before.
    assert hass.states.get("sensor.kinboard_next_birthday").state == "Lena Weber"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "reward" in r.getMessage().lower()]
    assert [r for r in caplog.records if r.levelno == logging.INFO and "1.13.0-rc.14" in r.getMessage()]

    # And it is not asked again on every poll.
    await _poll(hass, config_entry)
    await _poll(hass, config_entry)
    assert mock_client.async_get_rewards.await_count == 1
    assert config_entry.runtime_data.rewards_supported is False


async def test_a_failed_read_never_fails_the_poll_and_recovers(hass, setup_integration, caplog):
    coordinator = setup_integration.runtime_data
    coordinator.client.async_get_rewards.side_effect = KinboardConnectionError("timeout")
    await _poll(hass, setup_integration)

    assert coordinator.last_update_success
    assert _state(hass, "points_child-1").state == "unavailable"
    assert _state(hass, "reward_requests").state == "unavailable"
    # The summary sensors are untouched.
    assert hass.states.get("sensor.kinboard_next_birthday").state == "Lena Weber"

    coordinator.client.async_get_rewards.side_effect = None
    await _poll(hass, setup_integration)
    assert _state(hass, "points_child-1").state == "110"
    assert coordinator.rewards_supported is True


async def test_a_failed_first_read_does_not_fail_setup(hass, config_entry, mock_client):
    mock_client.async_get_rewards.side_effect = KinboardConnectionError("timeout")
    await _setup_with(hass, config_entry, mock_client)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.kinboard_next_birthday").state == "Lena Weber"


async def test_sensors_appear_once_a_failed_first_read_succeeds_without_a_reload(
    hass, config_entry, mock_client, caplog
):
    mock_client.async_get_rewards.side_effect = KinboardConnectionError("timeout")
    await _setup_with(hass, config_entry, mock_client)
    for key in REWARD_KEYS:
        assert _entity_id(hass, key) is None, key

    mock_client.async_get_rewards.side_effect = None
    await _poll(hass, config_entry)

    for key in REWARD_KEYS:
        assert _entity_id(hass, key), key
    assert _state(hass, "points_child-1").state == "110"
    assert _state(hass, "reward_requests").state == "1"
    # A later poll adds nothing twice: Home Assistant would refuse the
    # duplicate, but loudly, with an error in the log on every poll.
    caplog.clear()
    await _poll(hass, config_entry)
    await _poll(hass, config_entry)
    assert not [r for r in caplog.records if "already exists" in r.getMessage()]
    registry = er.async_get(hass)
    ours = [e for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)
            if e.unique_id.endswith(("_reward_requests", "_points_child-1"))]
    assert len(ours) == 2


async def test_an_older_kinboard_updated_in_place_gains_the_sensors(hass, config_entry, mock_client):
    """Looked at again after an hour, not on every poll, and no reload needed."""
    from unittest.mock import patch

    clock = [1000.0]
    with patch("custom_components.kinboard.coordinator._monotonic", side_effect=lambda: clock[0]):
        mock_client.async_get_rewards.side_effect = KinboardVersionError("/rewards not found")
        await _setup_with(hass, config_entry, mock_client)
        assert _entity_id(hass, "reward_requests") is None

        # Kinboard is updated; within the hour it is not asked.
        mock_client.async_get_rewards.side_effect = None
        clock[0] += 3599
        await _poll(hass, config_entry)
        assert mock_client.async_get_rewards.await_count == 1
        assert _entity_id(hass, "reward_requests") is None

        clock[0] += 2
        await _poll(hass, config_entry)
        assert mock_client.async_get_rewards.await_count == 2
        assert config_entry.runtime_data.rewards_supported is True
        for key in REWARD_KEYS:
            assert _entity_id(hass, key), key
        assert _state(hass, "creature_stage_child-1").state == "Hatchling"


async def test_a_child_given_a_creature_later_gets_sensors_on_the_next_poll(hass, setup_integration):
    coordinator = setup_integration.runtime_data
    more = deepcopy(REWARDS)
    more["children"].append({
        **deepcopy(REWARDS["children"][0]), "person_id": "child-3", "name": "Ida",
    })
    coordinator.client.async_get_rewards.return_value = more
    await _poll(hass, setup_integration)
    assert _state(hass, "points_child-3").state == "110"
    assert _entity_id(hass, "points_child-3") == "sensor.kinboard_ida_points"


async def test_shop_purchases_show_once_kinboard_sends_them(hass, setup_integration):
    """Kinboard's shop (svenger87/kinboard#375) adds `purchased`; before it, no attribute."""
    assert "purchased" not in _state(hass, "points_child-1").attributes
    coordinator = setup_integration.runtime_data
    shop = deepcopy(REWARDS)
    shop["children"][0]["points"]["purchased"] = 30
    coordinator.client.async_get_rewards.return_value = shop
    await _poll(hass, setup_integration)
    assert _state(hass, "points_child-1").attributes["purchased"] == 30


async def test_diagnostics_count_and_never_name(hass, setup_integration):
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)
    assert diagnostics["rewards"] == {"supported": True, "children": 2, "pending": 1}
    text = str(diagnostics)
    for private in ("Mia", "Ben", "Minecraft", "Hatchling"):
        assert private not in text


# -- request_reward ------------------------------------------------------------


async def _ask(hass, data: dict) -> None:
    await hass.services.async_call(DOMAIN, "request_reward", data, blocking=True)


async def test_request_reward_sends_exactly_child_and_reward_and_refreshes(
    hass, setup_integration, mock_client
):
    before = mock_client.async_get_rewards.await_count
    await _ask(hass, {"child": "  Mia ", "reward": "Eine Stunde Minecraft"})

    assert mock_client.async_request_reward.await_count == 1
    args = mock_client.async_request_reward.await_args
    assert args.args == ("Mia", "Eine Stunde Minecraft")
    assert set(args.kwargs) == {"idempotency_key"}
    # Two asks are two requests, so each carries its own key.
    await _ask(hass, {"child": "Mia", "reward": "Eis"})
    keys = {c.kwargs["idempotency_key"] for c in mock_client.async_request_reward.await_args_list}
    assert len(keys) == 2
    # It goes nowhere else: not through the generic services endpoint.
    mock_client.async_call_service.assert_not_awaited()
    # The waiting-requests sensor is refreshed rather than a poll behind.
    assert mock_client.async_get_rewards.await_count > before


@pytest.mark.parametrize(
    "data",
    [{}, {"child": "Mia"}, {"reward": "Eis"}, {"child": " ", "reward": "Eis"}, {"child": "Mia", "reward": "Eis", "approve": True}],
)
async def test_request_reward_wants_exactly_child_and_reward(hass, setup_integration, mock_client, data):
    with pytest.raises(vol.Invalid):
        await _ask(hass, data)
    mock_client.async_request_reward.assert_not_awaited()


async def test_kinboards_refusal_is_shown_in_its_own_words(hass, setup_integration, mock_client):
    mock_client.async_request_reward.side_effect = KinboardRequestError(
        "This child does not have enough points for that reward, counting the requests already waiting. Nothing was asked.",
        status=409,
    )
    with pytest.raises(HomeAssistantError) as err:
        await _ask(hass, {"child": "Mia", "reward": "Eine Stunde Minecraft"})
    assert "not have enough points" in str(err.value)
    assert "request_reward" in str(err.value)
    # The pocket-money hint about #309 belongs to other services.
    assert "#309" not in str(err.value)


async def test_a_token_without_the_scope_is_told_which(hass, setup_integration, mock_client):
    mock_client.async_request_reward.side_effect = KinboardAuthError("403", status=403)
    with pytest.raises(HomeAssistantError) as err:
        await _ask(hass, {"child": "Mia", "reward": "Eis"})
    assert "pocket_money:write" in str(err.value)
    assert setup_integration.state is ConfigEntryState.LOADED


async def test_an_older_kinboard_says_it_needs_updating(hass, setup_integration, mock_client):
    mock_client.async_request_reward.side_effect = KinboardVersionError("/rewards/requests not found")
    with pytest.raises(HomeAssistantError) as err:
        await _ask(hass, {"child": "Mia", "reward": "Eis"})
    assert "newer than 1.13.0-rc.14" in str(err.value)


async def test_rate_limited_says_when_to_try_again(hass, setup_integration, mock_client):
    mock_client.async_request_reward.side_effect = KinboardRateLimitError("429", retry_after=42)
    with pytest.raises(HomeAssistantError) as err:
        await _ask(hass, {"child": "Mia", "reward": "Eis"})
    assert "42" in str(err.value)


async def test_the_service_is_described_for_people(hass, setup_integration):
    from pathlib import Path
    import json

    import yaml

    root = Path(__file__).parent.parent / "custom_components" / DOMAIN
    services = yaml.safe_load((root / "services.yaml").read_text())
    assert set(services["request_reward"]["fields"]) == {"child", "reward"}
    for name in ("strings.json", "translations/en.json"):
        strings = json.loads((root / name).read_text())
        described = strings["services"]["request_reward"]
        assert "only asks" in described["description"]
        assert "PIN" in described["description"]
        assert set(described["fields"]) == {"child", "reward"}
        assert strings["entity"]["sensor"]["reward_requests"]["name"] == "Reward requests"
