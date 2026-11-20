"""Deliberately defective swing implementations ("mutants") used to prove the test suite
can actually detect the defects the Build Order cares about.

Each mutant exposes run(series) -> events. They are TEST FIXTURES, never used by the system.
"""

from __future__ import annotations

from typing import List

from trading_system.config import timeframe_seconds
from trading_system.data.ohlc import OHLCSeries, candle_close_time
from trading_system.detectors.swing import SwingDetector
from trading_system.events import SWING_HIGH, SWING_LOW, DetectorEvent, make_swing_event
from trading_system.replay import replay_series


def correct(n: int):
    def run(series: OHLCSeries) -> List[DetectorEvent]:
        return replay_series(series, SwingDetector(series.symbol, series.timeframe, {"n": n}))
    return run


def _emit(series: OHLCSeries, kind: str, pivot_pos: int, conf_pos: int, convention="open"):
    tf = timeframe_seconds(series.timeframe)
    price = series.highs[pivot_pos] if kind == SWING_HIGH else series.lows[pivot_pos]
    return make_swing_event(kind, series.symbol, series.timeframe,
                            candle_close_time(series[conf_pos].time, tf, convention), price,
                            series[pivot_pos].index, series[pivot_pos].time, series[conf_pos].index)


def early_confirm_repaints(n: int):
    """Confirms after only n-1 right-hand bars: emits too early and is later invalidated
    when bar p+n exceeds the pivot (a repainting detector). Deterministic per prefix."""
    def run(series: OHLCSeries) -> List[DetectorEvent]:
        h, l, out = series.highs, series.lows, []
        r = n - 1
        for p in range(n, len(h) - r):
            if all(h[p] > h[p - k] for k in range(1, n + 1)) and all(h[p] > h[p + k] for k in range(1, r + 1)):
                out.append((p + r, 0, _emit(series, SWING_HIGH, p, p + r)))
            if all(l[p] < l[p - k] for k in range(1, n + 1)) and all(l[p] < l[p + k] for k in range(1, r + 1)):
                out.append((p + r, 1, _emit(series, SWING_LOW, p, p + r)))
        return [e for _, _, e in sorted(out, key=lambda x: (x[0], x[1]))]
    return run


def one_bar_late(n: int):
    """Correct pivots, but published one candle after the confirmation candle."""
    def run(series: OHLCSeries) -> List[DetectorEvent]:
        h, l, out = series.highs, series.lows, []
        for p in range(n, len(h) - n - 1):
            if all(h[p] > h[p - k] and h[p] > h[p + k] for k in range(1, n + 1)):
                out.append((p + n + 1, 0, _emit(series, SWING_HIGH, p, p + n + 1)))
            if all(l[p] < l[p - k] and l[p] < l[p + k] for k in range(1, n + 1)):
                out.append((p + n + 1, 1, _emit(series, SWING_LOW, p, p + n + 1)))
        return [e for _, _, e in sorted(out, key=lambda x: (x[0], x[1]))]
    return run


def leaky_tail_batch(n: int):
    """Whole-series centred fractal that evaluates partial right windows at the end of the
    input (like rolling(center=True, min_periods=1)). Leaks the data boundary into results."""
    def run(series: OHLCSeries) -> List[DetectorEvent]:
        h, l, out, last = series.highs, series.lows, [], len(series) - 1
        for p in range(n, len(h)):
            right = range(1, min(n, last - p) + 1)
            conf = min(p + n, last)
            if all(h[p] > h[p - k] for k in range(1, n + 1)) and all(h[p] > h[p + k] for k in right):
                out.append((conf, 0, _emit(series, SWING_HIGH, p, conf)))
            if all(l[p] < l[p - k] for k in range(1, n + 1)) and all(l[p] < l[p + k] for k in right):
                out.append((conf, 1, _emit(series, SWING_LOW, p, conf)))
        return [e for _, _, e in sorted(out, key=lambda x: (x[0], x[1]))]
    return run


def open_time_confirmation(n: int):
    """Correct pivots and timing, but stamps confirmation_time with the confirming candle's
    OPEN time instead of its close time (the defect found in the original implementation)."""
    def run(series: OHLCSeries) -> List[DetectorEvent]:
        return replay_series(series, SwingDetector(series.symbol, series.timeframe,
                                                   {"n": n, "candle_time": "close"}))
    return run
