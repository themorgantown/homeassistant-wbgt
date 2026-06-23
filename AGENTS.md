# AGENTS.md

This file provides guidance to AI agents.

## What this is

A Home Assistant custom integration (`heat_stress_guidance`) distributed via HACS. It is a **thin client** over the hosted [Heat Guidance Calculator API](https://heat-guidance-calculator.pages.dev) (`iot_class: cloud_polling`, no Python `requirements`). All heat-stress science — evaluating 76 occupational standards and picking the most protective work/rest schedule — happens server-side. This repo's job is: acquire a WBGT value, POST it to the API, and expose the response as HA entities.

## Commands

```bash
# Install test deps and run the live API contract test
pip install -r requirements_test.txt
pytest tests/test_api_contract.py -v

# Run a single test
pytest tests/test_api_contract.py::test_compare_normal -v

# Point the contract test at a different/local API instance
HGC_API_BASE=http://localhost:8788 pytest tests/test_api_contract.py -v

# Validate JSON manifests / translations
python3 -m json.tool hacs.json
python3 -m compileall custom_components/heat_stress_guidance tests
```

There is no build step or linter configured. `tests/test_api_contract.py` is the only test and it hits the **live** hosted API (also runs daily via `.github/workflows/api-contract.yml`). It has no Home Assistant dependency — it just validates that the API still returns the response fields the integration reads.

## Architecture

The whole runtime flows through one class, `HeatStressCoordinator` (`coordinator.py`), a `DataUpdateCoordinator`. Each poll (`_async_update_data`):

1. **`_get_wbgt()`** resolves a WBGT °C value via one of four `weather_mode`s:
   - `location` — fixed lat/lon → `GET /api/v1/weather/wbgt`, picks the hourly entry closest to now.
   - `tracked_entity` — reads `latitude`/`longitude` attributes off a `person.*`/`device_tracker.*` entity, then same API call. Both location modes share `_get_location_wbgt()`.
   - `ha_sensors` — reads HA temp/humidity entities and estimates WBGT locally via the Stull (2011) wet-bulb approximation (`_estimate_wbgt`). Globe-temp entity is optional; without it, dry-bulb substitutes for Tg and the result undercounts radiant load.
   - `manual_wbgt` — reads a WBGT value directly off an HA entity (auto-converts °F→°C).
2. **`_call_compare_api()`** POSTs to `/api/v1/compare` with workload, acclimatization, clothing, shift times.
3. The flattened response dict feeds 8 `CoordinatorEntity` sensors (`sensor.py`) + 1 binary sensor (`binary_sensor.py`). `_derive_risk_level()` maps the composite schedule to a risk tier locally.

**Failure backoff.** `_async_update_data` applies exponential backoff: each consecutive failure doubles the effective `update_interval` (capped at `MAX_BACKOFF_INTERVAL`, 1 hour), resetting to the configured interval on the next success. This keeps a down/erroring API from being polled at the full configured rate (which can be as low as 1 min). HA's base coordinator does not do this on its own — it reschedules at a fixed interval.

**Defensive field reads are intentional.** The coordinator reads every API field with `.get()` so a renamed/dropped upstream field blanks a sensor rather than crashing. This is exactly why the contract test exists — when you change which response fields the coordinator consumes, update the field lists at the top of `tests/test_api_contract.py` to match.

### Workload detection (`workload_mode`)
- `static` — uses the configured `workload` setting.
- `mqtt` — subscribes (via `async_start_mqtt`) to an open-sensor accelerometer MQTT topic. `_handle_mqtt_message` computes net motion `abs(sqrt(x²+y²+z²) - 9.81)`, maps it to light/moderate/heavy/very_heavy via configurable thresholds, and triggers an immediate refresh when the tier changes. Falls back to the static `workload` until the first message arrives.

### Config & options flow (`config_flow.py`)
- Setup is a 3-step wizard: `user` (API) → `weather` (mode + source) → `worker` (workload/profile).
- The options flow (`HeatStressOptionsFlow`) exposes **all** of those settings for later editing in one form.
- `__init__.py` registers `add_update_listener(_async_update_options)` which calls `async_reload`. This reload is **required**: changing weather/workload mode (especially static→MQTT) must rebuild the coordinator and re-subscribe MQTT, which only happens on reload.
- `coordinator._config` is a property merging `entry.data | entry.options` so options changes apply on the next poll without a restart.

## Conventions specific to this repo

- `const.py` is the single source of truth for all `CONF_*` keys, `WEATHER_MODE_*`/`WORKLOAD_MODE_*` values, defaults, and option lists. Add new config there first.
- Any user-facing string (field labels, error keys like `cannot_connect`, mode selector values) must be mirrored in **both** `strings.json` and `translations/en.json` — they are kept identical.
- Do not commit `__pycache__`/`.pyc` (a stray one was previously tracked; `.gitignore` now covers it).

## HACS install gotcha

If a user reports `Repository themorgantown/homeassistant-wbgt not found`, the repo is fine (public, returns HTTP 200). HACS reports this when searching its default catalog. The fix is to add it as a **custom repository** (HACS → ⋮ → Custom repositories → repo URL, category Integration) before downloading. This is documented in the README install section.
