from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

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
    CONF_STANDARD,
    CONF_STATE,
    CONF_TEMP_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WBGT_ENTITY,
    CONF_WEATHER_MODE,
    CONF_WORKER_DEVICE,
    CONF_WORKER_NAME,
    CONF_WORKLOAD,
    CONF_WORKLOAD_MODE,
    DEFAULT_ACCLIMATIZATION,
    DEFAULT_API_URL,
    DEFAULT_CLOTHING,
    DEFAULT_MQTT_TOPIC,
    DEFAULT_SHIFT_END,
    DEFAULT_SHIFT_START,
    DEFAULT_STANDARD,
    DEFAULT_STATE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WORKER_NAME,
    DEFAULT_WORKLOAD,
    DEFAULT_WORKLOAD_MODE,
    DOMAIN,
    STANDARD_AUTO,
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


def _normalize_api_url(raw: str) -> str | None:
    """Return a canonical https (or local-only http) URL, or None if invalid.

    This is a worker-safety product that POSTs live GPS coordinates, so plaintext
    http is rejected for anything but a local/self-hosted host — a typo or a
    pasted ``http://`` must not silently leak coordinates in cleartext.
    Lowercasing host+scheme and stripping the trailing slash also makes the value
    canonical, so scheme/case/slash variants can't bypass the unique_id guard.
    """
    p = urlparse((raw or "").strip())
    if not p.scheme or not p.netloc or not p.hostname:
        return None
    host = p.hostname.lower()
    if p.scheme == "http":
        try:
            addr = ipaddress.ip_address(host)
            is_local = addr.is_private or addr.is_loopback
        except ValueError:
            is_local = host == "localhost"
        if not is_local:
            return None
    elif p.scheme != "https":
        return None
    netloc = host + (f":{p.port}" if p.port else "")
    return f"{p.scheme}://{netloc}{p.path}".rstrip("/")


async def _test_api_connection(hass, api_url: str) -> bool:
    url = api_url.rstrip("/") + "/health"
    try:
        session = async_get_clientsession(hass)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return resp.status == 200
    except Exception:
        return False


async def _fetch_standards(hass, api_url: str) -> list:
    """Download the available standards (id + displayName) to populate the
    selector. Best-effort: returns ``[]`` on any failure so the form still
    renders (the selector then offers Auto + the LIN default only)."""
    url = (api_url or DEFAULT_API_URL).rstrip("/") + "/api/v1/standards"
    try:
        session = async_get_clientsession(hass)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except Exception:
        return []
    return data.get("standards") or []


def _standard_options(standards: list) -> list[selector.SelectOptionDict]:
    """SelectSelector options: ``Auto`` first, then each downloaded standard.

    The LIN default is always injected so it stays selectable even when the
    standards fetch failed and returned an empty list."""
    options = [
        selector.SelectOptionDict(
            value=STANDARD_AUTO, label="Auto — most protective rule in my region"
        )
    ]
    seen: set[str] = set()
    for s in standards:
        sid = s.get("id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        options.append(selector.SelectOptionDict(value=sid, label=s.get("displayName") or sid))
    if DEFAULT_STANDARD not in seen:
        options.append(
            selector.SelectOptionDict(
                value=DEFAULT_STANDARD,
                label="La Isla Network-informed RSH-s Default Protocol",
            )
        )
    return options


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


def _settings_schema(
    hass, current: dict, standards: list, default_standard: str, with_name: bool = False
) -> vol.Schema:
    """Schema shared by setup and options: location + alert/worker devices up
    front, the rest in a collapsed Advanced section. ``current`` pre-fills every
    field; ``standards`` populates the standard selector; ``default_standard`` is
    the fallback when the entry has no saved standard (LIN for new installs,
    Auto for existing ones, so an upgrade doesn't silently change behavior).
    ``with_name`` adds the worker/site name field (setup only — it names the
    entry, device, and entities, and is fixed once the entry exists)."""
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
        vol.Required(CONF_STANDARD, default=d(CONF_STANDARD, default_standard)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_standard_options(standards),
                mode=selector.SelectSelectorMode.DROPDOWN,
                # Tolerate a saved id that is missing from a degraded fetch, so a
                # pinned standard never fails validation when the API is briefly down.
                custom_value=True,
            )
        ),
        vol.Required(CONF_COUNTRY, default=default_country): vol.In(SUPPORTED_COUNTRIES),
        vol.Optional(CONF_STATE, default=d(CONF_STATE, DEFAULT_STATE)): vol.In(US_STATES),
        vol.Required(CONF_API_URL, default=d(CONF_API_URL, DEFAULT_API_URL)): str,
        vol.Required(CONF_UPDATE_INTERVAL, default=d(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)): vol.All(int, vol.Range(min=1, max=1440)),
    })

    top: dict = {}
    if with_name:
        # First field at setup: each worker is its own entry, named here.
        top[vol.Required(CONF_WORKER_NAME, default=d(CONF_WORKER_NAME, DEFAULT_WORKER_NAME))] = str
    top.update({
        vol.Optional(CONF_LATITUDE, default=d(CONF_LATITUDE, ha_lat)): vol.Coerce(float),
        vol.Optional(CONF_LONGITUDE, default=d(CONF_LONGITUDE, ha_lon)): vol.Coerce(float),
        vol.Optional(
            CONF_ALERT_DEVICE,
            description={"suggested_value": current.get(CONF_ALERT_DEVICE)},
        ): selector.DeviceSelector(selector.DeviceSelectorConfig(integration="mobile_app")),
        vol.Optional(
            CONF_WORKER_DEVICE,
            description={"suggested_value": current.get(CONF_WORKER_DEVICE)},
        ): selector.DeviceSelector(selector.DeviceSelectorConfig(integration="mobile_app")),
        vol.Required(ADVANCED_SECTION): section(advanced, {"collapsed": True}),
    })
    return vol.Schema(top)


