from __future__ import annotations

import re

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.helpers import selector

from .qr import (
    CloudhookUnavailable,
    DEFAULT_DEVICE_ID,
    DEFAULT_TRACKER_ID,
    DEFAULT_USER,
    EncryptionSecretUnavailable,
    OwnTracksNotConfigured,
    async_build_owntracks_qr_payload,
)
from .const import (
    ACCLIMATIZATION_OPTIONS,
    CLOTHING_LABELS,
    CLOTHING_OPTIONS,
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


# All the work-/standards-related and technical fields live behind this collapsed
# header so first-time setup stays focused on "alert me when it's dangerously hot":
# the visible fields are just location + the alert device. Sensible defaults mean a
# beginner can submit without ever opening it.
ADVANCED_SECTION = "advanced"


def _flatten(user_input: dict) -> dict:
    """Merge the collapsed advanced section's fields back up to the top level."""
    flat = dict(user_input)
    advanced = flat.pop(ADVANCED_SECTION, None)
    if isinstance(advanced, dict):
        flat.update(advanced)
    return flat


def _settings_schema(hass, current: dict) -> vol.Schema:
    """Schema shared by setup and options: location + alert device up front, the
    rest in a collapsed Advanced section. ``current`` pre-fills every field."""
    ha_lat = hass.config.latitude
    ha_lon = hass.config.longitude
    default_country = (current.get(CONF_COUNTRY) or (hass.config.country or "")).upper()
    if default_country not in SUPPORTED_COUNTRIES:
        default_country = ""

    def d(key, fallback):
        return current.get(key, fallback)

    advanced = vol.Schema({
        vol.Required(CONF_WEATHER_MODE, default=d(CONF_WEATHER_MODE, WEATHER_MODE_LOCATION)): vol.In([
            WEATHER_MODE_LOCATION,
            WEATHER_MODE_TRACKED_ENTITY,
            WEATHER_MODE_HA_SENSORS,
            WEATHER_MODE_MANUAL_WBGT,
        ]),
        vol.Optional(CONF_LOCATION_ENTITY, default=d(CONF_LOCATION_ENTITY, "")): str,
        vol.Optional(CONF_TEMP_ENTITY, default=d(CONF_TEMP_ENTITY, "")): str,
        vol.Optional(CONF_HUMIDITY_ENTITY, default=d(CONF_HUMIDITY_ENTITY, "")): str,
        vol.Optional(CONF_GLOBE_TEMP_ENTITY, default=d(CONF_GLOBE_TEMP_ENTITY, "")): str,
        vol.Optional(CONF_WBGT_ENTITY, default=d(CONF_WBGT_ENTITY, "")): str,
        vol.Required(CONF_WORKLOAD_MODE, default=d(CONF_WORKLOAD_MODE, DEFAULT_WORKLOAD_MODE)): vol.In([WORKLOAD_MODE_STATIC, WORKLOAD_MODE_MQTT]),
        vol.Required(CONF_WORKLOAD, default=d(CONF_WORKLOAD, DEFAULT_WORKLOAD)): vol.In(WORKLOAD_OPTIONS),
        vol.Optional(CONF_MQTT_TOPIC, default=d(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC)): str,
        vol.Required(CONF_ACCLIMATIZATION, default=d(CONF_ACCLIMATIZATION, DEFAULT_ACCLIMATIZATION)): vol.In(ACCLIMATIZATION_OPTIONS),
        vol.Required(CONF_SHIFT_START, default=d(CONF_SHIFT_START, DEFAULT_SHIFT_START)): str,
        vol.Required(CONF_SHIFT_END, default=d(CONF_SHIFT_END, DEFAULT_SHIFT_END)): str,
        vol.Required(CONF_CLOTHING, default=d(CONF_CLOTHING, DEFAULT_CLOTHING)): vol.In(CLOTHING_OPTIONS),
        vol.Required(CONF_COUNTRY, default=default_country): vol.In(SUPPORTED_COUNTRIES),
        vol.Optional(CONF_STATE, default=d(CONF_STATE, DEFAULT_STATE)): vol.In(US_STATES),
        vol.Required(CONF_API_URL, default=d(CONF_API_URL, DEFAULT_API_URL)): str,
        vol.Required(CONF_UPDATE_INTERVAL, default=d(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)): vol.All(int, vol.Range(min=1, max=1440)),
    })

    return vol.Schema({
        vol.Optional(CONF_LATITUDE, default=d(CONF_LATITUDE, ha_lat)): vol.Coerce(float),
        vol.Optional(CONF_LONGITUDE, default=d(CONF_LONGITUDE, ha_lon)): vol.Coerce(float),
        vol.Optional(
            CONF_ALERT_DEVICE,
            description={"suggested_value": current.get(CONF_ALERT_DEVICE)},
        ): selector.DeviceSelector(selector.DeviceSelectorConfig(integration="mobile_app")),
        vol.Required(ADVANCED_SECTION): section(advanced, {"collapsed": True}),
    })


async def _validate_settings(hass, flat: dict, errors: dict) -> str:
    """Validate a flattened settings dict; record a base-level error if any and
    return the normalized API URL."""
    api_url = (flat.get(CONF_API_URL) or DEFAULT_API_URL).rstrip("/")
    if not await _test_api_connection(api_url):
        errors["base"] = "cannot_connect"
        return api_url

    mode = flat.get(CONF_WEATHER_MODE, WEATHER_MODE_LOCATION)
    if mode == WEATHER_MODE_LOCATION:
        if flat.get(CONF_LATITUDE) is None or flat.get(CONF_LONGITUDE) is None:
            errors["base"] = "missing_location"
    elif mode == WEATHER_MODE_TRACKED_ENTITY:
        if not (flat.get(CONF_LOCATION_ENTITY) or "").strip():
            errors["base"] = "missing_location_entity"
    elif mode == WEATHER_MODE_HA_SENSORS:
        if not flat.get(CONF_TEMP_ENTITY) or not flat.get(CONF_HUMIDITY_ENTITY):
            errors["base"] = "missing_sensors"
    elif mode == WEATHER_MODE_MANUAL_WBGT:
        if not flat.get(CONF_WBGT_ENTITY):
            errors["base"] = "missing_wbgt_entity"

    if not errors:
        workload_mode = flat.get(CONF_WORKLOAD_MODE, WORKLOAD_MODE_STATIC)
        if workload_mode == WORKLOAD_MODE_MQTT:
            if not (flat.get(CONF_MQTT_TOPIC) or "").strip():
                errors["base"] = "missing_mqtt_topic"
            elif "mqtt" not in hass.config.components:
                errors["base"] = "mqtt_not_available"

    if not errors:
        try:
            _validate_time(flat.get(CONF_SHIFT_START, DEFAULT_SHIFT_START))
            _validate_time(flat.get(CONF_SHIFT_END, DEFAULT_SHIFT_END))
        except vol.Invalid:
            errors["base"] = "invalid_time"

    return api_url


class HeatStressGuidanceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        current = _flatten(user_input) if user_input is not None else {}

        if user_input is not None:
            api_url = await _validate_settings(self.hass, current, errors)
            if not errors:
                await self.async_set_unique_id(api_url)
                self._abort_if_unique_id_configured()
                current[CONF_API_URL] = api_url
                workload_mode = current.get(CONF_WORKLOAD_MODE, WORKLOAD_MODE_STATIC)
                mode_label = (
                    "MQTT" if workload_mode == WORKLOAD_MODE_MQTT
                    else current.get(CONF_WORKLOAD, DEFAULT_WORKLOAD)
                )
                return self.async_create_entry(
                    title=f"Heat Stress Guidance ({mode_label})", data=current
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(self.hass, current),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HeatStressOptionsFlow(config_entry)


class HeatStressOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry
        self._qr_identity = None

    async def async_step_init(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["configure", "show_qr"],
        )

    async def async_step_show_qr(self, user_input=None) -> FlowResult:
        if user_input is not None:
            identity = (
                (user_input.get("user") or "").strip() or DEFAULT_USER,
                (user_input.get("deviceid") or "").strip() or DEFAULT_DEVICE_ID,
                (user_input.get("trackerid") or "").strip() or DEFAULT_TRACKER_ID,
            )
            # Submitting without changing any identity field means "I'm done" —
            # finish the flow so the dialog closes (preserving existing options),
            # instead of re-rendering the same QR forever. Editing a field and
            # resubmitting falls through below to regenerate the QR.
            if identity == self._qr_identity:
                return self.async_create_entry(
                    title="", data=dict(self._config_entry.options)
                )
        else:
            identity = (DEFAULT_USER, DEFAULT_DEVICE_ID, DEFAULT_TRACKER_ID)

        user, device_id, tracker_id = identity
        try:
            payload = await async_build_owntracks_qr_payload(
                self.hass, user=user, device_id=device_id, tracker_id=tracker_id
            )
        except OwnTracksNotConfigured:
            return self.async_abort(reason="owntracks_not_configured")
        except CloudhookUnavailable:
            return self.async_abort(reason="cloud_unavailable")
        except EncryptionSecretUnavailable:
            return self.async_abort(reason="owntracks_secret_unavailable")

        # Remember what this QR encodes so the next submit can tell "regenerate
        # with a changed identity" apart from "done, close the dialog".
        self._qr_identity = identity
        return self.async_show_form(
            step_id="show_qr",
            data_schema=vol.Schema({
                vol.Optional("user", default=user): str,
                vol.Optional("deviceid", default=device_id): str,
                vol.Optional("trackerid", default=tracker_id): str,
                vol.Optional("qr"): selector.QrCodeSelector(
                    config=selector.QrCodeSelectorConfig(
                        data=payload,
                        scale=4,
                        error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                    )
                ),
            }),
        )

    async def async_step_configure(self, user_input=None) -> FlowResult:
        errors = {}
        stored = {**self._config_entry.data, **self._config_entry.options}
        current = stored

        if user_input is not None:
            flat = _flatten(user_input)
            current = {**stored, **flat}
            api_url = await _validate_settings(self.hass, current, errors)
            if not errors:
                current[CONF_API_URL] = api_url
                # An emptied device selector is absent from the submission; drop it
                # so the alert device can actually be cleared, not just changed.
                if CONF_ALERT_DEVICE not in flat:
                    current.pop(CONF_ALERT_DEVICE, None)
                return self.async_create_entry(title="", data=current)

        return self.async_show_form(
            step_id="configure",
            data_schema=_settings_schema(self.hass, current),
            errors=errors,
        )
