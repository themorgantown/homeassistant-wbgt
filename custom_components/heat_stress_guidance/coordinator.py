from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util, slugify

from .const import (
    ALERT_RISK_LEVELS,
    CONF_ACCLIMATIZATION,
    CONF_ALERT_DEVICE,
    CONF_API_URL,
    CONF_CLOTHING,
    CONF_COUNTRY,
    CONF_GLOBE_TEMP_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_LATITUDE,
    CONF_LOCATION_ENTITY,
    CONF_LONGITUDE,
    CONF_STATE,
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
    GLOBAL_JURISDICTIONS,
    US_STATE_JURISDICTIONS,
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

# How far ahead the forecast-peak sensors look.
FORECAST_HORIZON = timedelta(hours=24)


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


def _standard_in_scope(result: dict, country: str, state: str) -> bool:
    """Is an API standard relevant to the user's country/state?

    Global standards always apply. Otherwise the standard's country must match;
    for the US, state-specific standards apply only in their own state while
    federal standards apply everywhere. A blank country scopes to global-only.
    """
    jurisdiction = result.get("jurisdiction")
    if jurisdiction in GLOBAL_JURISDICTIONS:
        return True
    country_code = result.get("countryCode")
    if not country_code:
        return False  # region-only (e.g. european_union) or untagged
    if not country or country_code != country:
        return False
    if country == "US" and jurisdiction in US_STATE_JURISDICTIONS:
        return US_STATE_JURISDICTIONS[jurisdiction] == state
    return True


def _standard_label(result: dict) -> str:
    return result.get("displayName") or result.get("standardId") or "unknown"


def _scope_composite(results: list, country: str, state: str) -> dict:
    """Recompute the composite over only the standards in the user's scope.

    Mirrors the API's "most protective wins" rule, but restricted to relevant
    jurisdictions: any in-scope standard requiring stop-work wins; otherwise the
    schedule with the fewest work minutes per hour is the binding one.
    """
    in_scope = [r for r in results if _standard_in_scope(r, country, state)]
    scoped = [r for r in in_scope if r.get("applicable")]
    stop = [r for r in scoped if (r.get("recommendation") or {}).get("stopWork")]
    advisory = [_standard_label(r) for r in scoped]

    # No standard covers the user's jurisdiction *at all* — distinct from "rules
    # cover it but none triggered at this WBGT", which is a legitimate safe
    # state. Without coverage we cannot give jurisdiction-correct guidance, so
    # flag it; the coordinator marks the entities unavailable rather than letting
    # a SAFETY binary sensor read clear off an empty set.
    scope_empty = not in_scope

    if stop:
        composite = {
            "stopWork": True,
            "workMinutesPerHour": None,
            "restMinutesPerHour": None,
            "contributingStandards": [_standard_label(r) for r in stop],
            "advisoryStandards": advisory,
            "triggeredBy": _standard_label(stop[0]),
        }
    else:
        scheduled = [
            r for r in scoped
            if (r.get("recommendation") or {}).get("workMinutesPerHour") is not None
        ]
        if scheduled:
            best = min(scheduled, key=lambda r: r["recommendation"]["workMinutesPerHour"])
            rec = best["recommendation"]
            composite = {
                "stopWork": False,
                "workMinutesPerHour": rec.get("workMinutesPerHour"),
                "restMinutesPerHour": rec.get("restMinutesPerHour"),
                "contributingStandards": [_standard_label(best)],
                "advisoryStandards": advisory,
                "triggeredBy": _standard_label(best),
            }
        else:
            composite = {
                "stopWork": False,
                "workMinutesPerHour": None,
                "restMinutesPerHour": None,
                "contributingStandards": [],
                "advisoryStandards": advisory,
                "triggeredBy": None,
            }

    composite["scopeEmpty"] = scope_empty
    return composite


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


def _is_restricted(data: dict) -> bool:
    """A heat restriction is in force: work must stop, or risk is high or above."""
    return bool(data.get("stop_work")) or data.get("risk_level") in ALERT_RISK_LEVELS


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


def _forecast_peak(hourly_wbgt: list, tzname: str | None, now_utc) -> dict | None:
    """Highest-WBGT hour within the next 24 hours of the forecast.

    Each forecast entry carries its own ``date`` and ``time`` in the location's
    timezone, so peaks are anchored to real instants rather than a bare hour.
    Returns ``{"valueC": float, "when": datetime}`` (timezone-aware) or ``None``
    when the forecast has no upcoming hours (e.g. non-forecast weather modes).
    """
    if not hourly_wbgt:
        return None
    tz = (dt_util.get_time_zone(tzname) if tzname else None) or dt_util.DEFAULT_TIME_ZONE
    horizon = now_utc + FORECAST_HORIZON
    best = None
    for entry in hourly_wbgt:
        try:
            naive = datetime.strptime(f"{entry['date']} {entry['time']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError, TypeError):
            continue
        when = naive.replace(tzinfo=tz)
        if when < now_utc or when > horizon:
            continue
        value = entry.get("valueC")
        if value is None:
            continue
        if best is None or value > best["valueC"]:
            best = {"valueC": value, "when": when}
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
        self._forecast_hourly: list = []
        self._forecast_tz: str | None = None
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

    def _composite_for(self, result: dict, country: str, state: str) -> dict:
        """Scope an API response to the user's jurisdiction (fallback: API composite)."""
        results = result.get("results") or []
        return _scope_composite(results, country, state) if results else (result.get("composite") or {})

    @property
    def _alert_tag(self) -> str:
        return f"heat_stress_{self._config_entry.entry_id}"

    def _notify_service_for(self, device_id: str) -> str | None:
        """Resolve a mobile_app device to its notify service, or None if missing."""
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None or not device.name:
            return None
        # mobile_app derives the service from the device's original (integration)
        # name, which a user rename in HA does not change — so use .name, not
        # .name_by_user.
        service = "mobile_app_" + slugify(device.name)
        if not self.hass.services.has_service("notify", service):
            _LOGGER.warning(
                "Heat alert device has no notify.%s service (is the HA app installed?)",
                service,
            )
            return None
        return service

    async def _handle_heat_alert(self, prev: dict, new: dict) -> None:
        """Push a rich alert on the rising edge into a restriction; clear on recovery."""
        device_id = self._config.get(CONF_ALERT_DEVICE)
        if not device_id:
            return

        prev_restricted = _is_restricted(prev)
        new_restricted = _is_restricted(new) and new.get("available", True)
        escalated_to_stop = bool(new.get("stop_work")) and not bool(prev.get("stop_work"))

        if new_restricted and (not prev_restricted or escalated_to_stop):
            await self._async_send_alert(device_id, new)
        elif prev_restricted and not new_restricted:
            await self._async_clear_alert(device_id)

    async def _async_send_alert(self, device_id: str, new: dict) -> None:
        service = self._notify_service_for(device_id)
        if service is None:
            return

        wbgt = new.get("wbgt_c")
        if new.get("stop_work"):
            title = "⛔ Heat alert: STOP WORK"
            message = (
                f"WBGT {wbgt}°C — all work must stop now. "
                f"Driver: {new.get('triggered_by') or 'applicable standard'}."
            )
            color = "#b71c1c"
        else:
            title = f"⚠️ Heat alert: {new.get('risk_level')} risk"
            message = (
                f"WBGT {wbgt}°C — work {new.get('work_minutes')}/"
                f"rest {new.get('rest_minutes')} min per hour, "
                f"drink {new.get('hydration_ml_per_hr')} mL/hr."
            )
            color = "#e65100"

        payload = {
            "title": title,
            "message": message,
            "data": {
                "tag": self._alert_tag,        # update the same notification in place
                "group": "heat_stress",
                "color": color,                # Android accent
                "importance": "high",          # Android channel importance
                "channel": "Heat alerts",
                "ttl": 0,
                "priority": "high",
                "notification_icon": "mdi:thermometer-alert",
                "push": {"interruption-level": "time-sensitive"},  # iOS
                "actions": [
                    {"action": "URI", "title": "Open dashboard", "uri": "/lovelace/heat-stress"},
                ],
            },
        }
        await self.hass.services.async_call("notify", service, payload, blocking=False)

    async def _async_clear_alert(self, device_id: str) -> None:
        service = self._notify_service_for(device_id)
        if service is None:
            return
        await self.hass.services.async_call(
            "notify",
            service,
            {"message": "clear_notification", "data": {"tag": self._alert_tag}},
            blocking=False,
        )

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

        # Scope the guidance to the user's jurisdiction. The API's own `composite`
        # is computed across every standard worldwide; we instead recompute it
        # over only the standards relevant to the configured country/state. Fall
        # back to the API composite if the response carries no per-standard list.
        country = (self._config.get(CONF_COUNTRY) or self.hass.config.country or "").upper()
        state = (self._config.get(CONF_STATE) or "").upper()
        composite = self._composite_for(result, country, state)

        # Forecast lookahead: peak WBGT over the next 24 hours, plus the risk that
        # WBGT would produce for this worker profile. The risk needs one extra
        # /compare call (skipped when the peak is essentially the current value);
        # a failure here must not sink the otherwise-successful update.
        peak = _forecast_peak(self._forecast_hourly, self._forecast_tz, dt_util.utcnow())
        forecast_peak_wbgt = forecast_peak_time = None
        forecast_peak_risk = forecast_peak_stop = None
        if peak is not None:
            forecast_peak_wbgt = round(peak["valueC"], 1)
            forecast_peak_time = peak["when"]
            try:
                if abs(peak["valueC"] - wbgt_c) > 0.1:
                    peak_composite = self._composite_for(
                        await self._call_compare_api(peak["valueC"]), country, state
                    )
                else:
                    peak_composite = composite
                forecast_peak_risk = _derive_risk_level(peak_composite)
                forecast_peak_stop = peak_composite.get("stopWork", False)
            except Exception as err:  # noqa: BLE001 - forecast risk is best-effort
                _LOGGER.debug("Forecast peak risk lookup failed: %s", err)

        derived = result.get("derivedOutputs") or {}
        hydration = derived.get("hydration") or {}
        input_summary = result.get("inputSummary") or {}

        workload_mode = self._config.get(CONF_WORKLOAD_MODE, WORKLOAD_MODE_STATIC)
        active_workload = (
            self._current_workload
            if workload_mode == WORKLOAD_MODE_MQTT
            else self._config.get(CONF_WORKLOAD, DEFAULT_WORKLOAD)
        )

        data = {
            # False when no standard covers the configured jurisdiction at all,
            # so the entities go unavailable instead of reporting a misleading
            # "no stop-work". See _scope_composite.
            "available": not composite.get("scopeEmpty", False),
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
            "triggered_by": composite.get("triggeredBy"),
            "jurisdiction_scope": f"{country}/{state}" if state else (country or "global"),
            "clothing": self._config.get(CONF_CLOTHING, DEFAULT_CLOTHING),
            "acclimatization": self._config.get(CONF_ACCLIMATIZATION, DEFAULT_ACCLIMATIZATION),
            "active_workload": active_workload,
            "workload_mode": workload_mode,
            "forecast_peak_wbgt_c": forecast_peak_wbgt,
            "forecast_peak_time": forecast_peak_time,
            "forecast_peak_risk_level": forecast_peak_risk,
            "forecast_peak_stop_work": forecast_peak_stop,
        }

        # Fire a rich push alert on the rising edge into a restriction (and clear
        # it when conditions normalize). Best-effort: never let it break an update.
        try:
            await self._handle_heat_alert(self.data or {}, data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Heat alert dispatch failed: %s", err)

        return data

    async def _get_wbgt(self) -> float | None:
        # Only location-based modes carry an hourly forecast; clear any stale data
        # so the forecast-peak sensors go empty in sensor/manual modes.
        self._forecast_hourly = []
        self._forecast_tz = None
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
        # Stash the forecast for the peak-lookahead sensors in _async_update_data.
        self._forecast_hourly = hourly
        self._forecast_tz = data.get("timezone")
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
