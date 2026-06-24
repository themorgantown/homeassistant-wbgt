"""Offline unit-test harness.

The integration modules import ``homeassistant``, which is deliberately *not* a
test dependency — the suite stays light (see requirements_test.txt) so it runs
in CI in seconds without pulling the whole Home Assistant core. When the real
package isn't importable we register minimal stand-ins for just the symbols the
integration touches at import time, so its pure logic (jurisdiction scoping,
forecast peak, entity availability, …) can be unit-tested in isolation. If a
real Home Assistant *is* installed, we use it and skip the stubs entirely.

These stubs intentionally model only what the tests exercise. The most
behaviour-bearing one is ``CoordinatorEntity.available`` → ``last_update_success``,
which mirrors real HA so the availability tests assert the genuine contract.
"""
from __future__ import annotations

import sys
import types
from datetime import timezone
from pathlib import Path

# Make `custom_components.heat_stress_guidance...` importable from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _install_homeassistant_stubs() -> None:
    ha = _module("homeassistant")

    core = _module("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - opaque stand-in
        ...

    def callback(fn):
        return fn

    core.HomeAssistant = HomeAssistant
    core.callback = callback
    ha.core = core

    config_entries = _module("homeassistant.config_entries")

    class ConfigEntry:
        ...

    config_entries.ConfigEntry = ConfigEntry
    ha.config_entries = config_entries

    # homeassistant.const
    const = _module("homeassistant.const")

    class UnitOfTemperature:
        CELSIUS = "°C"

    const.UnitOfTemperature = UnitOfTemperature
    ha.const = const

    # homeassistant.components.{sensor,binary_sensor}
    components = _module("homeassistant.components")
    ha.components = components

    sensor = _module("homeassistant.components.sensor")

    class SensorEntity:
        ...

    class SensorStateClass:
        MEASUREMENT = "measurement"

    class SensorDeviceClass:
        TIMESTAMP = "timestamp"

    sensor.SensorEntity = SensorEntity
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorDeviceClass = SensorDeviceClass
    components.sensor = sensor

    binary_sensor = _module("homeassistant.components.binary_sensor")

    class BinarySensorEntity:
        ...

    class BinarySensorDeviceClass:
        SAFETY = "safety"

    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    components.binary_sensor = binary_sensor

    # homeassistant.helpers.*
    helpers = _module("homeassistant.helpers")
    ha.helpers = helpers

    device_registry = _module("homeassistant.helpers.device_registry")
    helpers.device_registry = device_registry

    aiohttp_client = _module("homeassistant.helpers.aiohttp_client")

    def async_get_clientsession(hass):  # pragma: no cover - never called offline
        raise RuntimeError("network sessions are unavailable in unit tests")

    aiohttp_client.async_get_clientsession = async_get_clientsession
    helpers.aiohttp_client = aiohttp_client

    entity_platform = _module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    helpers.entity_platform = entity_platform

    update_coordinator = _module("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *args, **kwargs) -> None:
            self.update_interval = kwargs.get("update_interval")
            self.last_update_success = True

    class UpdateFailed(Exception):
        ...

    class CoordinatorEntity:
        """Mirrors HA: availability follows the coordinator's last update."""

        def __init__(self, coordinator, *args, **kwargs) -> None:
            self.coordinator = coordinator

        @property
        def available(self) -> bool:
            return self.coordinator.last_update_success

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    helpers.update_coordinator = update_coordinator

    # homeassistant.util + homeassistant.util.dt
    util = _module("homeassistant.util")

    def slugify(value, *_args, **_kwargs):
        return str(value)

    util.slugify = slugify

    dt = _module("homeassistant.util.dt")

    try:
        from zoneinfo import ZoneInfo

        def get_time_zone(name):
            try:
                return ZoneInfo(name)
            except Exception:  # noqa: BLE001 - unknown tz name → caller falls back
                return None

    except ImportError:  # pragma: no cover

        def get_time_zone(name):
            return None

    dt.get_time_zone = get_time_zone
    dt.DEFAULT_TIME_ZONE = timezone.utc
    dt.now = lambda: None
    dt.utcnow = lambda: None
    util.dt = dt
    ha.util = util


# Prefer a real Home Assistant if the environment provides one. Probe a real
# submodule rather than the top-level package: the repo ships a `homeassistant/`
# lovelace directory that Python would otherwise import as an empty namespace
# package, masking the absence of the actual library.
try:
    import homeassistant.config_entries  # noqa: F401
except ImportError:
    _install_homeassistant_stubs()
