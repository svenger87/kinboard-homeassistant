"""binary_sensor.kinboard_attention_required (RFC-001 section 5.1)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KinboardConfigEntry
from .const import BINARY_SENSOR_ATTENTION_REQUIRED
from .entity import KinboardEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: KinboardConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([KinboardAttentionRequired(entry.runtime_data)])


class KinboardAttentionRequired(KinboardEntity, BinarySensorEntity):
    """True when at least one attention item is active.

    This is the entity most automations will trigger on, so it stays a plain
    boolean: the detail belongs in the Heute-Motor's own view, not in an
    attribute payload HA has to record on every change.
    """

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, BINARY_SENSOR_ATTENTION_REQUIRED)

    @property
    def is_on(self) -> bool:
        value = (self.coordinator.data or {}).get(BINARY_SENSOR_ATTENTION_REQUIRED)
        if isinstance(value, dict):
            return bool(value.get("state"))
        return bool(value)
