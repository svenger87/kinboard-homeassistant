"""binary_sensor.kinboard_attention_required (RFC-001 section 5.1)."""

from __future__ import annotations

from typing import Any

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


# The server sends at most ten items; the cap is repeated here so a server that
# one day sends more cannot turn this attribute into an unbounded payload.
MAX_ITEMS = 10


class KinboardAttentionRequired(KinboardEntity, BinarySensorEntity):
    """True when at least one attention item is active.

    This is the entity most automations will trigger on, so the state stays a
    plain boolean. The attributes say what is outstanding, because an
    automation that wants to dismiss an item has to be able to name it:
    `count`, the `top` item's title, its key as `top_key`, and `items` — key
    and title only, at most ten.

    `items` is the one attribute that can grow, so it is kept out of the
    recorder: the current list is useful, a history of every list the board
    ever showed is not. The rest are a number and two short strings.
    """

    _unrecorded_attributes = frozenset({"items"})

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, BINARY_SENSOR_ATTENTION_REQUIRED)

    @property
    def is_on(self) -> bool:
        value = (self.coordinator.data or {}).get(BINARY_SENSOR_ATTENTION_REQUIRED)
        if isinstance(value, dict):
            return bool(value.get("state"))
        return bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # A sibling of `attention_required` in the summary, not part of it.
        attention = (self.coordinator.data or {}).get("attention")
        if not isinstance(attention, dict):
            return None
        attributes: dict[str, Any] = {
            k: attention.get(k) for k in ("count", "top", "top_key") if k in attention
        }
        # Only when the server sends it. A Kinboard from before the list existed
        # sends {count, top}; an empty list there would claim nothing is
        # outstanding while `count` says otherwise.
        items = attention.get("items")
        if isinstance(items, list):
            attributes["items"] = [
                {"key": item.get("key"), "title": item.get("title")}
                for item in items[:MAX_ITEMS]
                if isinstance(item, dict)
            ]
        return attributes
