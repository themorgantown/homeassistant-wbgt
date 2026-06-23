from __future__ import annotations

import json
import logging
import math
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACCLIMATIZATION,
    CONF_API_URL,
    CONF_CLOTHING,
    CONF_GLOBE_TEMP_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_LATITUDE,
    CONF_LOCATION_ENTITY,
    CONF_LONGITUDE,
    CONF_MOTION_THRESHOLD_HEAVY,
    CONF_MOTION_THRESHOLD_LIGHT,
    CONF_MOTION_THRESHOLD_MODERATE,
    CONF_MQTT_TOPIC,
    CONF_SHIFT_END,
    CONF_SHIFT_START,
    CONF_TEMP_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WBGT_ENTITY,
    CONF_WEATHER_MODE,
    CONF_WORKLOAD,
    CONF_WORKLOAD_MODE,
    DEFAULT_ACCLIMATIZATION,
    DEFAULT_API_URL,
    DEFAULT_CLOTHING,
    DEFAULT_MOTION_THRESHOLD_HEAVY,
    DEFAULT_MOTION_THRESHOLD_LIGHT,
    DEFAULT_MOTION_THRESHOLD_MODERATE,
    DEFAULT_MQTT_TOPIC,
    DEFAULT_SHIFT_END,
    DEFAULT_SHIFT_START,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WORKLOAD,
    DOMAIN,
    WEATHER_MODE_HA_SENSORS,
    WEATHER_MODE_LOCATION,
    WEATHER_MODE_MANUAL_WBGT,
    WEATHER_MODE_TRACKED_ENTITY,
    WORKLOAD_MODE_MQTT,
    WORKLOAD_MODE_STATIC,
)

_LOGGER = logging.getLogger(__name__)

_FAHRENHEIT_UNITS = {"°F", "F", "degF", "fahrenheit"}

# Cap exponential backoff so a failing API is retried at most once an hour.
MAX_BACKOFF_INTERVAL = timedelta(hours=1)


