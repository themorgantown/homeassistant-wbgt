from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, WORKLOAD_MODE_STATIC
from .coordinator import HeatStressCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HeatStressCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WbgtSensor(coordinator, entry),
        RiskLevelSensor(coordinator, entry),
        WorkMinutesSensor(coordinator, entry),
        RestMinutesSensor(coordinator, entry),
        HydrationMlPerHrSensor(coordinator, entry),
        HydrationOzPerHrSensor(coordinator, entry),
        BreakMlSensor(coordinator, entry),
        ActiveWorkloadSensor(coordinator, entry),
    ])


def _common_attrs(data: dict) -> dict:
    return {
        "contributing_standards": data.get("contributing_standards", []),
        "advisory_standards": data.get("advisory_standards", []),
        "acclimatization": data.get("acclimatization"),
        "clothing": data.get("clothing"),
        "effective_wbgt_c": data.get("effective_wbgt_c"),
    }


class _HeatStressSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: HeatStressCoordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._entry = entry

    @property
    def extra_state_attributes(self) -> dict:
        return _common_attrs(self.coordinator.data or {})

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Heat Stress Guidance",
            "manufacturer": "La Isla Network / Heat Guidance Calculator",
            "model": "heat-guidance-calculator.pages.dev",
        }


class WbgtSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "wbgt_c", "Heat Stress WBGT")
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer-water"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("wbgt_c") if self.coordinator.data else None


class RiskLevelSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "risk_level", "Heat Stress Risk Level")
        self._attr_icon = "mdi:alert-circle"

    @property
    def native_value(self):
        return self.coordinator.data.get("risk_level") if self.coordinator.data else None


class WorkMinutesSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "work_minutes", "Heat Stress Work Minutes")
        self._attr_native_unit_of_measurement = "min/hr"
        self._attr_icon = "mdi:briefcase-clock"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("work_minutes") if self.coordinator.data else None


class RestMinutesSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "rest_minutes", "Heat Stress Rest Minutes")
        self._attr_native_unit_of_measurement = "min/hr"
        self._attr_icon = "mdi:sleep"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("rest_minutes") if self.coordinator.data else None


class HydrationMlPerHrSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hydration_ml_per_hr", "Heat Stress Hydration")
        self._attr_native_unit_of_measurement = "mL/h"
        self._attr_icon = "mdi:water"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("hydration_ml_per_hr") if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict:
        base = _common_attrs(self.coordinator.data or {})
        base["hyponatremia_ceiling_warning"] = (self.coordinator.data or {}).get("hyponatremia_ceiling", False)
        return base


class HydrationOzPerHrSensor(_HeatStressSensor):
    _ML_PER_OZ = 29.5735

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hydration_oz_per_hr", "Heat Stress Hydration Ounces")
        self._attr_native_unit_of_measurement = "fl oz/h"
        self._attr_icon = "mdi:cup-water"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        ml = (self.coordinator.data or {}).get("hydration_ml_per_hr")
        if ml is None:
            return None
        return round(ml / self._ML_PER_OZ, 1)

    @property
    def extra_state_attributes(self) -> dict:
        base = _common_attrs(self.coordinator.data or {})
        base["hyponatremia_ceiling_warning"] = (self.coordinator.data or {}).get("hyponatremia_ceiling", False)
        return base


class BreakMlSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hydration_ml_per_break", "Heat Stress Break mL")
        self._attr_native_unit_of_measurement = "mL"
        self._attr_icon = "mdi:cup-water"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("hydration_ml_per_break") if self.coordinator.data else None


class ActiveWorkloadSensor(_HeatStressSensor):
    """Current workload level being sent to the API — static config or MQTT-derived."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "active_workload", "Heat Stress Active Workload")
        self._attr_icon = "mdi:run"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("active_workload")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        base = _common_attrs(data)
        base["workload_mode"] = data.get("workload_mode", WORKLOAD_MODE_STATIC)
        return base
