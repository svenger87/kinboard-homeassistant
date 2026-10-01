"""add_pocket_money and dismiss_attention, down to the exact payload sent.

Both were declared with RFC-001's field names from the start and both failed
on every call, because Kinboard read different ones (svenger87/kinboard#309).
Nothing on this side noticed, because nothing here looked at what actually
went over the wire. These tests do — and they also pin the two shortcuts that
make the services usable at all: picking a child by their sensor rather than
by a person_id nobody knows, and dismissing the top item without its key.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kinboard.api import KinboardRequestError
from custom_components.kinboard.const import CONF_BASE_URL, CONF_TOKEN, DOMAIN

from .conftest import FAMILY_ID, SUMMARY


def _entity_id(hass, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{FAMILY_ID}_{key}")
    assert entity_id, f"no {platform} entity registered for {key}"
    return entity_id


def _sent(mock_client) -> tuple[str, dict]:
    """The one service call that reached the client: (service, payload)."""
    assert mock_client.async_call_service.await_count == 1
    args = mock_client.async_call_service.await_args
    return args.args[0], args.args[1]


async def _call(hass, service: str, data: dict) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


# -- add_pocket_money -------------------------------------------------------


async def test_pocket_money_by_entity_sends_the_person_id(hass, setup_integration, mock_client):
    """The child is picked by their sensor; Kinboard is sent their person_id."""
    purse = _entity_id(hass, "sensor", "pocket_money_child-1")

    await _call(hass, "add_pocket_money", {"entity_id": purse, "amount": 2.5, "reason": "Rasen gemaeht"})

    assert _sent(mock_client) == (
        "add_pocket_money",
        {"person_id": "child-1", "amount": 2.5, "reason": "Rasen gemaeht"},
    )


async def test_pocket_money_by_person_id_is_passed_through(hass, setup_integration, mock_client):
    await _call(hass, "add_pocket_money", {"person_id": "child-1", "amount": -1, "reason": "Eis"})

    assert _sent(mock_client) == (
        "add_pocket_money",
        {"person_id": "child-1", "amount": -1.0, "reason": "Eis"},
    )


async def test_an_amount_written_as_text_is_sent_as_a_number(hass, setup_integration, mock_client):
    """YAML makes "2.50" as easy to write as 2.50; Kinboard accepts only a number."""
    await _call(hass, "add_pocket_money", {"person_id": "child-1", "amount": "2.50", "reason": "x"})

    assert _sent(mock_client)[1]["amount"] == 2.5


async def test_the_entity_still_resolves_while_the_sensor_is_unavailable(
    hass, setup_integration, mock_client
):
    """A purse missing from one poll must not make the child unpayable.

    The mapping comes from the entity registry, not from the live state.
    """
    purse = _entity_id(hass, "sensor", "pocket_money_child-1")
    coordinator = setup_integration.runtime_data
    coordinator.async_set_updated_data({**SUMMARY, "pocket_money": []})
    await hass.async_block_till_done()
    assert hass.states.get(purse).state == "unavailable"

    await _call(hass, "add_pocket_money", {"entity_id": purse, "amount": 1, "reason": "x"})

    assert _sent(mock_client)[1]["person_id"] == "child-1"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"amount": 1, "reason": "x"}, id="neither"),
        pytest.param(
            {"entity_id": "sensor.mia_pocket_money", "person_id": "child-1", "amount": 1, "reason": "x"},
            id="both",
        ),
    ],
)
async def test_exactly_one_way_of_naming_the_child(hass, setup_integration, mock_client, data):
    with pytest.raises(ServiceValidationError, match="exactly one of entity_id"):
        await _call(hass, "add_pocket_money", data)
    mock_client.async_call_service.assert_not_awaited()


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("shopping_items", id="another kinboard sensor"),
        pytest.param("saving_goals_mia_tesla siku", id="a saving goal"),
    ],
)
async def test_only_a_pocket_money_sensor_names_a_child(
    hass, setup_integration, mock_client, key
):
    """Any other sensor — ours or not — is refused by name, not sent as a guess."""
    entity_id = _entity_id(hass, "sensor", key)
    with pytest.raises(ServiceValidationError, match="not a Kinboard pocket money sensor"):
        await _call(hass, "add_pocket_money", {"entity_id": entity_id, "amount": 1, "reason": "x"})
    mock_client.async_call_service.assert_not_awaited()


async def test_a_foreign_entity_is_refused(hass, setup_integration, mock_client):
    with pytest.raises(ServiceValidationError, match="not a Kinboard pocket money sensor"):
        await _call(hass, "add_pocket_money", {"entity_id": "sensor.outside_temperature", "amount": 1, "reason": "x"})
    mock_client.async_call_service.assert_not_awaited()


async def test_zero_is_refused_before_it_reaches_kinboard(hass, setup_integration, mock_client):
    with pytest.raises(ServiceValidationError, match="non-zero"):
        await _call(hass, "add_pocket_money", {"person_id": "child-1", "amount": 0, "reason": "x"})
    mock_client.async_call_service.assert_not_awaited()


async def test_the_picked_child_chooses_the_family(hass, setup_integration, mock_client):
    """With two families configured, the child's sensor already says which one.

    Without this the call would demand an entry_id that the person picking a
    child from a list has no way to know.
    """
    other_client = AsyncMock()
    other_client.base_url = "http://other.test"
    other_client.async_get_summary.return_value = SUMMARY
    other_client.async_get_events.return_value = {"events": [], "has_more": False}
    other_client.async_get_calendar_events.return_value = []
    other_client.async_get_list.return_value = []
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Nachbarn",
        unique_id="99999999-2222-3333-4444-555555555555",
        data={CONF_BASE_URL: "http://other.test", CONF_TOKEN: "kbi_other"},
    )
    other.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=other_client):
        assert await hass.config_entries.async_setup(other.entry_id)
        await hass.async_block_till_done()

    purse = _entity_id(hass, "sensor", "pocket_money_child-1")
    mock_client.async_call_service.reset_mock()
    await _call(hass, "add_pocket_money", {"entity_id": purse, "amount": 1, "reason": "x"})

    assert _sent(mock_client)[1]["person_id"] == "child-1"
    other_client.async_call_service.assert_not_awaited()


async def test_an_older_kinboard_says_it_needs_updating(hass, setup_integration, mock_client):
    """The payload stays RFC-shaped, so a Kinboard from before the fix still
    refuses it — but the error now names the server, not just "400"."""
    mock_client.async_call_service.side_effect = KinboardRequestError(
        "`person` and a non-zero `amount` are required"
    )
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "add_pocket_money", {"person_id": "child-1", "amount": 1, "reason": "x"})

    message = str(err.value)
    assert "`person` and a non-zero `amount` are required" in message
    assert "svenger87/kinboard#309" in message


# -- dismiss_attention ------------------------------------------------------


async def test_an_explicit_attention_id_is_sent_as_given(hass, setup_integration, mock_client):
    await _call(hass, "dismiss_attention", {"attention_id": "permission-slip:mia"})

    assert _sent(mock_client) == ("dismiss_attention", {"attention_id": "permission-slip:mia"})


async def test_without_an_id_the_top_item_is_dismissed(hass, setup_integration, mock_client):
    await _call(hass, "dismiss_attention", {})

    assert _sent(mock_client) == ("dismiss_attention", {"attention_id": "dentist:2026-08-12"})


async def test_nothing_outstanding_is_said_plainly(hass, setup_integration, mock_client):
    setup_integration.runtime_data.async_set_updated_data(
        {**SUMMARY, "attention_required": False,
         "attention": {"count": 0, "top": None, "top_key": None, "items": []}}
    )
    with pytest.raises(ServiceValidationError, match="Nothing is outstanding"):
        await _call(hass, "dismiss_attention", {})
    mock_client.async_call_service.assert_not_awaited()


async def test_an_older_kinboard_without_keys_asks_for_an_id(hass, setup_integration, mock_client):
    """{count, top} with no top_key: there is something, but no way to name it."""
    setup_integration.runtime_data.async_set_updated_data(
        {**SUMMARY, "attention": {"count": 1, "top": "Zahnarzt"}}
    )
    with pytest.raises(ServiceValidationError, match="Pass attention_id, or update Kinboard"):
        await _call(hass, "dismiss_attention", {})
    mock_client.async_call_service.assert_not_awaited()


# -- binary_sensor.kinboard_attention_required ------------------------------


async def test_the_attention_sensor_names_what_is_outstanding(hass, setup_integration):
    state = hass.states.get(_entity_id(hass, "binary_sensor", "attention_required"))

    assert state.state == "on"
    assert state.attributes["count"] == 2
    assert state.attributes["top"] == "Zahnarzt-Termin bestaetigen"
    assert state.attributes["top_key"] == "dentist:2026-08-12"
    # Key and title only: priority is the server's ordering, already applied.
    assert state.attributes["items"] == [
        {"key": "dentist:2026-08-12", "title": "Zahnarzt-Termin bestaetigen"},
        {"key": "permission-slip:mia", "title": "Elternbrief unterschreiben"},
    ]


async def test_items_stay_bounded_and_out_of_the_recorder(hass, setup_integration):
    """Every recorded attribute is copied on each state change, forever."""
    from custom_components.kinboard.binary_sensor import KinboardAttentionRequired

    assert "items" in KinboardAttentionRequired._unrecorded_attributes

    many = [{"key": f"k{i}", "title": f"t{i}", "priority": 1} for i in range(25)]
    setup_integration.runtime_data.async_set_updated_data(
        {**SUMMARY, "attention": {"count": 25, "top": "t0", "top_key": "k0", "items": many}}
    )
    await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass, "binary_sensor", "attention_required"))
    assert len(state.attributes["items"]) == 10
    assert state.attributes["count"] == 25


async def test_an_older_kinboard_degrades_to_what_it_sends(hass, setup_integration):
    """A server from before #309 sends {count, top}. The sensor shows those and
    does not invent an empty `items` that would contradict `count`."""
    setup_integration.runtime_data.async_set_updated_data(
        {**SUMMARY, "attention": {"count": 1, "top": "Zahnarzt"}}
    )
    await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass, "binary_sensor", "attention_required"))
    assert state.state == "on"
    assert state.attributes["count"] == 1
    assert state.attributes["top"] == "Zahnarzt"
    assert "top_key" not in state.attributes
    assert "items" not in state.attributes


async def test_no_attention_object_at_all_is_just_the_boolean(hass, setup_integration):
    summary = {k: v for k, v in SUMMARY.items() if k != "attention"}
    setup_integration.runtime_data.async_set_updated_data(summary)
    await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass, "binary_sensor", "attention_required"))
    assert state.state == "on"
    assert not {"count", "top", "top_key", "items"} & set(state.attributes)


async def test_the_child_picker_lists_exactly_the_pocket_money_sensors(hass, setup_integration):
    """The selector can only filter by integration, domain and device class, so
    the device class is what keeps saving goals and counts out of the list."""
    from pathlib import Path

    import yaml

    services = yaml.safe_load(
        (Path(__file__).parent.parent / "custom_components" / DOMAIN / "services.yaml").read_text()
    )
    wanted = services["add_pocket_money"]["fields"]["entity_id"]["selector"]["entity"]["filter"]
    assert wanted == {"integration": DOMAIN, "domain": "sensor", "device_class": "monetary"}

    registry = er.async_get(hass)
    offered = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, setup_integration.entry_id)
        if e.domain == "sensor"
        and hass.states.get(e.entity_id).attributes.get("device_class") == "monetary"
    }
    assert offered == {_entity_id(hass, "sensor", "pocket_money_child-1")}
