"""Jurisdiction scoping, derived risk, and entity-availability contracts.

These guard the safety-critical behaviour of the integration: the guidance a
worker sees must come from a standard that actually applies to their location,
and the SAFETY "Stop Work" sensor must never read *clear* merely because no
standard was in scope. The functions under test are pure; the entity tests
exercise the real ``available`` wiring against a fake coordinator.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heat_stress_guidance.coordinator import (
    _derive_risk_level,
    _is_restricted,
    _scope_composite,
    _standard_in_scope,
)
from custom_components.heat_stress_guidance.binary_sensor import StopWorkBinarySensor
from custom_components.heat_stress_guidance.sensor import WbgtSensor


# --- helpers ---------------------------------------------------------------


def _std(jurisdiction, *, country=None, applicable=True, stop=False, work=None, name="std"):
    """Build one API ``results[]`` entry."""
    rec = {"stopWork": stop}
    if work is not None:
        rec["workMinutesPerHour"] = work
        rec["restMinutesPerHour"] = 60 - work
    return {
        "displayName": name,
        "jurisdiction": jurisdiction,
        "countryCode": country,
        "applicable": applicable,
        "recommendation": rec,
    }


GLOBAL = _std("global", country=None, work=45, name="ISO 7243")
US_FEDERAL = _std("osha_us", country="US", work=30, name="OSHA")
CALIFORNIA = _std("california_usa", country="US", work=20, name="Cal/OSHA")
UAE = _std("uae", country="AE", stop=True, name="UAE midday ban")


# --- _standard_in_scope ----------------------------------------------------


def test_global_standard_always_in_scope():
    assert _standard_in_scope(GLOBAL, "US", "NY") is True
    assert _standard_in_scope(GLOBAL, "", "") is True  # even with no country


def test_us_federal_applies_in_every_state():
    assert _standard_in_scope(US_FEDERAL, "US", "NY") is True
    assert _standard_in_scope(US_FEDERAL, "US", "") is True


def test_state_standard_only_applies_in_its_own_state():
    assert _standard_in_scope(CALIFORNIA, "US", "CA") is True
    assert _standard_in_scope(CALIFORNIA, "US", "NY") is False
    assert _standard_in_scope(CALIFORNIA, "US", "") is False  # state not chosen


def test_foreign_standard_excluded():
    assert _standard_in_scope(UAE, "US", "NY") is False
    assert _standard_in_scope(UAE, "AE", "") is True  # ...but applies in the UAE


def test_region_only_standard_excluded():
    """A standard tagged to a region but with no countryCode (e.g. an EU-wide
    directive) is currently out of scope for everyone — documents the known
    gap so a future change to support it is a deliberate, test-visible one."""
    eu = _std("european_union", country=None, work=30, name="EU OSH")
    assert _standard_in_scope(eu, "DE", "") is False


# --- _scope_composite: the safety-critical selection -----------------------


def test_scope_empty_when_no_standard_covers_jurisdiction():
    """Only a foreign rule came back, so a US/NY worker has zero coverage. The
    composite must flag scopeEmpty (→ entities go unavailable) and must NOT
    silently report stopWork=False off an empty set."""
    composite = _scope_composite([UAE], "US", "NY")
    assert composite["scopeEmpty"] is True
    assert composite["stopWork"] is False  # value present but not authoritative


def test_scope_not_empty_when_covered_but_nothing_triggered():
    """Coverage exists for the jurisdiction but no standard is applicable at this
    (mild) WBGT — a legitimate 'safe' state, distinct from 'no coverage'."""
    composite = _scope_composite([_std("global", country=None, applicable=False)], "US", "NY")
    assert composite["scopeEmpty"] is False


def test_blank_country_scopes_to_global_only():
    composite = _scope_composite([GLOBAL, US_FEDERAL], "", "")
    assert composite["scopeEmpty"] is False
    assert composite["contributingStandards"] == ["ISO 7243"]
    assert "OSHA" not in composite["advisoryStandards"]


def test_in_scope_stop_work_wins_over_a_schedule():
    """Most-protective rule wins: an in-scope stop-work beats any schedule."""
    stop_here = _std("osha_us", country="US", stop=True, name="OSHA stop")
    composite = _scope_composite([GLOBAL, stop_here], "US", "NY")
    assert composite["stopWork"] is True
    assert composite["triggeredBy"] == "OSHA stop"


def test_most_protective_schedule_has_fewest_work_minutes():
    composite = _scope_composite([US_FEDERAL, CALIFORNIA], "US", "CA")
    assert composite["stopWork"] is False
    assert composite["workMinutesPerHour"] == 20  # Cal/OSHA, the stricter one
    assert composite["triggeredBy"] == "Cal/OSHA"


def test_foreign_stop_work_does_not_hijack_local_guidance():
    """The whole point of scoping: the UAE midday ban must not force a US worker
    to stop. The in-scope schedule should bind instead."""
    composite = _scope_composite([GLOBAL, UAE], "US", "NY")
    assert composite["stopWork"] is False
    assert composite["workMinutesPerHour"] == 45  # from the global standard


# --- _derive_risk_level boundaries -----------------------------------------


@pytest.mark.parametrize(
    "composite, expected",
    [
        ({"stopWork": True}, "critical"),
        ({"workMinutesPerHour": None}, "unknown"),
        ({"workMinutesPerHour": 0}, "extreme"),
        ({"workMinutesPerHour": 15}, "high"),
        ({"workMinutesPerHour": 16}, "moderate"),
        ({"workMinutesPerHour": 30}, "moderate"),
        ({"workMinutesPerHour": 31}, "low"),
        ({"workMinutesPerHour": 45}, "low"),
        ({"workMinutesPerHour": 46}, "safe"),
        ({"workMinutesPerHour": 60}, "safe"),
    ],
)
def test_derive_risk_level(composite, expected):
    assert _derive_risk_level(composite) == expected


def test_stop_work_outranks_a_present_schedule():
    # Defensive: if both flags are set, stop-work must dominate.
    assert _derive_risk_level({"stopWork": True, "workMinutesPerHour": 60}) == "critical"


# --- _is_restricted --------------------------------------------------------


@pytest.mark.parametrize(
    "data, restricted",
    [
        ({"stop_work": True, "risk_level": "safe"}, True),
        ({"stop_work": False, "risk_level": "high"}, True),
        ({"stop_work": False, "risk_level": "extreme"}, True),
        ({"stop_work": False, "risk_level": "critical"}, True),
        ({"stop_work": False, "risk_level": "moderate"}, False),
        ({"stop_work": False, "risk_level": "safe"}, False),
        ({"stop_work": False, "risk_level": "unknown"}, False),
        ({}, False),
    ],
)
def test_is_restricted(data, restricted):
    assert _is_restricted(data) is restricted


# --- entity availability: the fix, wired end-to-end ------------------------


def _coordinator(data, *, last_update_success=True):
    return SimpleNamespace(data=data, last_update_success=last_update_success)


def _entry():
    return SimpleNamespace(entry_id="test_entry")


def test_safety_sensor_unavailable_when_scope_empty():
    sensor = StopWorkBinarySensor(_coordinator({"available": False, "stop_work": False}), _entry())
    assert sensor.available is False  # not merely is_on=False


def test_safety_sensor_available_and_clear_when_safe():
    sensor = StopWorkBinarySensor(_coordinator({"available": True, "stop_work": False}), _entry())
    assert sensor.available is True
    assert sensor.is_on is False


def test_safety_sensor_on_when_stop_work():
    sensor = StopWorkBinarySensor(_coordinator({"available": True, "stop_work": True}), _entry())
    assert sensor.available is True
    assert sensor.is_on is True


def test_value_sensor_unavailable_when_scope_empty():
    sensor = WbgtSensor(_coordinator({"available": False, "wbgt_c": 28.0}), _entry())
    assert sensor.available is False


def test_entities_unavailable_when_update_failed():
    """A failed poll wins over scope: last_update_success=False → unavailable
    regardless of stale data still flagged available."""
    coord = _coordinator({"available": True, "stop_work": False}, last_update_success=False)
    assert StopWorkBinarySensor(coord, _entry()).available is False


def test_available_defaults_true_when_flag_absent():
    """Older/fallback data without the 'available' key must not knock entities
    offline — the flag defaults to available."""
    sensor = StopWorkBinarySensor(_coordinator({"stop_work": False}), _entry())
    assert sensor.available is True
