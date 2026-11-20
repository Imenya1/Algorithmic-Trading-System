"""Synthetic OHLC data generators for software testing.

All data produced here is clearly labelled synthetic test data.
It is NOT real market data and must not be treated as such.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import numpy as np

from ..data.ohlc import Candle, OHLCSeries


def generate_random_walk(
    n_bars: int,
    start_price: float = 1000.0,
    volatility: float = 0.01,
    timeframe_minutes: int = 5,
    start_time: Optional[datetime] = None,
    seed: Optional[int] = 42,
    symbol: str = "SYNTH",
    timeframe: str = "M5",
) -> OHLCSeries:
    """
    Constant-volatility geometric random walk OHLC series.

    Synthetic test data only.
    """
    rng = np.random.default_rng(seed)
    if start_time is None:
        start_time = datetime(2024, 1, 1, 0, 0, 0)

    candles: List[Candle] = []
    price = start_price
    dt = timedelta(minutes=timeframe_minutes)

    for i in range(n_bars):
        # Log-return
        ret = rng.normal(0.0, volatility)
        open_p = price
        close_p = price * np.exp(ret)
        # Intrabar high/low noise
        wick = abs(rng.normal(0.0, volatility * 0.5)) * price
        high_p = max(open_p, close_p) + wick
        low_p = min(open_p, close_p) - wick
        t = start_time + i * dt
        candles.append(
            Candle(
                time=t,
                open=float(open_p),
                high=float(high_p),
                low=float(low_p),
                close=float(close_p),
                volume=float(rng.uniform(10, 100)),
                index=i,
            )
        )
        price = close_p

    return OHLCSeries(candles, symbol=symbol, timeframe=timeframe, validate=True)


def generate_known_swings(
    seed: int = 7,
) -> Tuple[OHLCSeries, List[dict]]:
    """
    Produce a short synthetic series with known, hand-verifiable swing points.

    Returns (series, list of expected swing descriptions).
    Expected swings are the pivots that a classic 2-bar fractal should detect
    under strict inequality.
    """
    # Construct a clear pattern:
    # indices: 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
    # We will force a swing high at index 5 and a swing low at index 10
    # for n=2.
    base = 1000.0
    highs = [
        1005, 1010, 1015, 1020, 1025, 1035,  # 5 is local max
        1028, 1020, 1015, 1010, 1000,        # 10 is local min
        1005, 1010, 1015, 1020, 1025,
    ]
    lows = [h - 8 for h in highs]
    opens = [(h + l) / 2 for h, l in zip(highs, lows)]
    closes = [o + 1 for o in opens]

    start = datetime(2024, 6, 1, 10, 0, 0)
    candles = []
    for i in range(len(highs)):
        candles.append(
            Candle(
                time=start + timedelta(minutes=5 * i),
                open=float(opens[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                close=float(closes[i]),
                volume=50.0,
                index=i,
            )
        )

    series = OHLCSeries(candles, symbol="SYNTH_KNOWN", timeframe="M5", validate=True)

    # For n=2 the first evaluable pivot is at index 2 (needs 0,1 left and 3,4 right)
    # We document the expected confirmed swings for the test suite.
    expected = [
        # After enough bars the pivot at 5 should become a SWING_HIGH
        # confirmation occurs when the candle at index 5+2=7 closes
        {
            "type": "SWING_HIGH",
            "bar_index": 5,
            "price": 1035.0,
            "confirm_index": 7,
        },
        {
            "type": "SWING_LOW",
            "bar_index": 10,
            "price": 992.0,  # 1000 - 8
            "confirm_index": 12,
        },
    ]
    return series, expected


def generate_constant_volatility_baseline(
    n_bars: int,
    nominal_vol: float,  # 0.50, 0.75, 1.00
    timeframe_minutes: int = 5,
    seed: int = 123,
    symbol: str = "BASELINE",
) -> OHLCSeries:
    """
    Constant-volatility random-walk simulation at the instrument's
    nominal volatility level (50 %, 75 %, 100 %).

    Synthetic baseline only — separate from the Swing Detector.
    """
    # Map nominal vol to a per-bar sigma roughly consistent with the label
    # (exact mapping is an implementation choice; documented as such)
    sigma = 0.005 * nominal_vol  # rough scaling for synthetic tests
    return generate_random_walk(
        n_bars=n_bars,
        volatility=sigma,
        timeframe_minutes=timeframe_minutes,
        seed=seed,
        symbol=symbol,
        timeframe=f"M{timeframe_minutes}",
    )
