"""Diagnostics download.

RFC-001 section 10 requires diagnostics that contain no secrets. Redaction is
allow-list shaped on purpose: a deny-list silently leaks whatever field the
API gains next.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import KinboardConfigEntry

SAFE_SUMMARY_KEYS = {
    "version",
    "display_mode",
    "attention_required",
    "shopping_items",
    "tasks_due",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KinboardConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    summary = coordinator.data or {}

    return {
        "entry": {
            # The address is a private hostname, so it is reported only as a
            # shape: whether it is https and whether it is a bare hostname.
            "base_url_scheme": coordinator.client.base_url.split(":", 1)[0],
            "has_token": bool(entry.data.get("token")),
            "unique_id_set": entry.unique_id is not None,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
        },
        # Values only, never names: "3 shopping items" is diagnostic,
        # "Windeln, Bier" is the family's private data.
        "summary_shape": {
            key: type(summary.get(key)).__name__
            for key in sorted(summary)
        },
        "summary_safe_values": {
            key: summary.get(key) for key in sorted(SAFE_SUMMARY_KEYS & set(summary))
        },
        # Entity ids and camera ids only — what "my doorbell does nothing"
        # needs, and nothing that says where the camera streams from.
        "doorbells": coordinator.doorbells.diagnostics()
        if coordinator.doorbells is not None
        else None,
        # Counts only: names, rewards and points are the family's own.
        "rewards": {
            "supported": coordinator.rewards_supported,
            "children": len(coordinator.rewards.get("children") or [])
            if isinstance(coordinator.rewards, dict)
            else None,
            "pending": len(coordinator.rewards.get("pending") or [])
            if isinstance(coordinator.rewards, dict)
            else None,
        },
    }
