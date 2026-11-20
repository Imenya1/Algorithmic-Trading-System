"""Fractal calculation, confirmation timing, event contract, state and input handling."""

from __future__ import annotations

import zlib
from datetime import timedelta

import pytest

from helpers import T0, make_series, oracle, tie_rich_series, tuples, walk
from trading_system.config import load_config
from trading_system.detectors.base import DetectorInputError, GapError
from trading_system.detectors.swing import SwingDetector
from trading_system.events import SWING_HIGH, SWING_LOW, DetectorEvent
from trading_system.data.ohlc import Candle
from trading_system.replay import DetectorBank, replay_series, stream_candles
from trading_system.utils.synthetic import generate_known_swings


# ---------------------------------------------------------------- A. fractal calculation
def test_known_swings_exact_values_and_close_time_confirmation():
    series, expected = generate_known_swings()
    events = replay_series(series, SwingDetector(series.symbol, series.timeframe, {"n": 2}))
    assert tuples(events) == oracle(series.highs, series.lows, 2)
    for exp in expected:
        (ev,) = [e for e in events if e.type == exp["type"] and e.payload["bar_index"] == exp["bar_index"]]
        assert ev.payload["price"] == exp["price"]
        assert ev.payload["confirmation_bar_index"] == exp["confirm_index"]
        # Build Order: confirmation time = CLOSE of the confirming candle (M5 -> open + 5 min).
        assert ev.confirmation_time == series[exp["confirm_index"]].time + timedelta(minutes=5)
        assert ev.confirmation_time > series[exp["bar_index"]].time


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("seed", range(8))
def test_matches_independent_bruteforce_oracle_on_random_walks(n, seed):
    series = walk(400, seed)
    events = replay_series(series, SwingDetector("T", "M5", {"n": n}))
    assert tuples(events) == oracle(series.highs, series.lows, n)


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("seed", range(8))
def test_matches_oracle_on_tie_rich_data(n, seed):
    """Equal highs/lows on a coarse grid: strict-inequality rule must agree with the oracle."""
    series = tie_rich_series(400, seed)
    events = replay_series(series, SwingDetector("T", "M5", {"n": n}))
    assert tuples(events) == oracle(series.highs, series.lows, n)


def test_equal_highs_do_not_form_a_pivot_documented_decision():
    series = make_series([10, 11, 15, 15, 11, 10, 9], [9, 10, 14, 14, 10, 9, 8])
    events = replay_series(series, SwingDetector("T", "M5", {"n": 2}))
    assert [e for e in events if e.type == SWING_HIGH] == []


def test_uses_high_for_swing_high_and_low_for_swing_low():
    series = make_series([10, 12, 20, 12, 10, 9, 8, 9, 10], [9, 11, 18, 11, 9, 5, 1, 5, 9])
    events = replay_series(series, SwingDetector("T", "M5", {"n": 1}))
    for e in events:
        pos = series.position_of(e.payload["bar_index"])
        assert e.payload["price"] == (series.highs[pos] if e.type == SWING_HIGH else series.lows[pos])


def test_outside_bar_emits_both_events_high_first():
    series = make_series([10, 12, 20, 12, 10], [9, 8, 1, 8, 9])
    events = replay_series(series, SwingDetector("T", "M5", {"n": 1}))
    assert [(e.type, e.payload["bar_index"]) for e in events if e.payload["bar_index"] == 2] == [
        (SWING_HIGH, 2), (SWING_LOW, 2)]


# ---------------------------------------------------------------- start / end / insufficient data
@pytest.mark.parametrize("n", [1, 2, 3])
def test_insufficient_candles_emit_nothing_without_error(n):
    series = make_series(list(range(2 * n)), [x - 1 for x in range(2 * n)])
    assert replay_series(series, SwingDetector("T", "M5", {"n": n})) == []


@pytest.mark.parametrize("n", [1, 2, 3])
def test_minimum_history_exactly_2n_plus_1_bars(n):
    highs = [1] * n + [9] + [1] * n
    lows = [0] * n + [5] + [0] * n
    series = make_series(highs, lows)
    events = replay_series(series, SwingDetector("T", "M5", {"n": n}))
    assert tuples(events) == [(SWING_HIGH, n, 2 * n)]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_dataset_boundaries_no_pivot_in_first_or_last_n_bars(n):
    series = walk(300, seed=11)
    events = replay_series(series, SwingDetector("T", "M5", {"n": n}))
    assert events
    assert all(n <= e.payload["bar_index"] <= len(series) - 1 - n for e in events)
    assert all(e.payload["confirmation_bar_index"] <= len(series) - 1 for e in events)


# ---------------------------------------------------------------- C. confirmation timing
@pytest.mark.parametrize("n", [1, 2, 3])
def test_event_is_published_on_exactly_the_confirming_step(n):
    series = walk(200, seed=5)
    det = SwingDetector("T", "M5", {"n": n})
    for candle, events in stream_candles(list(series), det):
        for e in events:
            assert e.payload["confirmation_bar_index"] == candle.index
            assert e.payload["bar_index"] == candle.index - n
            assert e.confirmation_time == candle.time + timedelta(minutes=5)
            assert e.payload["pivot_time"] == series[candle.index - n].time.isoformat()


