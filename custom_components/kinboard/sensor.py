"""Sensors Kinboard publishes into Home Assistant (RFC-001 section 5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KinboardConfigEntry
from .const import (
    SENSOR_BIRTHDAYS_UPCOMING,
    SENSOR_MEAL_TOMORROW,
    SENSOR_POCKET_MONEY_PREFIX,
    SENSOR_TASKS_OVERDUE,
    SENSOR_DISPLAY_MODE,
    SENSOR_EVENTS_TODAY,
    SENSOR_MEAL_TODAY,
    SENSOR_NEXT_FAMILY_EVENT,
    SENSOR_SCHOOL_TOMORROW,
    SENSOR_SHOPPING_ITEMS,
    SENSOR_TASKS_DUE,
)
from .entity import KinboardEntity


@dataclass(frozen=True, kw_only=True)
class KinboardSensorDescription(SensorEntityDescription):
    """Adds which sub-keys of the summary become attributes.

    Attributes are listed explicitly rather than passing the whole object
    through: Home Assistant writes every state change to its recorder
    database, so an unbounded attribute set is copied on each update. RFC-001
    section 5.1 requires them to stay small and stable.
    """

    attribute_keys: tuple[str, ...] = ()


SENSORS: tuple[KinboardSensorDescription, ...] = (
    KinboardSensorDescription(
        key=SENSOR_NEXT_FAMILY_EVENT,
        attribute_keys=("title", "start", "person", "location", "minutes_remaining"),
    ),
    KinboardSensorDescription(key=SENSOR_EVENTS_TODAY, attribute_keys=("events",)),
    KinboardSensorDescription(key=SENSOR_SHOPPING_ITEMS),
    KinboardSensorDescription(key=SENSOR_MEAL_TODAY, attribute_keys=("meal", "recipe_id")),
    KinboardSensorDescription(key=SENSOR_TASKS_DUE, attribute_keys=("open", "overdue")),
    KinboardSensorDescription(
        key=SENSOR_SCHOOL_TOMORROW, attribute_keys=("children", "first_lesson")
    ),
    KinboardSensorDescription(
        key=SENSOR_BIRTHDAYS_UPCOMING, attribute_keys=("name", "days_remaining")
    ),
    KinboardSensorDescription(key=SENSOR_DISPLAY_MODE),
    # Its own sensor rather than an attribute of tasks_due, so "something is
    # overdue" is a trigger rather than a template.
    KinboardSensorDescription(key=SENSOR_TASKS_OVERDUE),
    KinboardSensorDescription(key=SENSOR_MEAL_TOMORROW, attribute_keys=("meal", "recipe_id")),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: KinboardConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [KinboardSensor(coordinator, d) for d in SENSORS]

    # One pocket-money sensor per child. Created from the first poll rather
    # than a fixed list, because how many children a family has is not
    # something this integration should assume. A child added later appears
    # after a restart — acceptable for something that changes about once.
    for purse in (coordinator.data or {}).get(SENSOR_POCKET_MONEY_PREFIX) or []:
        if isinstance(purse, dict) and purse.get("person_id"):
            entities.append(KinboardPocketMoney(coordinator, purse["person_id"], purse.get("name") or "?"))

    async_add_entities(entities)


class KinboardPocketMoney(KinboardEntity, SensorEntity):
    """One child's pocket money balance."""

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, person_id: str, name: str) -> None:
        super().__init__(coordinator, f"{SENSOR_POCKET_MONEY_PREFIX}_{person_id}")
        self._person_id = person_id
        # Named after the child rather than translated: a person's name is not
        # something to look up in a translation file.
        self._attr_translation_key = None
        self._attr_name = f"{name} pocket money"

    def _row(self) -> dict[str, Any] | None:
        for purse in (self.coordinator.data or {}).get(SENSOR_POCKET_MONEY_PREFIX) or []:
            if isinstance(purse, dict) and purse.get("person_id") == self._person_id:
                return purse
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._row() is not None

    @property
    def native_value(self) -> Any:
        row = self._row()
        return row.get("balance") if row else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        row = self._row()
        return row.get("currency") if row else None


class KinboardSensor(KinboardEntity, SensorEntity):
    """One value from the family summary."""

    entity_description: KinboardSensorDescription

    def __init__(self, coordinator, description: KinboardSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def _payload(self) -> dict[str, Any]:
        value = (self.coordinator.data or {}).get(self._key)
        return value if isinstance(value, dict) else {}

    @property
    def native_value(self) -> Any:
        value = (self.coordinator.data or {}).get(self._key)
        # The API returns either a scalar (a count, a mode) or an object with
        # a "state" field plus detail. Both shapes are legitimate.
        if isinstance(value, dict):
            return value.get("state")
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.attribute_keys:
            return None
        payload = self._payload
        return {k: payload.get(k) for k in self.entity_description.attribute_keys if k in payload}
