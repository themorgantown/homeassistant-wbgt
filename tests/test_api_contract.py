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
# Per-standard fields the coordinator reads to scope the composite to the
# user's jurisdiction (_scope_composite / _standard_in_scope). The integration
# no longer trusts the API's global `composite`; it recomputes from results[].
RESULTS_FIELDS = [
    "results[].standardId",
    "results[].applicable",
    "results[].jurisdiction",
    "results[].countryCode",
    "results[].recommendation.stopWork",
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


def _assert_reachable(body, paths):
    """Like _assert_present but tolerates null values (e.g. countryCode is
    legitimately null for global standards) — only the key must exist."""
    missing = [p for p in paths if _get(body, p) is _MISSING]
    assert not missing, f"unreachable fields the integration reads: {missing}"


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


def test_results_carry_jurisdiction_fields():
    """The integration scopes guidance to the user's country/state by reading
    each standard's jurisdiction tags and recommendation from results[]. Guard
    that contract so a server-side rename doesn't silently break scoping."""
    body = _compare(NORMAL)
    results = body.get("results")
    assert isinstance(results, list) and results, "results[] must be a non-empty list"
    _assert_reachable(body, RESULTS_FIELDS)
    # At least one global standard (countryCode null) must exist — these are the
    # baseline that always applies regardless of the user's jurisdiction scope.
    assert any(r.get("countryCode") is None for r in results), "expected ≥1 global standard"
    # The default pinned standard (LIN) must be one the /compare response carries,
    # or single-standard selection would fall through to scopeEmpty for everyone
    # on the default config.
    assert any(r.get("standardId") == "la_isla_network_rshs" for r in results), (
        "expected the LIN default standard (la_isla_network_rshs) in results[]"
    )


def test_standards_endpoint_lists_lin_default():
    """The config flow downloads GET /api/v1/standards to populate the standard
    selector and defaults to LIN. Guard that the endpoint exists, returns
    id+displayName per standard, and still carries la_isla_network_rshs."""
    resp = requests.get(f"{API_BASE}/api/v1/standards", timeout=TIMEOUT)
    assert resp.status_code == 200, resp.text
    standards = resp.json().get("standards")
    assert isinstance(standards, list) and standards, "standards[] must be a non-empty list"
    for s in standards:
        assert s.get("id") and s.get("displayName"), f"standard missing id/displayName: {s}"
    assert any(s.get("id") == "la_isla_network_rshs" for s in standards), (
        "expected the LIN default standard (la_isla_network_rshs) in /api/v1/standards"
    )


def test_weather_endpoint_returns_hourly_wbgt():
    resp = requests.get(
        f"{API_BASE}/api/v1/weather/wbgt",
        params={"lat": 37.7749, "lon": -122.4194},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    value = _get(body, "hourlyWbgt[].valueC")
    assert value not in (_MISSING, None), "hourlyWbgt[].valueC unreachable"
    assert isinstance(value, (int, float))

    # The forecast-peak sensors anchor each hour to a real instant, so they read
    # the per-entry date/time and the top-level timezone. Guard those too.
    for path in ("hourlyWbgt[].date", "hourlyWbgt[].time", "timezone"):
        assert _get(body, path) not in (_MISSING, None), f"{path} unreachable"
