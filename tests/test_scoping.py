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
    _apply_safety_floor,
    _derive_risk_level,
    _is_restricted,
    _scope_composite,
    _single_standard_composite,
    _standard_in_scope,
)
from custom_components.heat_stress_guidance.binary_sensor import StopWorkBinarySensor
from custom_components.heat_stress_guidance.sensor import WbgtSensor


# --- helpers ---------------------------------------------------------------


def _std(jurisdiction, *, country=None, applicable=True, stop=False, work=None, name="std", standard_id=None):
    """Build one API ``results[]`` entry."""
    rec = {"stopWork": stop}
    if work is not None:
        rec["workMinutesPerHour"] = work
        rec["restMinutesPerHour"] = 60 - work
    return {
        "standardId": standard_id,
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


# --- single-standard selection + the pinned-standard safety floor ----------
#
# A worker can pin one standard (default LIN) so guidance reflects that
# standard's schedule. These guard the two safety invariants: (1) a pinned
# standard that isn't in the response yields *no* guidance (unavailable), never
# a silent "safe"; (2) the safety floor means a pinned standard can never
# suppress a legally-binding in-scope stop-work.


LIN_ID = "la_isla_network_rshs"
LIN = _std("global", country=None, work=20, name="LIN", standard_id=LIN_ID)


def test_single_standard_uses_only_the_chosen_standard():
    composite = _single_standard_composite([LIN, US_FEDERAL, CALIFORNIA], LIN_ID)
    assert composite["scopeEmpty"] is False
    assert composite["workMinutesPerHour"] == 20  # LIN's schedule, not the strictest
    assert composite["contributingStandards"] == ["LIN"]
    assert composite["triggeredBy"] == "LIN"


def test_single_standard_absent_is_scope_empty():
    """The chosen standard isn't in the response at all → no guidance to give,
    so entities go unavailable rather than reading a silent 'safe'."""
    composite = _single_standard_composite([US_FEDERAL], LIN_ID)
    assert composite["scopeEmpty"] is True
    assert composite["stopWork"] is False  # present but not authoritative


def test_single_standard_present_but_not_applicable_is_covered_safe():
    """Present but not applicable at this mild WBGT is a legitimate covered-safe
    state — available, no schedule — distinct from 'no coverage'."""
    mild = _std("global", country=None, applicable=False, name="LIN", standard_id=LIN_ID)
    composite = _single_standard_composite([mild], LIN_ID)
    assert composite["scopeEmpty"] is False
    assert composite["workMinutesPerHour"] is None


def test_single_standard_honors_its_own_stop_work():
    stop = _std("global", country=None, stop=True, name="LIN", standard_id=LIN_ID)
    composite = _single_standard_composite([stop], LIN_ID)
    assert composite["stopWork"] is True
    assert composite["triggeredBy"] == "LIN"


def test_safety_floor_forces_stop_work_from_a_binding_local_rule():
    """The whole point of the floor: a worker pinned LIN (work/rest), but a
    legally-binding in-scope rule (e.g. a midday ban) requires stop-work — the
    alert must still fire, labelled with the rule that triggered it."""
    pinned = _single_standard_composite([LIN, UAE], LIN_ID)  # LIN says work 20
    assert pinned["stopWork"] is False
    floored = _apply_safety_floor(pinned, _scope_composite([LIN, UAE], "AE", ""))
    assert floored["stopWork"] is True
    assert floored["triggeredBy"] == "UAE midday ban"
    assert "LIN" in floored["advisoryStandards"]  # chosen standard kept as advisory


def test_safety_floor_does_not_fire_for_a_foreign_stop_work():
    """A stop-work from a standard *outside* the worker's jurisdiction must not
    be floored in — that would re-introduce the cross-border hijack scoping
    exists to prevent."""
    pinned = _single_standard_composite([LIN, UAE], LIN_ID)
    floored = _apply_safety_floor(pinned, _scope_composite([LIN, UAE], "US", "NY"))
    assert floored["stopWork"] is False
    assert floored["workMinutesPerHour"] == 20  # LIN's schedule survives


def test_safety_floor_keeps_pinned_stop_work():
    pinned = _single_standard_composite(
        [_std("global", country=None, stop=True, name="LIN", standard_id=LIN_ID)], LIN_ID
    )
    floored = _apply_safety_floor(pinned, {"stopWork": False})
    assert floored["stopWork"] is True
    assert floored["triggeredBy"] == "LIN"


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


def test_alert_threshold_aligns_with_risk_tiers():
    """WHY: ALERT_RISK_LEVELS gates whether a phone push fires, and
    _derive_risk_level buckets work-minutes into those tiers. Pin the seam at the
    documented ≤15 boundary so moving either the cutoff or the alert set across
    that line fails here instead of silently dropping (or spamming) alerts."""
    from custom_components.heat_stress_guidance.const import ALERT_RISK_LEVELS

    assert _derive_risk_level({"workMinutesPerHour": 15}) in ALERT_RISK_LEVELS
    assert _derive_risk_level({"workMinutesPerHour": 16}) not in ALERT_RISK_LEVELS
    assert _derive_risk_level({"workMinutesPerHour": 0}) in ALERT_RISK_LEVELS  # extreme
    assert _derive_risk_level({"stopWork": True}) in ALERT_RISK_LEVELS  # critical


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
