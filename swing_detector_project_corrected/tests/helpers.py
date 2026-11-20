"""Shared test helpers: series construction, an independent brute-force oracle, comparisons."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

import numpy as np

from trading_system.config import timeframe_seconds
from trading_system.data.ohlc import Candle, OHLCSeries
from trading_system.events import SWING_HIGH, SWING_LOW, DetectorEvent
from trading_system.utils.synthetic import generate_random_walk

T0 = datetime(2024, 1, 1, 0, 0, 0)


def make_series(highs: Sequence[float], lows: Sequence[float], symbol="T", timeframe="M5",
                start: datetime = T0) -> OHLCSeries:
    step = timedelta(seconds=timeframe_seconds(timeframe))
    candles = []
    for i, (h, l) in enumerate(zip(highs, lows)):
        mid = (h + l) / 2
        candles.append(Candle(start + i * step, mid, float(h), float(l), mid))
    return OHLCSeries(candles, symbol, timeframe)


def tie_rich_series(n_bars: int, seed: int, symbol="T", timeframe="M5") -> OHLCSeries:
    """Highs/lows on a coarse integer grid so equal highs/lows are frequent."""
    rng = np.random.default_rng(seed)
    base = np.cumsum(rng.integers(-2, 3, n_bars)) + 100
    highs = base + rng.integers(0, 3, n_bars)
    lows = base - rng.integers(0, 3, n_bars)
    return make_series(highs, lows, symbol, timeframe)


def walk(n_bars: int, seed: int, symbol="T", timeframe="M5") -> OHLCSeries:
    minutes = timeframe_seconds(timeframe) // 60
    return generate_random_walk(n_bars, seed=seed, symbol=symbol, timeframe=timeframe,
                                timeframe_minutes=minutes)


def oracle(highs: Sequence[float], lows: Sequence[float], n: int) -> List[Tuple[str, int, int]]:
    """Independent brute-force n-bar fractal: (type, pivot index, confirmation index),
    ordered by confirmation index, SWING_HIGH before SWING_LOW on the same bar."""
    out = []
    for p in range(n, len(highs) - n):
        if all(highs[p] > highs[p - k] and highs[p] > highs[p + k] for k in range(1, n + 1)):
            out.append((SWING_HIGH, p, p + n))
        if all(lows[p] < lows[p - k] and lows[p] < lows[p + k] for k in range(1, n + 1)):
            out.append((SWING_LOW, p, p + n))
    return sorted(out, key=lambda x: (x[2], x[0] != SWING_HIGH))


def tuples(events: Sequence[DetectorEvent]) -> List[Tuple[str, int, int]]:
    return [(e.type, e.payload["bar_index"], e.payload["confirmation_bar_index"]) for e in events]


def dicts(events: Sequence[DetectorEvent]) -> List[dict]:
    return [e.to_dict() for e in events]
