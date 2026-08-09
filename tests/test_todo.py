"""Ticking, adding and removing — the paths a family actually uses daily.

Both bugs found on the first real install were on these paths, and neither was
visible from reading the code: the component and the server each behaved
sensibly on their own and disagreed at the seam.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import async_update_entity

from custom_components.kinboard.api import KinboardConnectionError
from custom_components.kinboard.const import DOMAIN

from .conftest import FAMILY_ID

SHOPPING_ROWS = [
    {"id": "s1", "summary": "Milch", "status": "needs_action", "due": None},
    {"id": "s2", "summary": "Brot", "status": "completed", "due": None},
]


async def _refresh(hass, entity_id: str) -> None:
    """Poll one entity.

    The `homeassistant.update_entity` service is not registered in a bare test
    instance, and setting up that whole integration to poll one entity would be
    a lot of machinery for one call.
    """
    await async_update_entity(hass, entity_id)


def _todo_id(hass, list_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "todo", DOMAIN, f"{DOMAIN}_{FAMILY_ID}_todo_{list_id}"
    )
    assert entity_id
    return entity_id


async def test_items_arrive_from_kinboard(hass, config_entry, mock_client):
    mock_client.async_get_list.return_value = SHOPPING_ROWS
    config_entry.add_to_hass(hass)
    with patch("custom_components.kinboard.KinboardClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(_todo_id(hass, "shopping"))
    # The state is the count of items still to do — "Brot" is done.
    assert state.state == "1"


async def test_ticking_an_item_always_sends_due(hass, setup_integration, mock_client):
    """The uniform patch is the contract, and it is the component's to keep.

    An absent key means "leave it alone", so clearing a date needs an explicit
    null. The server rejecting that null on a list without a due column made
    every tick on the shopping list fail with a 400 — while the tick itself was
    perfectly valid. Fixed server-side; this pins the client half.
    """
    mock_client.async_get_list.return_value = SHOPPING_ROWS
    entity_id = _todo_id(hass, "shopping")
    # The fixture set the entry up against an empty list; load the rows before
    # asking Home Assistant to tick one of them.
    await _refresh(hass, entity_id)

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": entity_id, "item": "Milch", "status": "completed"},
        blocking=True,
    )

    mock_client.async_update_list_item.assert_awaited_once()
    list_id, item_id, patch = mock_client.async_update_list_item.await_args.args
    assert (list_id, item_id) == ("shopping", "s1")
    assert patch["status"] == "completed"
    assert "due" in patch and patch["due"] is None


async def test_adding_an_item_uses_a_fresh_idempotency_key(
    hass, setup_integration, mock_client
):
    """The key absorbs a retry of one call, not a user's intent.

    Two identical items added on purpose are two items, so the key must not be
    derived from the summary.
    """
    entity_id = _todo_id(hass, "shopping")
    for _ in range(2):
        await hass.services.async_call(
            "todo", "add_item", {"entity_id": entity_id, "item": "Milch"}, blocking=True
        )

    keys = [c.kwargs["idempotency_key"] for c in mock_client.async_add_list_item.await_args_list]
    assert len(keys) == 2
    assert keys[0] != keys[1]


async def test_a_failed_write_surfaces_as_an_error(hass, setup_integration, mock_client):
    """Silently swallowing it would leave the user's tick undone with no sign."""
    mock_client.async_add_list_item.side_effect = KinboardConnectionError("down")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "todo",
            "add_item",
            {"entity_id": _todo_id(hass, "shopping"), "item": "Milch"},
            blocking=True,
        )


async def test_a_failed_refresh_keeps_the_previous_items(
    hass, setup_integration, mock_client
):
    """An empty shopping list and an unreachable Kinboard look identical on
    screen, and only one of them means "you have nothing to buy"."""
    entity_id = _todo_id(hass, "shopping")
    mock_client.async_get_list.return_value = SHOPPING_ROWS
    await _refresh(hass, entity_id)
    assert hass.states.get(entity_id).state == "1"

    mock_client.async_get_list.side_effect = KinboardConnectionError("down")
    await _refresh(hass, entity_id)
    assert hass.states.get(entity_id).state == "1"


async def test_a_malformed_row_does_not_take_the_list_down(
    hass, setup_integration, mock_client
):
    """One bad row costs that row, not the whole list."""
    mock_client.async_get_list.return_value = [
        {"id": "s1", "summary": "Milch", "status": "needs_action", "due": None},
        {"id": None, "summary": None},
        {"id": "s3", "summary": "Käse", "status": "needs_action", "due": "not-a-date"},
    ]
    entity_id = _todo_id(hass, "shopping")
    await _refresh(hass, entity_id)
    # Two usable rows survive; the unusable one is simply absent.
    assert hass.states.get(entity_id).state == "2"
