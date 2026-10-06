"""Entity ids are a published contract, so they get pinned like one.

RFC-001 and the README both advertise `sensor.kinboard_next_birthday`. An
automation someone copies from the README has to find that entity, on their
install, whatever their family is called.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.kinboard.const import DOMAIN

from .conftest import FAMILY_ID

# Every id the documentation promises.
CONTRACT_IDS = {
    "sensor.kinboard_next_family_event",
    "sensor.kinboard_events_today",
    "sensor.kinboard_shopping_items",
    "sensor.kinboard_meal_today",
    "sensor.kinboard_meal_tomorrow",
    "sensor.kinboard_tasks_due",
    "sensor.kinboard_tasks_overdue",
    "sensor.kinboard_school_tomorrow",
    "sensor.kinboard_next_birthday",
    "sensor.kinboard_display_mode",
    "binary_sensor.kinboard_attention_required",
    "calendar.kinboard_family_calendar",
    "todo.kinboard_shopping_list",
    "todo.kinboard_tasks",
    # With a Kinboard newer than 1.13.0-rc.14 (points, creatures and rewards).
    "sensor.kinboard_reward_requests",
}


async def test_ids_do_not_depend_on_the_family_name(hass, setup_integration):
    """The fixture's family is "Testfamilie" and no id mentions it."""
    registry = er.async_get(hass)
    produced = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, setup_integration.entry_id)
    }
    assert CONTRACT_IDS <= produced, f"missing: {CONTRACT_IDS - produced}"
    assert not [e for e in produced if "testfamilie" in e]


async def test_the_family_is_still_visible_on_the_device(hass, setup_integration):
    """Moving it out of the entity_id must not lose it entirely."""
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, FAMILY_ID)})
    assert device.name == "Kinboard"
    assert device.model == "Testfamilie"


async def test_an_existing_install_is_migrated(hass, config_entry, mock_client):
    """Ids are minted once and kept forever, so a rename alone fixes only new
    installs — and existing owners are the ones reading the examples."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    # An entity as it would have been registered by the previous version.
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{FAMILY_ID}_birthdays_upcoming",
        suggested_object_id="testfamilie_next_birthday",
        original_name="Next birthday",
        config_entry=config_entry,
    )

    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get("sensor.testfamilie_next_birthday") is None
    assert registry.async_get("sensor.kinboard_next_birthday") is not None


async def test_the_pre_naming_generation_is_migrated_too(hass, config_entry, mock_client):
    """The shape the first real install is actually in.

    Before strings.json had an `entity:` block, every entity fell back to the
    device name, so one family got sensor.weber, sensor.weber_2,
    sensor.weber_3 — ids nobody could write an automation against. Giving the
    entities proper names later did not move them, because an id is minted once
    and then kept forever. Matching only the newer "<family> <name>" pattern
    would skip precisely the install that needs this most.
    """
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for suffix, unique, name in (
        ("", "birthdays_upcoming", "Next birthday"),
        ("_2", "tasks_due", "Tasks due"),
        ("_3", "shopping_items", "Shopping items"),
    ):
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{FAMILY_ID}_{unique}",
            suggested_object_id=f"testfamilie{suffix}",
            original_name=name,
            config_entry=config_entry,
        )

    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    for expected in (
        "sensor.kinboard_next_birthday",
        "sensor.kinboard_tasks_due",
        "sensor.kinboard_shopping_items",
    ):
        assert registry.async_get(expected) is not None, f"{expected} was not migrated"
    assert registry.async_get("sensor.testfamilie_2") is None


async def test_an_id_the_user_chose_is_left_alone(hass, config_entry, mock_client):
    """Their automations point at it. Renaming it would break them silently,
    which is a worse outcome than an inconsistent id."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{FAMILY_ID}_birthdays_upcoming",
        suggested_object_id="wann_hat_wer_geburtstag",
        original_name="Next birthday",
        config_entry=config_entry,
    )

    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get("sensor.wann_hat_wer_geburtstag") is not None
    assert registry.async_get("sensor.kinboard_next_birthday") is None


async def test_migration_does_not_steal_an_occupied_id(hass, config_entry, mock_client):
    """The rename would fail, and taking it would break whatever holds it."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "other_integration", "unrelated",
        suggested_object_id="kinboard_next_birthday",
        original_name="Something else",
    )
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{FAMILY_ID}_birthdays_upcoming",
        suggested_object_id="testfamilie_next_birthday",
        original_name="Next birthday",
        config_entry=config_entry,
    )

    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get("sensor.kinboard_next_birthday").platform == "other_integration"
    assert registry.async_get("sensor.testfamilie_next_birthday") is not None
