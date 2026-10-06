"""Sensors Kinboard publishes into Home Assistant (RFC-001 section 5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KinboardConfigEntry
from .const import (
    SENSOR_BIRTHDAYS_UPCOMING,
    SENSOR_CREATURE_STAGE_PREFIX,
    SENSOR_POINTS_PREFIX,
    SENSOR_REWARD_REQUESTS,
    SENSOR_MEAL_TOMORROW,
    SENSOR_WASTE_COLLECTION,
    SENSOR_SAVING_GOALS,
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
        # These are the API's own field names, not the words a person would
        # pick. They were once `start` and `person`; the server sends
        # `start_at` and `person_id`, so the sensor carried no start time at
        # all — and nothing failed loudly, because a declared key that is
        # missing from the payload is quietly skipped. test_sensor_attributes
        # now makes that mismatch a test failure.
        attribute_keys=("title", "start_at", "person_id", "location", "minutes_remaining"),
    ),
    KinboardSensorDescription(key=SENSOR_EVENTS_TODAY, attribute_keys=("events",)),
    KinboardSensorDescription(key=SENSOR_SHOPPING_ITEMS),
    KinboardSensorDescription(key=SENSOR_MEAL_TODAY, attribute_keys=("meal", "recipe_id")),
    KinboardSensorDescription(key=SENSOR_TASKS_DUE, attribute_keys=("open", "overdue")),
    KinboardSensorDescription(
        key=SENSOR_SCHOOL_TOMORROW, attribute_keys=("children", "count", "first_lesson")
    ),
    KinboardSensorDescription(
        # `date` matters as much as the countdown: "in 12 days" is the nudge,
        # the date is what you put in a calendar.
        key=SENSOR_BIRTHDAYS_UPCOMING,
        attribute_keys=("name", "days_remaining", "date", "born_on"),
    ),
    KinboardSensorDescription(key=SENSOR_DISPLAY_MODE),
    # Its own sensor rather than an attribute of tasks_due, so "something is
    # overdue" is a trigger rather than a template.
    KinboardSensorDescription(key=SENSOR_TASKS_OVERDUE),
    KinboardSensorDescription(key=SENSOR_MEAL_TOMORROW, attribute_keys=("meal", "recipe_id")),
    KinboardSensorDescription(
        # The state is which bin, because that is what somebody standing in the
        # hall at 22:00 needs to know. `days_until` is what an automation
        # triggers on, and `upcoming` lets one look further than tomorrow.
        key=SENSOR_WASTE_COLLECTION,
        attribute_keys=("type", "date", "days_until", "upcoming"),
    ),
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

    # One per active saving goal. Keyed on person + goal name rather than an
    # id, because the summary carries no goal id — and the pair is what makes
    # it unique anyway: two children may both be saving for a Lego set.
    for goal in (coordinator.data or {}).get(SENSOR_SAVING_GOALS) or []:
        if isinstance(goal, dict) and goal.get("name"):
            entities.append(
                KinboardSavingGoal(coordinator, str(goal.get("person") or "?"), str(goal["name"]))
            )

    # Points, creatures and rewards: one points and one creature sensor per
    # child with a creature, and the family's waiting requests -- created
    # once Kinboard has answered GET /rewards, not before: an older Kinboard
    # has none of it, and then none of these entities exist rather than
    # sitting unavailable forever. Checked again after every poll, so a first
    # read that failed, a Kinboard updated to one with rewards, or a child
    # given a creature later all get their sensors without a reload.
    added: set[str] = set()

    @callback
    def new_reward_entities() -> list[SensorEntity]:
        rewards = coordinator.rewards
        if not isinstance(rewards, dict):
            return []
        new: list[SensorEntity] = []
        for child in rewards.get("children") or []:
            person_id = child.get("person_id")
            if not isinstance(person_id, str) or person_id in added:
                continue
            added.add(person_id)
            name = child.get("name") if isinstance(child.get("name"), str) else "?"
            new.append(KinboardPoints(coordinator, person_id, name))
            new.append(KinboardCreatureStage(coordinator, person_id, name))
        if SENSOR_REWARD_REQUESTS not in added:
            added.add(SENSOR_REWARD_REQUESTS)
            new.append(KinboardRewardRequests(coordinator))
        return new

    async_add_entities([*entities, *new_reward_entities()])

    @callback
    def add_reward_entities_when_they_arrive() -> None:
        if new := new_reward_entities():
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(add_reward_entities_when_they_arrive))


class KinboardPocketMoney(KinboardEntity, SensorEntity):
    """One child's pocket money balance."""

    # Monetary is what a balance is, and it is also what lets the
    # add_pocket_money service offer an entity picker narrowed to exactly these
    # sensors: a selector can filter on integration and device class, but not
    # on anything finer. Saving goals are percentages and stay out of it.
    _attr_device_class = SensorDeviceClass.MONETARY
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


class KinboardSavingGoal(KinboardEntity, SensorEntity):
    """How far one child is towards one goal, as a percentage."""

    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, person: str, goal: str) -> None:
        super().__init__(coordinator, f"{SENSOR_SAVING_GOALS}_{person}_{goal}".lower())
        self._person = person
        self._goal = goal
        self._attr_translation_key = None
        self._attr_name = f"{person}: {goal}"

    def _row(self) -> dict[str, Any] | None:
        for goal in (self.coordinator.data or {}).get(SENSOR_SAVING_GOALS) or []:
            if (
                isinstance(goal, dict)
                and goal.get("name") == self._goal
                and str(goal.get("person")) == self._person
            ):
                return goal
        return None

    @property
    def available(self) -> bool:
        # A goal that has been reached or abandoned stops being reported, and
        # the entity goes unavailable rather than freezing at its last value.
        return self.coordinator.last_update_success and self._row() is not None

    @property
    def native_value(self) -> Any:
        row = self._row()
        return row.get("percent") if row else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        row = self._row()
        if not row:
            return None
        return {k: row.get(k) for k in ("saved", "target", "currency", "person") if k in row}


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


