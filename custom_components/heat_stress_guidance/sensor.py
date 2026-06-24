from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_WORKER_NAME, DEFAULT_WORKER_NAME, DOMAIN, WORKLOAD_MODE_STATIC
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
        ForecastPeakWbgtSensor(coordinator, entry),
        ForecastPeakTimeSensor(coordinator, entry),
        ForecastPeakRiskLevelSensor(coordinator, entry),
    ])


def _common_attrs(data: dict) -> dict:
    return {
        "contributing_standards": data.get("contributing_standards", []),
        "advisory_standards": data.get("advisory_standards", []),
        "triggered_by": data.get("triggered_by"),
        "jurisdiction_scope": data.get("jurisdiction_scope"),
        "acclimatization": data.get("acclimatization"),
        "clothing": data.get("clothing"),
        "effective_wbgt_c": data.get("effective_wbgt_c"),
        # True when WBGT is a shade-only local estimate (ha_sensors mode, no globe
        # sensor): it undercounts in-sun radiant load and is not safe to rely on
        # for sun-exposed outdoor work.
        "wbgt_estimate_no_globe": data.get("wbgt_estimate_no_globe", False),
    }


class _HeatStressSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HeatStressCoordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._entry = entry

    @property
    def available(self) -> bool:
        # Unavailable when no standard covers the configured jurisdiction, so a
        # value never reads as authoritative guidance when there is none.
        return super().available and bool((self.coordinator.data or {}).get("available", True))

    @property
    def extra_state_attributes(self) -> dict:
        return _common_attrs(self.coordinator.data or {})

    @property
    def device_info(self):
        # One HA device per worker, named for the worker so entities read
        # sensor.<worker>_wbgt and a fleet stays legible.
        name = self._entry.data.get(CONF_WORKER_NAME) or DEFAULT_WORKER_NAME
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": name,
            "manufacturer": "Heat Guidance Calculator",
            "model": "heat-guidance-calculator.pages.dev",
        }


class WbgtSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "wbgt_c", "WBGT")
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer-water"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("wbgt_c") if self.coordinator.data else None


class RiskLevelSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "risk_level", "Risk Level")
        self._attr_icon = "mdi:alert-circle"

    @property
    def native_value(self):
        return self.coordinator.data.get("risk_level") if self.coordinator.data else None


class WorkMinutesSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "work_minutes", "Work Minutes")
        self._attr_native_unit_of_measurement = "min/hr"
        self._attr_icon = "mdi:briefcase-clock"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("work_minutes") if self.coordinator.data else None


class RestMinutesSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "rest_minutes", "Rest Minutes")
        self._attr_native_unit_of_measurement = "min/hr"
        self._attr_icon = "mdi:sleep"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("rest_minutes") if self.coordinator.data else None


class HydrationMlPerHrSensor(_HeatStressSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hydration_ml_per_hr", "Hydration")
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
        super().__init__(coordinator, entry, "hydration_oz_per_hr", "Hydration Ounces")
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
        super().__init__(coordinator, entry, "hydration_ml_per_break", "Break mL")
        self._attr_native_unit_of_measurement = "mL"
        self._attr_icon = "mdi:cup-water"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("hydration_ml_per_break") if self.coordinator.data else None


class ActiveWorkloadSensor(_HeatStressSensor):
    """Current workload level being sent to the API — static config or MQTT-derived."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "active_workload", "Active Workload")
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


# --- Forecast lookahead -----------------------------------------------------
# Populated only in location / tracked_entity weather modes (the modes that
# fetch an hourly forecast); empty otherwise. "Peak" = the highest-WBGT hour in
# the next 24 hours, with the risk that WBGT implies for the current worker
# profile. These intentionally do not carry the current-condition attributes.


class ForecastPeakWbgtSensor(_HeatStressSensor):
    """Highest forecast WBGT in the next 24 hours."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "forecast_peak_wbgt_c", "Forecast Peak WBGT")
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer-chevron-up"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("forecast_peak_wbgt_c")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "peak_time": data.get("forecast_peak_time"),
            "risk_level_at_peak": data.get("forecast_peak_risk_level"),
            "stop_work_at_peak": data.get("forecast_peak_stop_work"),
            "forecast_window_hours": 24,
        }


class ForecastPeakTimeSensor(_HeatStressSensor):
    """When the next-24-hour WBGT peak occurs."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "forecast_peak_time", "Forecast Peak Time")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-alert-outline"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("forecast_peak_time")

    @property
    def extra_state_attributes(self) -> dict:
        return {"peak_wbgt_c": (self.coordinator.data or {}).get("forecast_peak_wbgt_c")}


class ForecastPeakRiskLevelSensor(_HeatStressSensor):
    """Risk level the forecast peak WBGT would produce for this worker profile."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "forecast_peak_risk_level", "Forecast Peak Risk Level")
        self._attr_icon = "mdi:alert-circle-outline"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("forecast_peak_risk_level")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "stop_work_at_peak": data.get("forecast_peak_stop_work"),
            "peak_wbgt_c": data.get("forecast_peak_wbgt_c"),
            "peak_time": data.get("forecast_peak_time"),
        }