async def _validate_settings(hass, flat: dict, errors: dict) -> str:
    """Validate a flattened settings dict; record a base-level error if any and
    return the normalized API URL."""
    api_url = _normalize_api_url(flat.get(CONF_API_URL) or DEFAULT_API_URL)
    if api_url is None:
        errors["base"] = "invalid_api_url"
        return (flat.get(CONF_API_URL) or DEFAULT_API_URL).rstrip("/")
    if not await _test_api_connection(hass, api_url):
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
                # One config entry per worker — keyed on the worker name, not the
                # API URL (which every worker shares). The slug also disambiguates
                # the entry's OwnTracks identity. Re-adding the same name aborts so
                # two entries can't claim the same worker / device_tracker.
                name = (current.get(CONF_WORKER_NAME) or "").strip() or DEFAULT_WORKER_NAME
                current[CONF_WORKER_NAME] = name
                await self.async_set_unique_id(slugify(name))
                self._abort_if_unique_id_configured()
                current[CONF_API_URL] = api_url
                return self.async_create_entry(title=name, data=current)

        standards = await _fetch_standards(
            self.hass, current.get(CONF_API_URL) or DEFAULT_API_URL
        )
        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(
                self.hass, current, standards, DEFAULT_STANDARD, with_name=True
            ),
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
        # Default the OwnTracks identity to this worker's name, so scanning their
        # phone yields device_tracker.<name>_phone — the tracker this entry links to.
        name = self._config_entry.data.get(CONF_WORKER_NAME) or DEFAULT_WORKER_NAME
        default_user = slugify(name) or DEFAULT_USER

        if user_input is not None:
            identity = (
                (user_input.get("user") or "").strip() or default_user,
                (user_input.get("deviceid") or "").strip() or DEFAULT_DEVICE_ID,
                (user_input.get("trackerid") or "").strip() or DEFAULT_TRACKER_ID,
            )
            # Submitting without changing any identity field means "I'm done" —
            # finish the flow so the dialog closes, instead of re-rendering the
            # same QR forever. Editing a field and resubmitting falls through
            # below to regenerate the QR.
            if identity == self._qr_identity:
                options = dict(self._config_entry.options)
                if user_input.get("link_tracking", True):
                    qr_user, qr_device, _ = identity
                    # OwnTracks publishes as device_tracker.<user>_<device>
                    # (slugified). Point this worker's entry at that tracker so
                    # their location flows into their monitoring the moment they
                    # scan — no manual entity wiring.
                    options[CONF_WEATHER_MODE] = WEATHER_MODE_TRACKED_ENTITY
                    options[CONF_LOCATION_ENTITY] = (
                        f"device_tracker.{slugify(qr_user)}_{slugify(qr_device)}"
                    )
                return self.async_create_entry(title="", data=options)
        else:
            identity = (default_user, DEFAULT_DEVICE_ID, DEFAULT_TRACKER_ID)

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
                vol.Optional("link_tracking", default=True): bool,
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
                # so a device can actually be cleared, not just changed.
                for key in (CONF_ALERT_DEVICE, CONF_WORKER_DEVICE):
                    if key not in flat:
                        current.pop(key, None)
                return self.async_create_entry(title="", data=current)

        standards = await _fetch_standards(
            self.hass, stored.get(CONF_API_URL) or DEFAULT_API_URL
        )
        # Existing entries that predate the standard selector default to Auto, so
        # opening this form and saving doesn't silently switch them to LIN.
        return self.async_show_form(
            step_id="configure",
            data_schema=_settings_schema(self.hass, current, standards, STANDARD_AUTO),
            errors=errors,
        )