# -- points, creatures and rewards ----------------------------------------

# The server lists every waiting request; this many reach the attribute, so a
# family that let fifty pile up cannot make the state an unbounded payload.
MAX_REWARD_REQUESTS = 20


def _child_row(coordinator, person_id: str) -> dict[str, Any] | None:
    rewards = coordinator.rewards
    if not isinstance(rewards, dict):
        return None
    for child in rewards.get("children") or []:
        if isinstance(child, dict) and child.get("person_id") == person_id:
            return child
    return None


class _KinboardChildRewardsSensor(KinboardEntity, SensorEntity):
    """A sensor about one child, read from GET /rewards."""

    def __init__(self, coordinator, prefix: str, person_id: str) -> None:
        super().__init__(coordinator, f"{prefix}_{person_id}")
        self._person_id = person_id
        # Named after the child rather than translated, like pocket money.
        self._attr_translation_key = None

    def _row(self) -> dict[str, Any] | None:
        return _child_row(self.coordinator, self._person_id)

    @property
    def available(self) -> bool:
        # Gone from the answer (creature switched off, child removed): the
        # entity goes unavailable rather than freezing at its last value.
        return self.coordinator.last_update_success and self._row() is not None


class KinboardPoints(_KinboardChildRewardsSensor):
    """One child's points to spend.

    The state is the balance, what the child can spend; the attributes say
    how it came about and what is held. `available` is what a new request may
    still use, the balance less what is already waiting.
    """

    _attr_native_unit_of_measurement = "points"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:star-circle"

    def __init__(self, coordinator, person_id: str, name: str) -> None:
        super().__init__(coordinator, SENSOR_POINTS_PREFIX, person_id)
        self._attr_name = f"{name} points"

    @property
    def native_value(self) -> Any:
        row = self._row()
        points = row.get("points") if row else None
        return points.get("balance") if isinstance(points, dict) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        row = self._row()
        if not row:
            return None
        points = row.get("points") if isinstance(row.get("points"), dict) else {}
        attributes = {k: points.get(k) for k in ("earned", "owed", "pending", "available", "purchased") if k in points}
        creature = row.get("creature") if isinstance(row.get("creature"), dict) else {}
        nxt = creature.get("next_stage")
        # The next stage's threshold, in points earned -- or, for a creature
        # that grows with saved money, in the account's currency.
        attributes["next_stage_threshold"] = nxt.get("at") if isinstance(nxt, dict) else None
        attributes["next_stage_unit"] = (
            (nxt.get("currency") if nxt.get("unit") == "money" else nxt.get("unit"))
            if isinstance(nxt, dict)
            else None
        )
        return attributes


class KinboardCreatureStage(_KinboardChildRewardsSensor):
    """The stage one child's creature has reached, by its name.

    The state is the stage's name as the screens show it ("Hatchling"),
    because a wall card reads better than "2". The creature's own name, the
    one the child gave it, is not here: Kinboard never sends it.
    """

    _attr_icon = "mdi:egg-easter"

    def __init__(self, coordinator, person_id: str, name: str) -> None:
        super().__init__(coordinator, SENSOR_CREATURE_STAGE_PREFIX, person_id)
        self._attr_name = f"{name} creature"

    def _creature(self) -> dict[str, Any]:
        row = self._row()
        creature = row.get("creature") if row else None
        return creature if isinstance(creature, dict) else {}

    @property
    def native_value(self) -> Any:
        return self._creature().get("stage_name")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        creature = self._creature()
        if not creature:
            return None
        nxt = creature.get("next_stage")
        return {
            "species": creature.get("species"),
            "stage": creature.get("stage"),
            "grows_with": creature.get("grows_with"),
            "next_stage": nxt.get("stage_name") if isinstance(nxt, dict) else None,
        }


class KinboardRewardRequests(KinboardEntity, SensorEntity):
    """How many reward requests wait for a parent, and which.

    The state is the count, which is what an automation triggers on ("above
    0"). `requests` lists them -- child, reward, cost, when -- at most twenty,
    and stays out of the recorder like the attention items: the current list
    is useful, a history of every list is not.
    """

    _attr_icon = "mdi:gift"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"requests"})

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, SENSOR_REWARD_REQUESTS)

    def _pending(self) -> list[dict[str, Any]] | None:
        rewards = self.coordinator.rewards
        if not isinstance(rewards, dict):
            return None
        return [p for p in rewards.get("pending") or [] if isinstance(p, dict)]

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._pending() is not None

    @property
    def native_value(self) -> Any:
        pending = self._pending()
        return len(pending) if pending is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        pending = self._pending()
        if pending is None:
            return None
        return {
            "requests": [
                {
                    "id": p.get("id"),
                    "child": p.get("child_name"),
                    "person_id": p.get("person_id"),
                    "reward": p.get("title"),
                    "icon": p.get("icon"),
                    "cost_points": p.get("cost_points"),
                    "requested_at": p.get("requested_at"),
                }
                for p in pending[:MAX_REWARD_REQUESTS]
            ]
        }
