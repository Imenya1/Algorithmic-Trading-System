"""Regression snapshots, throughput check, characterisation metrics, review set, manifest."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from helpers import walk
from trading_system.config import config_hash, load_config
from trading_system.detectors.swing import SwingDetector
from trading_system.events import DetectorEvent
from trading_system.metrics import NOT_COMPUTED, characterise
from trading_system.replay import (RunManifest, check_throughput_regression, compare_regression_snapshot,
                                   measure_throughput, replay_series, save_regression_snapshot)
from trading_system.review_set import build_review_set


@pytest.fixture(scope="module")
def run():
    series = walk(600, 2026, "V75", "M5")
    return series, replay_series(series, SwingDetector("V75", "M5", {"n": 2}))


# ------------------------------------------------------------------ regression snapshot
def test_snapshot_identical_output_passes_and_changes_are_detected(run, tmp_path):
    series, events = run
    path = tmp_path / "snap.json"
    h1 = save_regression_snapshot(events, path)
    assert compare_regression_snapshot(events, path) == []
    assert save_regression_snapshot(events, tmp_path / "again.json") == h1  # stable hash

    changed_price = list(events)
    d = changed_price[3].to_dict()
    d["payload"]["price"] += 0.01
    changed_price[3] = DetectorEvent.from_dict(d)
    assert any("event 3 differs" in x for x in compare_regression_snapshot(changed_price, path))

    assert any("count changed" in x for x in compare_regression_snapshot(events[:-1], path))
    other_n = replay_series(series, SwingDetector("V75", "M5", {"n": 3}))
    assert compare_regression_snapshot(other_n, path)  # a parameter/algorithm change is caught


# ------------------------------------------------------------------ throughput
def test_throughput_measurement_and_regression_rule(run):
    series, _ = run
    bench = measure_throughput(series, SwingDetector("V75", "M5", {"n": 2}), repeats=3)
    assert bench["candles"] == 600 and bench["median_candles_per_sec"] > 0
    assert check_throughput_regression(bench, None)["status"] == "BASELINE_RECORDED"
    base = dict(bench)
    assert check_throughput_regression(bench, base)["status"] == "PASS"
    slow = {**bench, "median_candles_per_sec": bench["median_candles_per_sec"] * 0.79}
    assert check_throughput_regression(slow, base)["status"] == "FAIL"      # > 20 % fall fails the build
    ok = {**bench, "median_candles_per_sec": bench["median_candles_per_sec"] * 0.81}
    assert check_throughput_regression(ok, base)["status"] == "PASS"


# ------------------------------------------------------------------ characterisation metrics
def test_metrics_per_symbol_timeframe_year(tmp_path):
    from trading_system.utils.synthetic import generate_random_walk
    series = generate_random_walk(3 * 24 * 12 * 40, seed=4, symbol="V75", timeframe="M5",
                                  start_time=datetime(2023, 12, 1))  # spans 2023 and 2024
    events = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
    rows = characterise(series, events)
    assert [r["year"] for r in rows] == [2023, 2024]
    assert sum(r["bars"] for r in rows) == len(series)
    assert sum(r["swing_highs"] + r["swing_lows"] for r in rows) == len(events)
    for r in rows:
        assert (r["symbol"], r["timeframe"]) == ("V75", "M5")
        assert r["confirmation_lag_bars_min"] == r["confirmation_lag_bars_max"] == 2  # measured, not assumed
        assert 0 < r["swing_density"] < 1 and r["leg_median"] > 0 and r["leg_p10"] <= r["leg_p90"]
        for key in NOT_COMPUTED:  # undefined metrics are explicitly absent, never fabricated
            assert r[key] is None


def test_metrics_reject_events_from_another_stream(run):
    series, events = run
    with pytest.raises(ValueError):
        characterise(walk(100, 1, "V100", "M3"), events)


# ------------------------------------------------------------------ review set
def test_review_set_is_deterministic_seeded_and_never_touches_data_after_review_until():
    series = walk(1500, 5, "V75", "M5")
    until = series[1000].time
    a = build_review_set(series, until, seed=7, cut_points=[100, 400, 1400])
    b = build_review_set(series, until, seed=7, cut_points=[100, 400, 1400])
    c = build_review_set(series, until, seed=8, cut_points=[100, 400, 1400])
    assert a == b and a != c
    assert max(w["last_bar"] for w in a["windows"]) <= 1000
    kinds = {w["kind"] for w in a["windows"]}
    assert {"largest_range_bar", "year_first_bars", "year_last_bars", "cut_point", "random"} <= kinds
    assert not any(w["kind"] == "cut_point" and w["centre_bar"] == 1400 for w in a["windows"])  # beyond review_until
    assert a["largest_disagreement_windows"].startswith("pending")
    with pytest.raises(ValueError):
        build_review_set(series, series[0].time - timedelta(days=1), seed=1)


# ------------------------------------------------------------------ manifest / sign-off
def _manifest():
    return RunManifest("v", "d", config_hash(load_config()), 1, ("a", "b"), "V75", "M5", {"n": 2})


def test_visual_signoff_is_pending_until_a_human_records_one():
    m = _manifest()
    assert m.to_dict()["visual_signoff"] is None and m.visual_review_status.startswith("pending")
    with pytest.raises(ValueError):
        m.record_visual_signoff("", "2026-11-18", [1], [], "accepted")
    with pytest.raises(ValueError):
        m.record_visual_signoff("dev", "2026-11-18", [1], [], "looks good")
    with pytest.raises(ValueError):
        m.record_visual_signoff("dev", "2026-11-18", [1], [], "accepted with defects logged")
    m.record_visual_signoff("dev", "2026-11-18", [{"first_bar": 0, "last_bar": 99}], [], "accepted")
    out = json.loads(json.dumps(m.to_dict()))
    assert out["visual_signoff"]["decision"] == "accepted" and out["visual_review_status"] == "signed off: accepted"


def test_waiver_is_recorded_as_waived_not_passed_and_needs_all_fields():
    m = _manifest()
    with pytest.raises(ValueError):
        m.record_waiver("Yearly stability", "", "2026-11-19", "reason")
    m.record_waiver("Yearly stability", "Owner", "2026-11-19", "only 90 days of data")
    w = json.loads(json.dumps(m.to_dict()))["waivers"][0]
    assert w["status"] == "WAIVED (not passed)" and w["requirement"] == "Yearly stability"
