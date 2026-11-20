"""Reusable correctness checks. Each returns a list of failure strings (empty = pass).

`weak_original_check` re-implements the logic of the original Grok no-lookahead test so the
suite can show what it can and cannot detect.
"""

from __future__ import annotations

import random
from typing import Callable, List, Sequence

from trading_system.config import timeframe_seconds
from trading_system.data.ohlc import OHLCSeries
from trading_system.events import SWING_HIGH, DetectorEvent
from helpers import dicts, oracle, tuples

Run = Callable[[OHLCSeries], List[DetectorEvent]]


def random_cuts(n_bars: int, count: int, seed: int) -> List[int]:
    rng = random.Random(seed)
    return sorted(rng.sample(range(1, n_bars), min(count, n_bars - 1)))


def weak_original_check(run: Run, series: OHLCSeries, cuts: Sequence[int]) -> List[str]:
    """Original test: every prefix event must exist, unchanged, in the full run."""
    full = {(e.type, e.payload["bar_index"]): (e.payload["price"], e.confirmation_time.isoformat())
            for e in run(series)}
    failures = []
    for cut in cuts:
        for e in run(series.slice(0, cut)):
            key = (e.type, e.payload["bar_index"])
            if key not in full:
                failures.append(f"cut={cut}: {key} not in full run")
            elif abs(full[key][0] - e.payload["price"]) > 1e-9 or full[key][1] != e.confirmation_time.isoformat():
                failures.append(f"cut={cut}: {key} changed")
    return failures


def truncation_check(run: Run, series: OHLCSeries, cuts: Sequence[int]) -> List[str]:
    """Strong truncation test: output on history[:cut] must EQUAL, exactly and in order, the
    full-history events whose confirming candle lies inside the prefix. Catches spurious,
    altered, missing and re-ordered events."""
    full = run(series)
    failures = []
    for cut in cuts:
        last_bar = int(series.indices[cut - 1])
        expected = dicts([e for e in full if e.payload["confirmation_bar_index"] <= last_bar])
        got = dicts(run(series.slice(0, cut)))
        if got != expected:
            failures.append(f"cut={cut}: prefix output differs from full output restricted to confirmed-by-cut")
    return failures


def timing_check(run: Run, series: OHLCSeries, n: int) -> List[str]:
    """Events must equal the independent oracle: same swings, confirmation bar = pivot + n,
    confirmation time = CLOSE time of the confirming candle."""
    events = run(series)
    failures = []
    expected = oracle(series.highs, series.lows, n)
    if tuples(events) != expected:
        failures.append(f"swings/confirmation bars differ from oracle ({len(events)} vs {len(expected)} events)")
    tf = timeframe_seconds(series.timeframe)
    for e in events:
        conf_pos = series.position_of(e.payload["confirmation_bar_index"])
        want = series[conf_pos].time.timestamp() + tf
        if e.confirmation_time.timestamp() != want:
            failures.append(f"bar {e.payload['bar_index']}: confirmation_time is not the confirming candle's close time")
            break
    return failures


def validity_check(run: Run, series: OHLCSeries, n: int) -> List[str]:
    """Non-repainting: every emitted swing must still be a genuine n-bar fractal when judged
    with the COMPLETE future history. A swing later invalidated by newer data is a repaint."""
    failures = []
    h, l = series.highs, series.lows
    for e in run(series):
        p = series.position_of(e.payload["bar_index"])
        arr, cmp = (h, lambda a, b: a > b) if e.type == SWING_HIGH else (l, lambda a, b: a < b)
        ok = p - n >= 0 and p + n < len(arr) and all(
            cmp(arr[p], arr[p - k]) and cmp(arr[p], arr[p + k]) for k in range(1, n + 1))
        if not ok:
            failures.append(f"{e.type} at bar {e.payload['bar_index']} is not a valid fractal given full history")
    return failures
