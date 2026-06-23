"""Daily live check against the Heat Guidance Calculator API.

Hits the real API with the same payloads the integration sends and asserts every
response field the integration reads is reachable and returns sane values. Runs
daily in CI (.github/workflows/api-contract.yml) so that if the API renames or
drops a field the integration depends on, we find out within a day instead of
via a blank sensor in someone's Home Assistant.

The field lists below mirror what custom_components/heat_stress_guidance/
coordinator.py reads — keep them in sync when you change what the integration
consumes. Override the target with HGC_API_BASE (e.g. a local dev server).
"""
from __future__ import annotations

import os

import pytest
import requests

API_BASE = os.environ.get(
    "HGC_API_BASE", "https://heat-guidance-calculator.pages.dev"
).rstrip("/")
TIMEOUT = 20

_SHIFT = {"shiftStart": "07:00", "shiftEnd": "15:00", "date": "2026-06-23"}

# A normal scenario produces a work/rest schedule; the stop-work scenario does
# not (the API omits the schedule fields when all work must stop — which the
# integration handles, reading them with .get()). We exercise both.
NORMAL = {
    "wbgtC": 25.0,
    "workload": "light",
    "acclimatization": "acclimatized",
    "clothing": "work",
    "outdoor": False,
    **_SHIFT,
}
STOP_WORK = {
    "wbgtC": 31.0,
    "workload": "heavy",
    "acclimatization": "unacclimatized",
    "clothing": "work",
    "outdoor": True,
    **_SHIFT,
}

# Fields the integration reads from every compare response, stop-work or not.
ALWAYS_PRESENT = [
    "composite.stopWork",
    "composite.contributingStandards",
    "inputSummary.rawWbgtC",
    "inputSummary.effectiveWbgtC",
    "derivedOutputs.hydration.mlPerHr",
    "derivedOutputs.hydration.mlPerBreak",
    "derivedOutputs.hydration.hyponatremiaCeiling",
]
# Fields present only when a schedule is produced (omitted under stop-work).
SCHEDULE_ONLY = [
    "composite.workMinutesPerHour",
    "composite.restMinutesPerHour",
    "composite.advisoryStandards",
]

_MISSING = object()


def _get(obj, path: str):
    """Resolve a dotted path, '[]' meaning 'first array element'.
    Returns _MISSING if any step is absent."""
    cur = obj
    for part in path.split("."):
        if part.endswith("[]"):
            cur = cur.get(part[:-2]) if isinstance(cur, dict) else None
            if not isinstance(cur, list) or not cur:
                return _MISSING
            cur = cur[0]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return _MISSING
            cur = cur[part]
    return cur


def _assert_present(body, paths):
    missing = [p for p in paths if _get(body, p) in (_MISSING, None)]
    assert not missing, f"missing or null fields the integration reads: {missing}"


def _compare(payload):
    resp = requests.post(f"{API_BASE}/api/v1/compare", json=payload, timeout=TIMEOUT)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health_endpoint():
    resp = requests.get(f"{API_BASE}/health", timeout=TIMEOUT)
    assert resp.status_code == 200


def test_normal_scenario_returns_full_schedule():
    body = _compare(NORMAL)
    _assert_present(body, ALWAYS_PRESENT + SCHEDULE_ONLY)

    # ...and the values are sane.
    assert _get(body, "composite.stopWork") is False
    assert _get(body, "inputSummary.rawWbgtC") == 25.0
    work = _get(body, "composite.workMinutesPerHour")
    rest = _get(body, "composite.restMinutesPerHour")
    assert work + rest == 60, f"work/rest should fill the hour, got {work}+{rest}"
    assert _get(body, "derivedOutputs.hydration.mlPerHr") > 0


def test_stop_work_scenario_flags_stop_work():
    body = _compare(STOP_WORK)
    _assert_present(body, ALWAYS_PRESENT)
    assert _get(body, "composite.stopWork") is True
    assert _get(body, "derivedOutputs.hydration.mlPerHr") > 0


def test_weather_endpoint_returns_hourly_wbgt():
    resp = requests.get(
        f"{API_BASE}/api/v1/weather/wbgt",
        params={"lat": 37.7749, "lon": -122.4194},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, resp.text
    value = _get(resp.json(), "hourlyWbgt[].valueC")
    assert value not in (_MISSING, None), "hourlyWbgt[].valueC unreachable"
    assert isinstance(value, (int, float))
