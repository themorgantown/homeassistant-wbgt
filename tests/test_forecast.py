"""Forecast-peak selection, closest-hour matching, and the WBGT estimate.

Pure helpers, no network. The forecast tests pin the 24-hour horizon window and
the tolerance to malformed upstream entries (the API is third-party and can
return partial rows); the WBGT tests pin the indoor formula and its globe-sensor
fallback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.heat_stress_guidance.coordinator import (
    _closest_hour_entry,
    _estimate_wbgt,
    _forecast_peak,
    _stull_wet_bulb,
)

NOW = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)


def _hour(date, time, value):
    return {"date": date, "time": time, "valueC": value}


# --- _forecast_peak --------------------------------------------------------


def test_forecast_peak_picks_highest_within_horizon():
    hourly = [
        _hour("2026-06-23", "13:00", 26.0),
        _hour("2026-06-23", "16:00", 31.5),  # the peak
        _hour("2026-06-23", "20:00", 28.0),
    ]
    peak = _forecast_peak(hourly, "UTC", NOW)
    assert peak["valueC"] == 31.5
    assert peak["when"] == datetime(2026, 6, 23, 16, 0, tzinfo=timezone.utc)


def test_forecast_peak_ignores_past_hours():
    hourly = [
        _hour("2026-06-23", "06:00", 40.0),  # hotter, but in the past
        _hour("2026-06-23", "15:00", 30.0),
    ]
    peak = _forecast_peak(hourly, "UTC", NOW)
    assert peak["valueC"] == 30.0


def test_forecast_peak_ignores_hours_beyond_24h():
    hourly = [
        _hour("2026-06-23", "15:00", 29.0),
        _hour("2026-06-25", "15:00", 45.0),  # >24h out, must be ignored
    ]
    peak = _forecast_peak(hourly, "UTC", NOW)
    assert peak["valueC"] == 29.0


def test_forecast_peak_skips_malformed_and_null_entries():
    hourly = [
        {"date": "2026-06-23", "time": "bad", "valueC": 99.0},  # unparseable time
        {"time": "15:00", "valueC": 50.0},                       # missing date
        _hour("2026-06-23", "15:00", None),                      # null value
        _hour("2026-06-23", "17:00", 27.0),                      # the only valid row
    ]
    peak = _forecast_peak(hourly, "UTC", NOW)
    assert peak["valueC"] == 27.0


def test_forecast_peak_empty_returns_none():
    assert _forecast_peak([], "UTC", NOW) is None


def test_forecast_peak_no_upcoming_hours_returns_none():
    past_only = [_hour("2026-06-23", "06:00", 35.0)]
    assert _forecast_peak(past_only, "UTC", NOW) is None


def test_forecast_peak_falls_back_to_default_tz_for_unknown_zone():
    # Unknown tz name → helper falls back to the default zone rather than crash.
    hourly = [_hour("2026-06-23", "15:00", 28.0)]
    peak = _forecast_peak(hourly, "Not/AZone", NOW)
    assert peak is not None and peak["valueC"] == 28.0


# --- _closest_hour_entry ---------------------------------------------------


def test_closest_hour_entry_picks_nearest_time():
    now = datetime(2026, 6, 23, 12, 20, tzinfo=timezone.utc)
    hourly = [
        _hour("2026-06-23", "11:00", 24.0),
        _hour("2026-06-23", "12:00", 26.0),  # nearest to 12:20
        _hour("2026-06-23", "14:00", 30.0),
    ]
    assert _closest_hour_entry(hourly, "UTC", now)["valueC"] == 26.0


def test_closest_hour_entry_skips_malformed_times():
    now = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
    hourly = [
        _hour("2026-06-23", "oops", 99.0),
        _hour("2026-06-23", "12:00", 26.0),
    ]
    assert _closest_hour_entry(hourly, "UTC", now)["valueC"] == 26.0


def test_closest_hour_entry_empty_returns_none():
    assert _closest_hour_entry([], "UTC", datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)) is None


def test_closest_hour_entry_does_not_pick_wrong_day():
    """WHY: this value is the live "current WBGT" that drives the stop-work
    sensor and alerts. Matching bare clock time (ignoring the date) could pick a
    clock-identical row on the wrong day and report a cooler value — masking a
    real stop-work. Anchoring to date+time must pick the truly nearest instant."""
    now = datetime(2026, 6, 23, 23, 30, tzinfo=timezone.utc)
    hourly = [
        _hour("2026-06-24", "23:00", 40.0),  # clock-close to 23:30, but ~23.5h away
        _hour("2026-06-24", "00:00", 26.0),  # the true nearest instant (30 min out)
    ]
    assert _closest_hour_entry(hourly, "UTC", now)["valueC"] == 26.0


def test_closest_hour_entry_uses_forecast_timezone():
    """WHY: in tracked_entity mode the worker can be in another timezone. ``now``
    is UTC but forecast times are in the location tz, so the match must compare
    real instants, not naive clock minutes (which would pick the wrong hour by
    the inter-zone offset)."""
    now = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)  # = 08:00 America/New_York (EDT)
    hourly = [
        _hour("2026-06-23", "08:00", 26.0),  # 12:00 UTC — the true nearest
        _hour("2026-06-23", "12:00", 40.0),  # 16:00 UTC — clock-closest but 4h away
    ]
    assert _closest_hour_entry(hourly, "America/New_York", now)["valueC"] == 26.0


# --- _estimate_wbgt / _stull_wet_bulb --------------------------------------


def test_estimate_wbgt_uses_globe_when_present():
    """Indoor WBGT = 0.7·Tnwb + 0.3·Tg. A hotter globe temp must raise WBGT
    versus the dry-bulb fallback at identical temperature/humidity."""
    t, rh = 30.0, 50.0
    with_globe = _estimate_wbgt(t, rh, globe_c=45.0)
    fallback = _estimate_wbgt(t, rh, globe_c=None)
    assert with_globe > fallback


def test_estimate_wbgt_fallback_equals_globe_eq_drybulb():
    t, rh = 30.0, 50.0
    assert _estimate_wbgt(t, rh, None) == _estimate_wbgt(t, rh, globe_c=t)


def test_estimate_wbgt_is_rounded_to_one_decimal():
    value = _estimate_wbgt(28.3, 61.0, None)
    assert value == round(value, 1)


def test_wet_bulb_below_dry_bulb_when_not_saturated():
    # Physical sanity: the wet-bulb temperature is below dry-bulb below 100% RH.
    assert _stull_wet_bulb(30.0, 50.0) < 30.0
