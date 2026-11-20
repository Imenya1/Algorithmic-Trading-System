"""Basic unit tests for the Swing Detector."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running tests without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_system.detectors.swing import SwingDetector
from trading_system.utils.synthetic import generate_known_swings, generate_random_walk
from trading_system.replay import replay_series, stream_candles
from trading_system.events import DetectorEvent


def test_known_swings_n2():
    series, expected = generate_known_swings()
    det = SwingDetector(symbol=series.symbol, timeframe=series.timeframe, params={"n": 2})
    events = replay_series(series, det)

    high_events = [e for e in events if e.type == "SWING_HIGH"]
    low_events = [e for e in events if e.type == "SWING_LOW"]

    # At least the hand-constructed swings should appear
    assert len(high_events) >= 1, "Expected at least one SWING_HIGH"
    assert len(low_events) >= 1, "Expected at least one SWING_LOW"

    # Check the primary swing high at bar 5
    pivot5 = [e for e in high_events if e.payload["bar_index"] == 5]
    assert len(pivot5) == 1
    assert abs(pivot5[0].payload["price"] - 1035.0) < 1e-6

    # Confirmation must be after the pivot
    assert pivot5[0].confirmation_time > series[5].time


def test_no_repaint_on_extension():
    """Confirmed events from a prefix must remain identical when more bars are added."""
    series = generate_random_walk(n_bars=200, seed=99, symbol="T", timeframe="M5")
    det = SwingDetector(symbol="T", timeframe="M5", params={"n": 3})

    # First half
    half = series.slice(0, 100)
    events_half = replay_series(half, det)

    # Full series
    det2 = SwingDetector(symbol="T", timeframe="M5", params={"n": 3})
    events_full = replay_series(series, det2)

    # All events that were confirmed in the first half must appear unchanged
    half_keys = {
        (e.type, e.payload["bar_index"], e.payload["price"], e.confirmation_time.isoformat())
        for e in events_half
    }
    full_keys = {
        (e.type, e.payload["bar_index"], e.payload["price"], e.confirmation_time.isoformat())
        for e in events_full
    }
    assert half_keys.issubset(full_keys), "Previously confirmed swings were altered or lost"


def test_batch_equals_streaming():
    series = generate_random_walk(n_bars=150, seed=7, symbol="T", timeframe="M5")
    det_batch = SwingDetector(symbol="T", timeframe="M5", params={"n": 2})
    events_batch = replay_series(series, det_batch)

    det_stream = SwingDetector(symbol="T", timeframe="M5", params={"n": 2})
    events_stream = []
    for _, evs in stream_candles(list(series), det_stream):
        events_stream.extend(evs)

    assert len(events_batch) == len(events_stream)
    for a, b in zip(events_batch, events_stream):
        assert a.type == b.type
        assert a.payload["bar_index"] == b.payload["bar_index"]
        assert abs(a.payload["price"] - b.payload["price"]) < 1e-9
        assert a.confirmation_time == b.confirmation_time


def test_determinism():
    series = generate_random_walk(n_bars=120, seed=42, symbol="T", timeframe="M5")
    results = []
    for _ in range(3):
        det = SwingDetector(symbol="T", timeframe="M5", params={"n": 3})
        events = replay_series(series, det)
        key = [
            (e.type, e.payload["bar_index"], e.payload["price"], e.confirmation_time.isoformat())
            for e in events
        ]
        results.append(key)
    assert results[0] == results[1] == results[2]


def test_state_isolation():
    """Different symbols must not share state."""
    s1 = generate_random_walk(n_bars=80, seed=1, symbol="V75", timeframe="H1")
    s2 = generate_random_walk(n_bars=80, seed=2, symbol="V100", timeframe="M15")

    d1 = SwingDetector(symbol="V75", timeframe="H1", params={"n": 3})
    d2 = SwingDetector(symbol="V100", timeframe="M15", params={"n": 3})

    e1 = replay_series(s1, d1)
    e2 = replay_series(s2, d2)

    assert all(e.symbol == "V75" for e in e1)
    assert all(e.symbol == "V100" for e in e2)
    # Different data → different event sets almost surely
    assert len(e1) != len(e2) or e1[0].payload["price"] != e2[0].payload["price"]