def test_candle_time_close_convention_uses_timestamp_as_confirmation_time():
    series = make_series([1, 1, 9, 1, 1], [0, 0, 5, 0, 0])
    (ev,) = replay_series(series, SwingDetector("T", "M5", {"n": 2, "candle_time": "close"}))
    assert ev.confirmation_time == series[4].time


# ---------------------------------------------------------------- E. event contract
def test_event_contract_fields_and_roundtrip():
    series = walk(100, seed=3, symbol="V75", timeframe="M5")
    events = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
    assert events
    for e in events:
        assert e.type in (SWING_HIGH, SWING_LOW)
        assert (e.symbol, e.timeframe) == ("V75", "M5")
        assert e.confirmation_time is not None
        assert {"price", "bar_index", "timeframe"} <= set(e.payload)
        assert e.payload["timeframe"] == "M5"
        assert isinstance(e.payload["price"], float) and isinstance(e.payload["bar_index"], int)
        assert DetectorEvent.from_dict(e.to_dict()) == e


# ---------------------------------------------------------------- D. state management
def test_state_is_bounded_and_does_not_grow_with_history():
    det = SwingDetector("T", "M5", {"n": 3})
    det.process_series(list(walk(5000, seed=3)))
    assert len(det.state.window) == 7
    assert not hasattr(det.state, "confirmed_events")


def test_exact_duplicate_is_idempotent_and_counted():
    series = walk(60, seed=1)
    det = SwingDetector("T", "M5", {"n": 2})
    events = replay_series(series, det)
    _, again = det.step(series[len(series) - 1])
    assert again == [] and det.state.duplicates_ignored == 1
    assert det.state.swing_high_count + det.state.swing_low_count == len(events)


def test_conflicting_duplicate_out_of_order_and_unclosed_are_rejected_without_state_change():
    series = walk(40, seed=1)
    det = SwingDetector("T", "M5", {"n": 2})
    replay_series(series, det)
    before = det.snapshot()
    last = series[len(series) - 1]
    bad = [
        Candle(last.time, last.open, last.high + 1, last.low, last.close, index=last.index),  # conflict
        series[3],  # out of order
        Candle(last.time + timedelta(minutes=5), 1, 2, 0, 1, closed=False),  # unclosed
        Candle(last.time + timedelta(minutes=5), 1, float("nan"), 0, 1),  # NaN high
        Candle(last.time + timedelta(minutes=5), 1, 0, 2, 1),  # low > high
    ]
    for candle in bad:
        with pytest.raises(DetectorInputError):
            det.step(candle)
        assert det.snapshot() == before


def test_nan_neighbour_cannot_create_a_false_swing():
    """Regression for the original defect: NaN compared False so a NaN bar never blocked a pivot."""
    series = make_series([10, 11, 15, 12, 11, 9, 8], [9, 10, 14, 11, 10, 8, 7])
    det = SwingDetector("T", "M5", {"n": 2})
    cs = list(series)
    cs[4] = Candle(cs[4].time, 1, float("nan"), 0, 1, index=4)
    with pytest.raises(DetectorInputError):
        for c in cs:
            det.step(c)


def test_gap_policy_reset_clears_window_and_never_spans_the_gap():
    series = make_series([1, 1, 9, 1, 1, 1, 1, 1], [0, 0, 5, 0, 0, 0, 0, 0])
    cs = list(series)
    det = SwingDetector("T", "M5", {"n": 2, "gap_policy": "reset"})
    events = []
    for c in cs[:3]:
        events += det.step(c)[1]
    for c in cs[4:]:  # candle 3 missing: the 9-high pivot cannot be confirmed across the gap
        events += det.step(c)[1]
    assert events == [] and det.state.gaps_reset == 1


@pytest.mark.parametrize("bad", [0, -1, 2.5, "2", True, None])
def test_invalid_n_rejected(bad):
    with pytest.raises(ValueError):
        SwingDetector("T", "M5", {"n": bad})


def test_unknown_param_and_bad_timeframe_rejected():
    with pytest.raises(ValueError):
        SwingDetector("T", "M5", {"n": 2, "nn": 3})
    with pytest.raises(ValueError):
        SwingDetector("T", "5min", {"n": 2})


# ---------------------------------------------------------------- state isolation
def test_bank_isolates_symbols_and_timeframes_under_interleaving():
    cfg = load_config()
    bank = DetectorBank(cfg)
    feeds = {}
    for sym, tf in bank.keys():
        feeds[(sym, tf)] = list(walk(150, seed=zlib.crc32(f'{sym}|{tf}'.encode()) % 1000, symbol=sym, timeframe=tf))
    got = {k: [] for k in feeds}
    for i in range(150):  # interleave candle by candle across all six streams
        for k, cs in feeds.items():
            got[k] += bank.on_candle(k[0], k[1], cs[i])
    for (sym, tf), cs in feeds.items():
        solo = DetectorBank(cfg).detector(sym, tf)
        solo_events = solo.process_series(cs)[1]
        assert [e.to_dict() for e in got[(sym, tf)]] == [e.to_dict() for e in solo_events]
        assert all((e.symbol, e.timeframe) == (sym, tf) for e in got[(sym, tf)])
