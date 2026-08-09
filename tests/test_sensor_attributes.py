"""Declared attributes must exist in the payload.

This is the quiet failure mode of the whole design: `extra_state_attributes`
skips any declared key the payload does not carry, so a name that never
matches produces a sensor missing that attribute and no error anywhere. It
cost `sensor.kinboard_next_family_event` its start time, undetected, because
the sensor still worked and still had four other attributes.
"""

from __future__ import annotations

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.kinboard.const import DOMAIN
from custom_components.kinboard.sensor import SENSORS

from .conftest import FAMILY_ID, SUMMARY

WITH_ATTRIBUTES = [d for d in SENSORS if d.attribute_keys]


@pytest.mark.parametrize("description", WITH_ATTRIBUTES, ids=lambda d: d.key)
def test_every_declared_attribute_is_a_real_field(description):
    """Checked against the payload fixture, which mirrors the live endpoint."""
    payload = SUMMARY[description.key]
    assert isinstance(payload, dict), f"{description.key} declares attributes but is a scalar"
    missing = [k for k in description.attribute_keys if k not in payload]
    assert not missing, (
        f"{description.key} declares {missing}, which the API does not send — "
        "the attribute would be silently absent"
    )


@pytest.mark.parametrize("description", WITH_ATTRIBUTES, ids=lambda d: d.key)
async def test_declared_attributes_reach_the_state(hass, setup_integration, description):
    """And survive all the way onto the entity, not just into the fixture."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{FAMILY_ID}_{description.key}")
    attributes = hass.states.get(entity_id).attributes
    for key in description.attribute_keys:
        assert key in attributes, f"{entity_id} lost {key}"


async def test_the_next_event_carries_its_start_time(hass, setup_integration):
    """The specific loss, pinned by name.

    "In 90 minutes" answers a different question from "at 15:00", and an
    automation that wants to announce the time had nothing to read.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{FAMILY_ID}_next_family_event")
    assert hass.states.get(entity_id).attributes["start_at"] == "2026-08-09T15:00:00+02:00"
