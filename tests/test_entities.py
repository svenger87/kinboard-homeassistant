"""What the entities actually show.

The theme here is that a state is read by a person on a wall display, not only
by an automation. "Next birthday: 0" is technically a fact and tells nobody
that it is Lena's.
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.kinboard.const import DOMAIN, TODO_LISTS

from .conftest import FAMILY_ID


def _entity_id(hass, platform: str, key: str) -> str:
    """Look up by unique_id — entity_id depends on translations and titles."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{FAMILY_ID}_{key}")
    assert entity_id, f"no {platform} entity registered for {key}"
    return entity_id


async def test_the_state_is_the_human_answer_not_a_count(hass, setup_integration):
    """Home Assistant shows the state on a card and hides attributes.

    So the name goes in the state and the count moves to an attribute, not the
    other way round.
    """
    birthday = hass.states.get(_entity_id(hass, "sensor", "birthdays_upcoming"))
    assert birthday.state == "Lena Weber"
    assert birthday.attributes["days_remaining"] == 12

    school = hass.states.get(_entity_id(hass, "sensor", "school_tomorrow"))
    assert school.state == "Mia"

    event = hass.states.get(_entity_id(hass, "sensor", "next_family_event"))
    assert event.state == "Jonas Physio"
    assert event.attributes["minutes_remaining"] == 90


async def test_a_scalar_payload_is_used_as_is(hass, setup_integration):
    """The API returns either an object with `state` or a bare value."""
    assert hass.states.get(_entity_id(hass, "sensor", "shopping_items")).state == "3"
    assert hass.states.get(_entity_id(hass, "sensor", "tasks_overdue")).state == "0"


async def test_nothing_planned_stays_unknown(hass, setup_integration):
    """An unplanned meal is not zero and not an empty string.

    Coercing it would put "0" on the kitchen display under "Dinner", which
    reads as a fact rather than as an absence.
    """
    assert hass.states.get(_entity_id(hass, "sensor", "meal_today")).state == "unknown"


async def test_attributes_stay_within_the_declared_set(hass, setup_integration):
    """Every state change is written to the recorder database, so this is size.

    An unbounded attribute set is copied on each update, forever.
    """
    tasks = hass.states.get(_entity_id(hass, "sensor", "tasks_due"))
    ours = set(tasks.attributes) - {"friendly_name", "device_class", "state_class", "unit_of_measurement", "icon", "attribution"}
    assert ours == {"open", "overdue"}


async def test_one_pocket_money_sensor_per_child(hass, setup_integration):
    """Created from the poll, because family size is not ours to assume."""
    purse = hass.states.get(_entity_id(hass, "sensor", "pocket_money_child-1"))
    assert purse.state == "12.5"
    assert purse.attributes["unit_of_measurement"] == "EUR"
    assert "Mia" in purse.attributes["friendly_name"]


async def test_both_lists_exist_as_real_todo_entities(hass, setup_integration):
    """Not sensors: the To-do card, voice assistants and todo.* services all
    work with no glue, but only for a genuine todo entity."""
    registry = er.async_get(hass)
    for list_id in TODO_LISTS:
        assert registry.async_get_entity_id(
            "todo", DOMAIN, f"{DOMAIN}_{FAMILY_ID}_todo_{list_id}"
        ), f"{list_id} is not a todo entity"


async def test_due_dates_are_declared_per_list(hass, setup_integration):
    """Tasks have a due date column; shopping items do not.

    Home Assistant validates a service call against supported_features BEFORE
    the entity is asked to do anything, so declaring this globally rejected
    adding a task with a date outright — `update_field_not_supported`, and the
    item was never created.
    """
    from homeassistant.components.todo import TodoListEntityFeature

    registry = er.async_get(hass)

    tasks_id = registry.async_get_entity_id("todo", DOMAIN, f"{DOMAIN}_{FAMILY_ID}_todo_tasks")
    shopping_id = registry.async_get_entity_id("todo", DOMAIN, f"{DOMAIN}_{FAMILY_ID}_todo_shopping")

    tasks = hass.states.get(tasks_id).attributes["supported_features"]
    shopping = hass.states.get(shopping_id).attributes["supported_features"]

    assert tasks & TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    assert not shopping & TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    # Both can still be added to, ticked and cleared.
    for features in (tasks, shopping):
        assert features & TodoListEntityFeature.CREATE_TODO_ITEM
        assert features & TodoListEntityFeature.UPDATE_TODO_ITEM
        assert features & TodoListEntityFeature.DELETE_TODO_ITEM
