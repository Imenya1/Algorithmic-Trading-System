"""OHLC candle data structures and handling.

Supports:
- Time, Open, High, Low, Close
- Optional Volume (not used by Swing Detector)
- Validation of relationships and ordering
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Iterator, Tuple
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Candle:
    """Single OHLC candle. Immutable."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    index: Optional[int] = None  # absolute bar index in a series

    def __post_init__(self) -> None:
        # Basic sanity; full validation is done at series level
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            # Allow equality; strict validation is external
            pass

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low


class OHLCSeries:
    """
    Ordered sequence of closed candles for one symbol + timeframe.
    Provides array views for efficient detector processing.
    """

    def __init__(
        self,
        candles: Sequence[Candle],
        symbol: str,
        timeframe: str,
        validate: bool = True,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self._candles: List[Candle] = list(candles)

        if validate:
            self._validate()

        # Build numpy arrays for fast access (columnar)
        n = len(self._candles)
        self.times = np.array([c.time for c in self._candles], dtype=object)
        self.opens = np.array([c.open for c in self._candles], dtype=np.float64)
        self.highs = np.array([c.high for c in self._candles], dtype=np.float64)
        self.lows = np.array([c.low for c in self._candles], dtype=np.float64)
        self.closes = np.array([c.close for c in self._candles], dtype=np.float64)
        self.volumes = np.array(
            [c.volume if c.volume is not None else np.nan for c in self._candles],
            dtype=np.float64,
        )
        # Re-index candles with sequential indices
        self._candles = [
            Candle(
                time=c.time,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                index=i,
            )
            for i, c in enumerate(self._candles)
        ]

    def _validate(self) -> None:
        if not self._candles:
            return
        prev_time = None
        for i, c in enumerate(self._candles):
            # Required fields
            if c.time is None:
                raise ValueError(f"Candle {i}: missing time")
            for name, val in [("open", c.open), ("high", c.high), ("low", c.low), ("close", c.close)]:
                if val is None or not np.isfinite(val):
                    raise ValueError(f"Candle {i}: invalid {name}={val}")
            # OHLC relationships
            if not (c.low <= c.high):
                raise ValueError(f"Candle {i}: low ({c.low}) > high ({c.high})")
            if not (c.low <= c.open <= c.high):
                raise ValueError(f"Candle {i}: open ({c.open}) outside [low, high]")
            if not (c.low <= c.close <= c.high):
                raise ValueError(f"Candle {i}: close ({c.close}) outside [low, high]")
            # Ordering
            if prev_time is not None and c.time <= prev_time:
                raise ValueError(
                    f"Candle {i}: time {c.time} is not strictly after previous {prev_time}"
                )
            prev_time = c.time

    def __len__(self) -> int:
        return len(self._candles)

    def __getitem__(self, idx: int) -> Candle:
        return self._candles[idx]

    def __iter__(self) -> Iterator[Candle]:
        return iter(self._candles)

    def slice(self, start: int, end: Optional[int] = None) -> "OHLCSeries":
        """Return a new series for [start:end]."""
        end = end if end is not None else len(self)
        return OHLCSeries(
            self._candles[start:end],
            symbol=self.symbol,
            timeframe=self.timeframe,
            validate=False,
        )

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time": self.times,
                "open": self.opens,
                "high": self.highs,
                "low": self.lows,
                "close": self.closes,
                "volume": self.volumes,
            }
        )

    @classmethod
    def from_dataframe(
        cls,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        time_col: str = "time",
        validate: bool = True,
    ) -> "OHLCSeries":
        """Build from a pandas DataFrame with columns time, open, high, low, close [, volume]."""
        required = [time_col, "open", "high", "low", "close"]
        for col in required:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")
        candles = []
        for _, row in df.iterrows():
            t = row[time_col]
            if not isinstance(t, datetime):
                t = pd.to_datetime(t).to_pydatetime()
            vol = float(row["volume"]) if "volume" in df.columns and pd.notna(row["volume"]) else None
            candles.append(
                Candle(
                    time=t,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=vol,
                )
            )
        return cls(candles, symbol=symbol, timeframe=timeframe, validate=validate)

    @classmethod
    def from_records(
        cls,
        records: Sequence[dict],
        symbol: str,
        timeframe: str,
        validate: bool = True,
    ) -> "OHLCSeries":
        candles = []
        for r in records:
            t = r["time"]
            if isinstance(t, str):
                t = datetime.fromisoformat(t)
            candles.append(
                Candle(
                    time=t,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["volume"]) if "volume" in r and r["volume"] is not None else None,
                )
            )
        return cls(candles, symbol=symbol, timeframe=timeframe, validate=validate)
