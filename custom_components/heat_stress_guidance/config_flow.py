from __future__ import annotations

import re

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .qr import (
    CloudhookUnavailable,
    DEFAULT_DEVICE_ID,
    DEFAULT_TRACKER_ID,
    DEFAULT_USER,
    OwnTracksNotConfigured,
    async_build_owntracks_qr_payload,
)
from .const import (
    ACCLIMATIZATION_OPTIONS,
    CLOTHING_LABELS,
    CLOTHING_OPTIONS,
    CONF_ACCLIMATIZATION,
    CONF_API_URL,
    CONF_CLOTHING,
    CONF_COUNTRY,
    CONF_GLOBE_TEMP_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_LATITUDE,
    CONF_LOCATION_ENTITY,
    CONF_LONGITUDE,
    CONF_MQTT_TOPIC,
    CONF_SHIFT_END,
    CONF_SHIFT_START,
    CONF_STATE,
    CONF_TEMP_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WBGT_ENTITY,
    CONF_WEATHER_MODE,
    CONF_WORKLOAD,
    CONF_WORKLOAD_MODE,
    DEFAULT_ACCLIMATIZATION,
    DEFAULT_API_URL,
    DEFAULT_CLOTHING,
    DEFAULT_MQTT_TOPIC,
    DEFAULT_SHIFT_END,
    DEFAULT_SHIFT_START,
    DEFAULT_STATE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WORKLOAD,
    DEFAULT_WORKLOAD_MODE,
    DOMAIN,
    SUPPORTED_COUNTRIES,
    US_STATES,
    WEATHER_MODE_HA_SENSORS,
    WEATHER_MODE_LOCATION,
    WEATHER_MODE_MANUAL_WBGT,
    WEATHER_MODE_TRACKED_ENTITY,
    WORKLOAD_MODE_MQTT,
    WORKLOAD_MODE_STATIC,
    WORKLOAD_OPTIONS,
)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_time(value: str) -> str:
    if not TIME_RE.match(value):
        raise vol.Invalid("Expected HH:MM format (e.g. 07:00)")
    return value


async def _test_api_connection(api_url: str) -> bool:
    url = api_url.rstrip("/") + "/health"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
    except Exception:
        return False


class HeatStressGuidanceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._data: dict = {}

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            api_url = user_input[CONF_API_URL].rstrip("/")
            ok = await _test_api_connection(api_url)
            if not ok:
                errors[CONF_API_URL] = "cannot_connect"
            else:
                await self.async_set_unique_id(api_url)
                self._abort_if_unique_id_configured()
                self._data.update(user_input)
                self._data[CONF_API_URL] = api_url
                return await self.async_step_weather()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_URL, default=DEFAULT_API_URL): str,
                vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(int, vol.Range(min=1, max=1440)),
            }),
            errors=errors,
        )

    async def async_step_weather(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            mode = user_input[CONF_WEATHER_MODE]
            if mode == WEATHER_MODE_LOCATION:
                if user_input.get(CONF_LATITUDE) is None or user_input.get(CONF_LONGITUDE) is None:
                    errors["base"] = "missing_location"
                else:
                    self._data.update(user_input)
                    return await self.async_step_worker()
            elif mode == WEATHER_MODE_TRACKED_ENTITY:
                if not user_input.get(CONF_LOCATION_ENTITY, "").strip():
                    errors["base"] = "missing_location_entity"
                else:
                    self._data.update(user_input)
                    return await self.async_step_worker()
            elif mode == WEATHER_MODE_HA_SENSORS:
                if not user_input.get(CONF_TEMP_ENTITY) or not user_input.get(CONF_HUMIDITY_ENTITY):
                    errors["base"] = "missing_sensors"
                else:
                    self._data.update(user_input)
                    return await self.async_step_worker()
            elif mode == WEATHER_MODE_MANUAL_WBGT:
                if not user_input.get(CONF_WBGT_ENTITY):
                    errors["base"] = "missing_wbgt_entity"
                else:
                    self._data.update(user_input)
                    return await self.async_step_worker()

        ha_lat = self.hass.config.latitude
        ha_lon = self.hass.config.longitude

        return self.async_show_form(
            step_id="weather",
            data_schema=vol.Schema({
                vol.Required(CONF_WEATHER_MODE, default=WEATHER_MODE_LOCATION): vol.In([
                    WEATHER_MODE_LOCATION,
                    WEATHER_MODE_TRACKED_ENTITY,
                    WEATHER_MODE_HA_SENSORS,
                    WEATHER_MODE_MANUAL_WBGT,
                ]),
                vol.Optional(CONF_LATITUDE, default=ha_lat): vol.Coerce(float),
                vol.Optional(CONF_LONGITUDE, default=ha_lon): vol.Coerce(float),
                vol.Optional(CONF_LOCATION_ENTITY, default=""): str,
                vol.Optional(CONF_TEMP_ENTITY, default=""): str,
                vol.Optional(CONF_HUMIDITY_ENTITY, default=""): str,
                vol.Optional(CONF_GLOBE_TEMP_ENTITY, default=""): str,
                vol.Optional(CONF_WBGT_ENTITY, default=""): str,
            }),
            errors=errors,
        )

    async def async_step_worker(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            workload_mode = user_input.get(CONF_WORKLOAD_MODE, WORKLOAD_MODE_STATIC)
            if workload_mode == WORKLOAD_MODE_MQTT:
                if not user_input.get(CONF_MQTT_TOPIC, "").strip():
                    errors["base"] = "missing_mqtt_topic"
                elif "mqtt" not in self.hass.config.components:
                    errors["base"] = "mqtt_not_available"
            if not errors:
                try:
                    _validate_time(user_input[CONF_SHIFT_START])
                    _validate_time(user_input[CONF_SHIFT_END])
                except vol.Invalid:
                    errors["base"] = "invalid_time"
            if not errors:
                self._data.update(user_input)
                mode_label = "MQTT" if workload_mode == WORKLOAD_MODE_MQTT else self._data.get(CONF_WORKLOAD, DEFAULT_WORKLOAD)
                return self.async_create_entry(title=f"Heat Stress Guidance ({mode_label})", data=self._data)

        default_country = (self.hass.config.country or "").upper()
        if default_country not in SUPPORTED_COUNTRIES:
            default_country = ""

        return self.async_show_form(
            step_id="worker",
            data_schema=vol.Schema({
                vol.Required(CONF_WORKLOAD_MODE, default=DEFAULT_WORKLOAD_MODE): vol.In([WORKLOAD_MODE_STATIC, WORKLOAD_MODE_MQTT]),
                vol.Required(CONF_WORKLOAD, default=DEFAULT_WORKLOAD): vol.In(WORKLOAD_OPTIONS),
                vol.Optional(CONF_MQTT_TOPIC, default=DEFAULT_MQTT_TOPIC): str,
                vol.Required(CONF_ACCLIMATIZATION, default=DEFAULT_ACCLIMATIZATION): vol.In(ACCLIMATIZATION_OPTIONS),
                vol.Required(CONF_SHIFT_START, default=DEFAULT_SHIFT_START): str,
                vol.Required(CONF_SHIFT_END, default=DEFAULT_SHIFT_END): str,
                vol.Required(CONF_CLOTHING, default=DEFAULT_CLOTHING): vol.In(CLOTHING_OPTIONS),
                vol.Required(CONF_COUNTRY, default=default_country): vol.In(SUPPORTED_COUNTRIES),
                vol.Optional(CONF_STATE, default=DEFAULT_STATE): vol.In(US_STATES),
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HeatStressOptionsFlow(config_entry)


class HeatStressOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["configure", "show_qr"],
        )

    async def async_step_show_qr(self, user_input=None) -> FlowResult:
        user = DEFAULT_USER
        device_id = DEFAULT_DEVICE_ID
        tracker_id = DEFAULT_TRACKER_ID
        if user_input is not None:
            user = (user_input.get("user") or "").strip() or DEFAULT_USER
            device_id = (user_input.get("deviceid") or "").strip() or DEFAULT_DEVICE_ID
            tracker_id = (user_input.get("trackerid") or "").strip() or DEFAULT_TRACKER_ID

        try:
            payload = await async_build_owntracks_qr_payload(
                self.hass, user=user, device_id=device_id, tracker_id=tracker_id
            )
        except OwnTracksNotConfigured:
            return self.async_abort(reason="owntracks_not_configured")
        except CloudhookUnavailable:
            return self.async_abort(reason="cloud_unavailable")

        # Editing an identity field and resubmitting regenerates the QR; there is
        # nothing to persist, so the step just re-renders. The user closes the
        # dialog when done.
        return self.async_show_form(
            step_id="show_qr",
            data_schema=vol.Schema({
                vol.Optional("user", default=user): str,
                vol.Optional("deviceid", default=device_id): str,
                vol.Optional("trackerid", default=tracker_id): str,
                vol.Optional("qr"): selector.QrCodeSelector(
                    config=selector.QrCodeSelectorConfig(
                        data=payload,
                        scale=6,
                        error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                    )
                ),
            }),
        )

    async def async_step_configure(self, user_input=None) -> FlowResult:
        errors = {}
        current = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            api_url = user_input[CONF_API_URL].rstrip("/")
            ok = await _test_api_connection(api_url)
            if not ok:
                errors[CONF_API_URL] = "cannot_connect"

            mode = user_input[CONF_WEATHER_MODE]
            if mode == WEATHER_MODE_LOCATION:
                if user_input.get(CONF_LATITUDE) is None or user_input.get(CONF_LONGITUDE) is None:
                    errors["base"] = "missing_location"
            elif mode == WEATHER_MODE_TRACKED_ENTITY:
                if not user_input.get(CONF_LOCATION_ENTITY, "").strip():
                    errors["base"] = "missing_location_entity"
            elif mode == WEATHER_MODE_HA_SENSORS:
                if not user_input.get(CONF_TEMP_ENTITY) or not user_input.get(CONF_HUMIDITY_ENTITY):
                    errors["base"] = "missing_sensors"
            elif mode == WEATHER_MODE_MANUAL_WBGT:
                if not user_input.get(CONF_WBGT_ENTITY):
                    errors["base"] = "missing_wbgt_entity"

            workload_mode = user_input.get(CONF_WORKLOAD_MODE, WORKLOAD_MODE_STATIC)
            if workload_mode == WORKLOAD_MODE_MQTT:
                if not user_input.get(CONF_MQTT_TOPIC, "").strip():
                    errors["base"] = "missing_mqtt_topic"
                elif "mqtt" not in self.hass.config.components:
                    errors["base"] = "mqtt_not_available"
            if not errors:
                try:
                    _validate_time(user_input[CONF_SHIFT_START])
                    _validate_time(user_input[CONF_SHIFT_END])
                except vol.Invalid:
                    errors["base"] = "invalid_time"
            if not errors:
                user_input[CONF_API_URL] = api_url
                return self.async_create_entry(title="", data={**current, **user_input})

        ha_lat = self.hass.config.latitude
        ha_lon = self.hass.config.longitude

        return self.async_show_form(
            step_id="configure",
            data_schema=vol.Schema({
                vol.Required(CONF_API_URL, default=current.get(CONF_API_URL, DEFAULT_API_URL)): str,
                vol.Required(CONF_WEATHER_MODE, default=current.get(CONF_WEATHER_MODE, WEATHER_MODE_LOCATION)): vol.In([
                    WEATHER_MODE_LOCATION,
                    WEATHER_MODE_TRACKED_ENTITY,
                    WEATHER_MODE_HA_SENSORS,
                    WEATHER_MODE_MANUAL_WBGT,
                ]),
                vol.Optional(CONF_LATITUDE, default=current.get(CONF_LATITUDE, ha_lat)): vol.Coerce(float),
                vol.Optional(CONF_LONGITUDE, default=current.get(CONF_LONGITUDE, ha_lon)): vol.Coerce(float),
                vol.Optional(CONF_LOCATION_ENTITY, default=current.get(CONF_LOCATION_ENTITY, "")): str,
                vol.Optional(CONF_TEMP_ENTITY, default=current.get(CONF_TEMP_ENTITY, "")): str,
                vol.Optional(CONF_HUMIDITY_ENTITY, default=current.get(CONF_HUMIDITY_ENTITY, "")): str,
                vol.Optional(CONF_GLOBE_TEMP_ENTITY, default=current.get(CONF_GLOBE_TEMP_ENTITY, "")): str,
                vol.Optional(CONF_WBGT_ENTITY, default=current.get(CONF_WBGT_ENTITY, "")): str,
                vol.Required(CONF_WORKLOAD_MODE, default=current.get(CONF_WORKLOAD_MODE, DEFAULT_WORKLOAD_MODE)): vol.In([WORKLOAD_MODE_STATIC, WORKLOAD_MODE_MQTT]),
                vol.Required(CONF_WORKLOAD, default=current.get(CONF_WORKLOAD, DEFAULT_WORKLOAD)): vol.In(WORKLOAD_OPTIONS),
                vol.Optional(CONF_MQTT_TOPIC, default=current.get(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC)): str,
                vol.Required(CONF_ACCLIMATIZATION, default=current.get(CONF_ACCLIMATIZATION, DEFAULT_ACCLIMATIZATION)): vol.In(ACCLIMATIZATION_OPTIONS),
                vol.Required(CONF_SHIFT_START, default=current.get(CONF_SHIFT_START, DEFAULT_SHIFT_START)): str,
                vol.Required(CONF_SHIFT_END, default=current.get(CONF_SHIFT_END, DEFAULT_SHIFT_END)): str,
                vol.Required(CONF_CLOTHING, default=current.get(CONF_CLOTHING, DEFAULT_CLOTHING)): vol.In(CLOTHING_OPTIONS),
                vol.Required(CONF_COUNTRY, default=current.get(CONF_COUNTRY, (self.hass.config.country or "").upper() if (self.hass.config.country or "").upper() in SUPPORTED_COUNTRIES else "")): vol.In(SUPPORTED_COUNTRIES),
                vol.Optional(CONF_STATE, default=current.get(CONF_STATE, DEFAULT_STATE)): vol.In(US_STATES),
                vol.Required(CONF_UPDATE_INTERVAL, default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)): vol.All(int, vol.Range(min=1, max=1440)),
            }),
            errors=errors,
        )
