"""Kinboard's shopping list and tasks as Home Assistant to-do lists.

The sensors report *counts*, which is the right shape for an automation and
the wrong shape for a person: "Shopping items: 3" on a wall display tells you
there is shopping to do and not what it is. A to-do entity shows the items and
lets you tick one, from a phone, a dashboard, or a voice assistant — and the
tick lands in Kinboard.

These do NOT ride on the coordinator's summary poll. The summary is a small
fixed payload fetched every minute for eight sensors; a list is unbounded and
only worth fetching when something is looking at it. So each list polls itself,
and a change made here refreshes immediately rather than waiting out the
interval.
"""

from __future__ import annotations

import uuid
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KinboardConfigEntry
from .api import KinboardError
from .const import DOMAIN, TODO_LISTS
from .entity import KinboardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KinboardConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            KinboardTodoList(
                entry.runtime_data,
                list_id,
                str(spec["name"]),
                bool(spec["supports_due"]),
            )
            for list_id, spec in TODO_LISTS.items()
        ],
        # Fetch before the entity is first shown, so it does not appear empty
        # and then fill in.
        True,
    )


def _to_todo_item(row: dict[str, Any]) -> TodoItem | None:
    """One API item as a Home Assistant TodoItem, or None if unusable."""
    item_id = row.get("id")
    summary = row.get("summary")
    if not item_id or not isinstance(summary, str):
        return None

    return TodoItem(
        uid=str(item_id),
        summary=summary,
        status=(
            TodoItemStatus.COMPLETED
            if row.get("status") == "completed"
            else TodoItemStatus.NEEDS_ACTION
        ),
        # The API sends a plain calendar day or null.
        due=None if not row.get("due") else _parse_date(row["due"]),
    )


def _parse_date(value: str):
    from datetime import date

    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        # A malformed date must not cost the whole item — the summary and its
        # tick box are the point, the date is decoration.
        return None


class KinboardTodoList(KinboardEntity, TodoListEntity):
    """One Kinboard list."""

    def __init__(self, coordinator, list_id: str, name: str, supports_due: bool) -> None:
        super().__init__(coordinator, f"todo_{list_id}")
        self._list_id = list_id

        # Declared per list, not globally. Home Assistant validates a service
        # call against these before the entity is asked to do anything, so a
        # list that claims a due date it cannot store would accept the call and
        # then lose the date — and one that omits a date it CAN store gets the
        # whole call rejected, which is what happened to tasks.
        features = (
            TodoListEntityFeature.CREATE_TODO_ITEM
            | TodoListEntityFeature.UPDATE_TODO_ITEM
            | TodoListEntityFeature.DELETE_TODO_ITEM
        )
        if supports_due:
            features |= TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        self._attr_supported_features = features
        # Named directly rather than through a translation key: these are
        # "Shopping list" and "Tasks", and a translation file for two strings
        # that never vary by family is more indirection than it is worth.
        self._attr_translation_key = None
        self._attr_name = name
        self._attr_todo_items: list[TodoItem] = []

    @property
    def available(self) -> bool:
        # KinboardEntity gates on a key in the summary, which a list does not
        # use. A list is available when the last fetch worked.
        return self.coordinator.last_update_success

    async def async_update(self) -> None:
        """Refresh the items."""
        try:
            rows = await self.coordinator.client.async_get_list(self._list_id)
        except KinboardError:
            # Leave the previous items in place rather than blanking the list.
            # An empty shopping list and an unreachable Kinboard look identical
            # on screen, and only one of them means "you have nothing to buy".
            return
        items = [_to_todo_item(row) for row in rows]
        self._attr_todo_items = [i for i in items if i is not None]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        try:
            await self.coordinator.client.async_add_list_item(
                self._list_id,
                item.summary or "",
                item.due.isoformat() if item.due else None,
                # A fresh key per call: two identical items added on purpose
                # are two items, and the key exists to absorb a retry of ONE
                # call, not to deduplicate a user's intent.
                idempotency_key=f"ha-{uuid.uuid4()}",
            )
        except KinboardError as err:
            raise HomeAssistantError(f"Could not add to {self._attr_name}: {err}") from err
        await self.async_update()
        self.async_write_ha_state()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        patch: dict[str, Any] = {}
        if item.summary is not None:
            patch["summary"] = item.summary
        if item.status is not None:
            patch["status"] = (
                "completed" if item.status == TodoItemStatus.COMPLETED else "needs_action"
            )
        # Sent even when None, because clearing a date is a change a user can
        # make and an absent key would mean "leave it alone". The server
        # accepts a null on a list without a due date as a no-op, so this is
        # uniform across both lists.
        patch["due"] = item.due.isoformat() if item.due else None

        try:
            await self.coordinator.client.async_update_list_item(self._list_id, item.uid or "", patch)
        except KinboardError as err:
            raise HomeAssistantError(f"Could not update the item: {err}") from err
        await self.async_update()
        self.async_write_ha_state()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Remove items.

        Each list keeps its own meaning: a task goes to Kinboard's recycle bin
        and can be restored, a shopping item is gone. That is what deleting
        means in Kinboard for each list, and Home Assistant should not invent
        a third behaviour.
        """
        try:
            for uid in uids:
                await self.coordinator.client.async_delete_list_item(self._list_id, uid)
        except KinboardError as err:
            raise HomeAssistantError(f"Could not remove the item: {err}") from err
        await self.async_update()
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.coordinator.entry.unique_id}_todo_{self._list_id}"
