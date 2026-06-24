from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HeatStressCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HeatStressCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StopWorkBinarySensor(coordinator, entry)])


class StopWorkBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_icon = "mdi:hand-back-right-off"

    def __init__(self, coordinator: HeatStressCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Stop Work"
        self._attr_unique_id = f"{entry.entry_id}_stop_work"
        self._entry = entry

    @property
    def available(self) -> bool:
        # A SAFETY sensor must not read "clear" when there is simply no guidance:
        # go unavailable when no standard covers the configured jurisdiction.
        return super().available and bool((self.coordinator.data or {}).get("available", True))

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("stop_work", False))

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "contributing_standards": data.get("contributing_standards", []),
            "risk_level": data.get("risk_level"),
            "wbgt_c": data.get("wbgt_c"),
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Heat Stress",
            "manufacturer": "Heat Guidance Calculator",
            "model": "heat-guidance-calculator.pages.dev",
        }