def _stull_wet_bulb(t_c: float, rh: float) -> float:
    """Stull (2011) wet-bulb approximation. Acceptable for occupational use (±1°C)."""
    return (
        t_c * math.atan(0.151977 * (rh + 8.313659) ** 0.5)
        + math.atan(t_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )


def _estimate_wbgt(t_c: float, rh: float, globe_c: float | None) -> float:
    """Indoor WBGT formula: 0.7·Tnwb + 0.3·Tg (fallback Tg = Tdb when no globe sensor)."""
    nwb = _stull_wet_bulb(t_c, rh)
    tg = globe_c if globe_c is not None else t_c
    return round(0.7 * nwb + 0.3 * tg, 1)


def _derive_risk_level(composite: dict) -> str:
    if composite.get("stopWork"):
        return "critical"
    work = composite.get("workMinutesPerHour")
    if work is None:
        return "unknown"
    if work == 0:
        return "extreme"
    if work <= 15:
        return "high"
    if work <= 30:
        return "moderate"
    if work <= 45:
        return "low"
    return "safe"


def _closest_hour_entry(hourly_wbgt: list, now) -> dict | None:
    if not hourly_wbgt:
        return None
    current_minutes = now.hour * 60 + now.minute
    best = None
    best_diff = None
    for entry in hourly_wbgt:
        time_str = entry.get("time", "")
        try:
            h, m = (int(x) for x in time_str.split(":"))
        except (ValueError, AttributeError):
            continue
        diff = abs(h * 60 + m - current_minutes)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = entry
    return best


def _read_temp_state(hass, entity_id: str, label: str) -> float:
    """Read a temperature entity state in °C, converting from °F if needed."""
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        raise UpdateFailed(f"{label} entity '{entity_id}' is unavailable")
    try:
        val = float(state.state)
    except ValueError as err:
        raise UpdateFailed(f"{label} entity '{entity_id}' has non-numeric state: {state.state}") from err
    uom = state.attributes.get("unit_of_measurement", "")
    if uom in _FAHRENHEIT_UNITS:
        val = (val - 32) * 5 / 9
    return val


def _read_rh_state(hass, entity_id: str) -> float:
    """Read a humidity entity state (always %)."""
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        raise UpdateFailed(f"Humidity entity '{entity_id}' is unavailable")
    try:
        return float(state.state)
    except ValueError as err:
        raise UpdateFailed(f"Humidity entity '{entity_id}' has non-numeric state: {state.state}") from err


def _read_location_state(hass, entity_id: str) -> tuple[float, float]:
    """Read latitude/longitude from a person or device tracker entity."""
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        raise UpdateFailed(f"Location entity '{entity_id}' is unavailable")

    attrs = state.attributes
    try:
        lat = float(attrs["latitude"])
        lon = float(attrs["longitude"])
    except KeyError as err:
        raise UpdateFailed(
            f"Location entity '{entity_id}' does not expose latitude/longitude attributes"
        ) from err
    except (TypeError, ValueError) as err:
        raise UpdateFailed(
            f"Location entity '{entity_id}' has non-numeric latitude/longitude attributes"
        ) from err

    return lat, lon


class HeatStressCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self._config_entry = config_entry
        self._current_workload: str = DEFAULT_WORKLOAD
        self._mqtt_unsubscribe = None
        self._failure_count = 0
        interval_min = self._config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval_min),
        )

    async def async_start_mqtt(self) -> None:
        """Subscribe to open-sensor accelerometer topic. No-op if mode is static."""
        if self._config.get(CONF_WORKLOAD_MODE) != WORKLOAD_MODE_MQTT:
            return
        from homeassistant.components import mqtt as ha_mqtt
        topic = self._config.get(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC)
        self._mqtt_unsubscribe = await ha_mqtt.async_subscribe(
            self.hass, topic, self._handle_mqtt_message, qos=0
        )
        _LOGGER.debug("Subscribed to open-sensor MQTT topic: %s", topic)

    @callback
    def _handle_mqtt_message(self, msg) -> None:
        """Derive workload from open-sensor accelerometer payload."""
        try:
            payload = json.loads(msg.payload)
            x = float(payload["x"])
            y = float(payload["y"])
            z = float(payload["z"])
        except (ValueError, KeyError, TypeError):
            _LOGGER.warning("Invalid open-sensor payload on %s: %r", msg.topic, msg.payload)
            return

        # Subtract gravity baseline to get net motion magnitude
        excess = abs(math.sqrt(x**2 + y**2 + z**2) - 9.81)

        lt = self._config.get(CONF_MOTION_THRESHOLD_LIGHT, DEFAULT_MOTION_THRESHOLD_LIGHT)
        mt = self._config.get(CONF_MOTION_THRESHOLD_MODERATE, DEFAULT_MOTION_THRESHOLD_MODERATE)
        ht = self._config.get(CONF_MOTION_THRESHOLD_HEAVY, DEFAULT_MOTION_THRESHOLD_HEAVY)

        if excess < lt:
            new_workload = "light"
        elif excess < mt:
            new_workload = "moderate"
        elif excess < ht:
            new_workload = "heavy"
        else:
            new_workload = "very_heavy"

        if new_workload != self._current_workload:
            _LOGGER.debug("Workload via MQTT: %s (excess=%.2f m/s²)", new_workload, excess)
            self._current_workload = new_workload
            self.hass.async_create_task(self.async_request_refresh())

    def stop_mqtt(self) -> None:
        """Unsubscribe from MQTT topic. Safe to call even if not subscribed."""
        if self._mqtt_unsubscribe:
            self._mqtt_unsubscribe()
            self._mqtt_unsubscribe = None

    @property
    def _config(self) -> dict:
        """Merge entry data with options so options-flow changes take effect immediately."""
        return {**self._config_entry.data, **self._config_entry.options}

    async def _async_update_data(self) -> dict:
        # Base interval from config (re-read so options-flow changes take effect)
        base_interval = timedelta(
            minutes=self._config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )

        try:
            try:
                wbgt_c = await self._get_wbgt()
            except UpdateFailed:
                raise
            except Exception as err:
                raise UpdateFailed(f"Failed to acquire WBGT: {err}") from err

            if wbgt_c is None:
                raise UpdateFailed("WBGT value unavailable")

            try:
                result = await self._call_compare_api(wbgt_c)
            except UpdateFailed:
                raise
            except Exception as err:
                raise UpdateFailed(f"API call failed: {err}") from err
        except UpdateFailed:
            # Exponential backoff: don't keep polling a failing endpoint at the
            # configured rate. The interval doubles per consecutive failure and
            # is capped, then resets to the configured value on the next success.
            self._failure_count += 1
            backoff = base_interval * 2 ** min(self._failure_count - 1, 10)
            self.update_interval = min(backoff, MAX_BACKOFF_INTERVAL)
            _LOGGER.debug(
                "Update failed (%d consecutive); next poll in %s",
                self._failure_count,
                self.update_interval,
            )
            raise

        # Success — clear any backoff and return to the configured interval.
        self._failure_count = 0
        self.update_interval = base_interval

        composite = result.get("composite") or {}
        derived = result.get("derivedOutputs") or {}
        hydration = derived.get("hydration") or {}
        input_summary = result.get("inputSummary") or {}

        workload_mode = self._config.get(CONF_WORKLOAD_MODE, WORKLOAD_MODE_STATIC)
        active_workload = (
            self._current_workload
            if workload_mode == WORKLOAD_MODE_MQTT
            else self._config.get(CONF_WORKLOAD, DEFAULT_WORKLOAD)
        )

        return {
            "wbgt_c": input_summary.get("rawWbgtC", wbgt_c),
            "effective_wbgt_c": input_summary.get("effectiveWbgtC"),
            "stop_work": composite.get("stopWork", False),
            "work_minutes": composite.get("workMinutesPerHour"),
            "rest_minutes": composite.get("restMinutesPerHour"),
            "risk_level": _derive_risk_level(composite),
            "hydration_ml_per_hr": hydration.get("mlPerHr"),
            "hydration_ml_per_break": hydration.get("mlPerBreak"),
            "hyponatremia_ceiling": hydration.get("hyponatremiaCeiling", False),
            "contributing_standards": composite.get("contributingStandards", []),
            "advisory_standards": composite.get("advisoryStandards", []),
            "clothing": self._config.get(CONF_CLOTHING, DEFAULT_CLOTHING),
            "acclimatization": self._config.get(CONF_ACCLIMATIZATION, DEFAULT_ACCLIMATIZATION),
            "active_workload": active_workload,
            "workload_mode": workload_mode,
        }

    async def _get_wbgt(self) -> float | None:
        mode = self._config.get(CONF_WEATHER_MODE, WEATHER_MODE_LOCATION)

        if mode == WEATHER_MODE_LOCATION:
            lat = self._config.get(CONF_LATITUDE)
            lon = self._config.get(CONF_LONGITUDE)
            return await self._get_location_wbgt(lat, lon)

        if mode == WEATHER_MODE_TRACKED_ENTITY:
            location_entity = self._config.get(CONF_LOCATION_ENTITY)
            lat, lon = _read_location_state(self.hass, location_entity)
            return await self._get_location_wbgt(lat, lon)

        if mode == WEATHER_MODE_HA_SENSORS:
            temp_entity = self._config.get(CONF_TEMP_ENTITY)
            hum_entity = self._config.get(CONF_HUMIDITY_ENTITY)
            globe_entity = self._config.get(CONF_GLOBE_TEMP_ENTITY)

            t_c = _read_temp_state(self.hass, temp_entity, "Temperature")
            rh = _read_rh_state(self.hass, hum_entity)
            globe_c = None
            if globe_entity:
                try:
                    globe_c = _read_temp_state(self.hass, globe_entity, "Globe temperature")
                except UpdateFailed:
                    globe_c = None  # optional — fall back to dry-bulb

            return _estimate_wbgt(t_c, rh, globe_c)

        if mode == WEATHER_MODE_MANUAL_WBGT:
            wbgt_entity = self._config.get(CONF_WBGT_ENTITY)
            return _read_temp_state(self.hass, wbgt_entity, "WBGT")

        raise UpdateFailed(f"Unknown weather mode: {mode}")

    async def _get_location_wbgt(self, lat: float, lon: float) -> float | None:
        api_url = self._config.get(CONF_API_URL, DEFAULT_API_URL).rstrip("/")
        url = f"{api_url}/api/v1/weather/wbgt"
        session = async_get_clientsession(self.hass)
        async with session.get(url, params={"lat": lat, "lon": lon}, timeout=10) as resp:
            resp.raise_for_status()
            data = await resp.json()
        hourly = data.get("hourlyWbgt") or []
        entry = _closest_hour_entry(hourly, dt_util.now())
        if entry is None:
            return None
        return entry.get("valueC")

    async def _call_compare_api(self, wbgt_c: float) -> dict:
        api_url = self._config.get(CONF_API_URL, DEFAULT_API_URL).rstrip("/")
        workload = (
            self._current_workload
            if self._config.get(CONF_WORKLOAD_MODE) == WORKLOAD_MODE_MQTT
            else self._config.get(CONF_WORKLOAD, DEFAULT_WORKLOAD)
        )
        payload = {
            "wbgtC": round(wbgt_c, 2),
            "workload": workload,
            "acclimatization": self._config.get(CONF_ACCLIMATIZATION, DEFAULT_ACCLIMATIZATION),
            "clothing": self._config.get(CONF_CLOTHING, DEFAULT_CLOTHING),
            "shiftStart": self._config.get(CONF_SHIFT_START, DEFAULT_SHIFT_START),
            "shiftEnd": self._config.get(CONF_SHIFT_END, DEFAULT_SHIFT_END),
            "date": dt_util.now().strftime("%Y-%m-%d"),
            "outdoor": True,
        }
        session = async_get_clientsession(self.hass)
        async with session.post(
            f"{api_url}/api/v1/compare",
            json=payload,
            timeout=15,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
