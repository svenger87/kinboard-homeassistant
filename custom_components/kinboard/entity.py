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
            # "Kinboard", not the family name. With `has_entity_name` the
            # device name becomes the entity_id prefix, so a family called
            # Weber produced `sensor.weber_next_birthday` — while RFC-001
            # and the README publish `sensor.kinboard_next_birthday` as a
            # frozen contract. Naming the device after the family made every
            # example automation unusable by anybody but that family.
            #
            # Two families in one household collide into `_2`, which is Home
            # Assistant's normal handling and still beats nobody being able to
            # copy an example.
            name="Kinboard",
            # The family name stays visible here instead.
            model=coordinator.entry.title,
            manufacturer="Kinboard",
            configuration_url=coordinator.client.base_url,
            sw_version=(coordinator.data or {}).get("version"),
        )

    @property
    def available(self) -> bool:
        return super().available and self._key in (self.coordinator.data or {})
