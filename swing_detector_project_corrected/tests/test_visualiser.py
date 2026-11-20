"""Chart visualiser: markers must sit on the right pivot candles, show only confirmed swings,
and mirror the detector's output exactly."""

from __future__ import annotations

from datetime import timedelta

import pytest

from helpers import walk
from trading_system.detectors.swing import SwingDetector
from trading_system.events import SWING_HIGH
from trading_system.replay import replay_series
from trading_system.visualiser import compute_markers, plot_swings, select_window


@pytest.fixture(scope="module")
def data():
    series = walk(500, 2026, "V75", "M5")
    return series, replay_series(series, SwingDetector("V75", "M5", {"n": 2}))


def test_default_window_markers_sit_on_the_correct_candles(data):
    """Regression for the original defect: after slicing, bar indices were renumbered from 0 so
    all 48 drawn markers sat on the wrong candles."""
    series, events = data
    view = select_window(series, None, max_bars=180)
    assert (int(view.indices[0]), int(view.indices[-1])) == (320, 499)
    markers, hidden = compute_markers(view, events)
    inside = [e for e in events if 320 <= e.payload["bar_index"] and e.payload["confirmation_bar_index"] <= 499]
    assert len(markers) == len(inside) > 20
    for m in markers:
        assert view[m.x].index == m.bar_index
        assert m.price == (view.highs[m.x] if m.event_type == SWING_HIGH else view.lows[m.x])
        assert m.confirm_x - m.x == 2  # n = 2: confirmation tick is two candles after the pivot


def test_marker_set_equals_detector_events_for_every_window(data):
    series, events = data
    for first, last in [(0, 99), (100, 250), (301, 420), (400, 499)]:
        view = select_window(series, (first, last))
        markers, hidden = compute_markers(view, events)
        want = {(e.type, e.payload["bar_index"], e.payload["price"]) for e in events
                if first <= e.payload["bar_index"] <= last and e.payload["confirmation_bar_index"] <= last}
        assert {(m.event_type, m.bar_index, m.price) for m in markers} == want
        # pivots inside the window whose confirmation falls after its end are hidden, not drawn
        late = [e for e in events if first <= e.payload["bar_index"] <= last
                and e.payload["confirmation_bar_index"] > last]
        assert hidden == len(late) <= 4


def test_unconfirmed_swings_are_not_displayed_before_their_confirmation_candle(data):
    series, events = data
    pivot = next(e for e in events if e.payload["bar_index"] > 50)
    p, c = pivot.payload["bar_index"], pivot.payload["confirmation_bar_index"]
    assert c == p + 2
    # window ends one candle before confirmation: swing must be hidden
    markers, hidden = compute_markers(select_window(series, (p - 30, c - 1)), [pivot])
    assert markers == [] and hidden == 1
    # window ends on the confirming candle: swing becomes visible
    markers, hidden = compute_markers(select_window(series, (p - 30, c)), [pivot])
    assert len(markers) == 1 and hidden == 0


def test_as_of_time_hides_swings_confirmed_later(data):
    series, events = data
    pivot = next(e for e in events if e.payload["bar_index"] > 50)
    view = select_window(series, (0, 200))
    assert compute_markers(view, [pivot], as_of=pivot.confirmation_time)[0]
    assert compute_markers(view, [pivot], as_of=pivot.confirmation_time - timedelta(seconds=1))[0] == []


def test_foreign_symbol_timeframe_or_mismatched_price_raises(data):
    series, events = data
    other = replay_series(walk(100, 1, "V100", "M3"), SwingDetector("V100", "M3", {"n": 2}))
    with pytest.raises(ValueError, match="cannot be drawn"):
        compute_markers(select_window(series, (0, 99)), other)
    tampered = type(events[0]).from_dict({**events[0].to_dict(),
                                          "payload": {**events[0].payload, "price": 1.0}})
    with pytest.raises(ValueError, match="does not match the series"):
        compute_markers(select_window(series, (0, 99)), [tampered])


def test_window_outside_series_rejected_and_empty_series_rejected(data):
    series, events = data
    with pytest.raises(ValueError):
        select_window(series, (400, 9999))
    with pytest.raises(ValueError):
        select_window(series, (300, 200))


def test_chart_file_is_written_for_default_and_historical_windows(data, tmp_path):
    series, events = data
    for kw in ({}, {"window": (100, 220)}):
        path = plot_swings(series, events, save_path=tmp_path / f"c{len(kw)}.png", dpi=60, **kw)
        assert path.exists() and path.stat().st_size > 5_000
