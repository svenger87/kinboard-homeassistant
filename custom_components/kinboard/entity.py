"""Shared base so every entity attaches to one device per family."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KinboardCoordinator


class KinboardEntity(CoordinatorEntity[KinboardCoordinator]):
    """One device per Kinboard family, so entities group in the UI."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KinboardCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id or coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="Kinboard",
            configuration_url=coordinator.client.base_url,
            sw_version=(coordinator.data or {}).get("version"),
        )

    @property
    def available(self) -> bool:
        return super().available and self._key in (self.coordinator.data or {})
