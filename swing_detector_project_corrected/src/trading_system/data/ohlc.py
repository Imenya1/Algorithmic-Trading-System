"""OHLC candle data structures and loading.

Convention (see config ``data.candle_time``): ``Candle.time`` is the bar OPEN time unless
the configuration says otherwise. ``candle_close_time`` converts to the bar close time,
which is what the Build Order calls the confirmation time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

import numpy as np
import pandas as pd

from .validation import DataValidationError, ValidationIssue, validate_candles


@dataclass(frozen=True)
class Candle:
    """One OHLC candle. Immutable.

    ``closed`` must be True for the detector to accept it: only closed candles may be
    processed (Build Order Section 9). ``index`` is the absolute bar index in the series.
    ``gap_confirmed`` is an optional upstream marker set by the ingestion layer on the first
    candle after a gap whose backfill was attempted and failed (a genuine platform gap).
    Connection handling and backfill are NOT the detector's concern.
    """

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    index: Optional[int] = None
    closed: bool = True
    gap_confirmed: bool = False

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low


def candle_close_time(candle_time: datetime, timeframe_seconds: int, convention: str = "open") -> datetime:
    """Close time of a candle whose timestamp follows ``convention`` ('open' or 'close')."""
    if convention == "open":
        return candle_time + timedelta(seconds=timeframe_seconds)
    if convention == "close":
        return candle_time
    raise ValueError(f"Unknown candle_time convention {convention!r}")


class OHLCSeries:
    """Ordered, validated sequence of closed candles for one symbol and timeframe.

    Absolute bar indices are preserved: a candle that already carries an ``index`` keeps it,
    so a slice of a series reports the same ``bar_index`` as the full series does. When no
    candle has an index, 0..N-1 is assigned. A mix is rejected.
    """

    def __init__(self, candles: Sequence[Candle], symbol: str, timeframe: str,
                 validate: bool = True) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        cands = list(candles)

        if validate:
            self._validate(cands)

        have_index = [c.index is not None for c in cands]
        if cands and any(have_index) and not all(have_index):
            raise ValueError("Candles must either all carry an index or none of them")
        if cands and not any(have_index):
            cands = [Candle(c.time, c.open, c.high, c.low, c.close, c.volume, i, c.closed, c.gap_confirmed)
                     for i, c in enumerate(cands)]
        elif cands:
            idx = [c.index for c in cands]
            if any(b <= a for a, b in zip(idx, idx[1:])):
                raise ValueError("Candle indices must be strictly increasing")
        self._candles: List[Candle] = cands

        self.indices = np.array([c.index for c in cands], dtype=np.int64)
        self.times = np.array([c.time for c in cands], dtype=object)
        self.opens = np.array([c.open for c in cands], dtype=np.float64)
        self.highs = np.array([c.high for c in cands], dtype=np.float64)
        self.lows = np.array([c.low for c in cands], dtype=np.float64)
        self.closes = np.array([c.close for c in cands], dtype=np.float64)
        self.volumes = np.array([np.nan if c.volume is None else c.volume for c in cands],
                                dtype=np.float64)
        self._position: Dict[int, int] = {int(b): p for p, b in enumerate(self.indices)}

    def _validate(self, candles: List[Candle]) -> None:
        # Structural errors are fatal here. Gaps are data-quality findings reported by
        # validate_series() and enforced by the detector's gap policy.
        report = validate_candles(candles, self.symbol, self.timeframe, allow_gaps=True)
        if not report.valid:
            raise DataValidationError(report.errors(), context=f"{self.symbol}/{self.timeframe}")

    def __len__(self) -> int:
        return len(self._candles)

    def __getitem__(self, idx: int) -> Candle:
        return self._candles[idx]

    def __iter__(self) -> Iterator[Candle]:
        return iter(self._candles)

    def position_of(self, bar_index: int) -> int:
        """Array position of an absolute bar index. KeyError if it is not in this series."""
        return self._position[int(bar_index)]

    def has_index(self, bar_index: int) -> bool:
        return int(bar_index) in self._position

    def slice(self, start: int, end: Optional[int] = None) -> "OHLCSeries":
        """Sub-series by array position [start:end]. Absolute bar indices are preserved."""
        return OHLCSeries(self._candles[start:end], self.symbol, self.timeframe, validate=False)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            "time": self.times, "open": self.opens, "high": self.highs,
            "low": self.lows, "close": self.closes, "volume": self.volumes,
        })

    # ------------------------------------------------------------------ loaders
    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, symbol: str, timeframe: str,
                       time_col: str = "time", validate: bool = True,
                       epoch_unit: Optional[str] = None) -> "OHLCSeries":
        """Build from columns time, open, high, low, close [, volume].

        The DataFrame is never modified. Unparseable values are reported with their row
        number and raw value; nothing is silently coerced, dropped or filled.
        """
        required = [time_col, "open", "high", "low", "close"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required column(s): {missing}; found {list(df.columns)}")
        if len(df) == 0:
            raise DataValidationError(
                [ValidationIssue(None, "error", "EMPTY", "No rows in input")], f"{symbol}/{timeframe}")

        issues: List[ValidationIssue] = []
        has_volume = "volume" in df.columns
        candles: List[Candle] = []
        for row_no, (t_raw, o, h, l, c, v) in enumerate(zip(
                df[time_col], df["open"], df["high"], df["low"], df["close"],
                df["volume"] if has_volume else [None] * len(df))):
            t = _parse_time(t_raw, epoch_unit)
            if t is None:
                issues.append(ValidationIssue(row_no, "error", "INVALID_TIME",
                                              f"Cannot parse timestamp {t_raw!r}"))
                continue
            prices = []
            for name, raw in (("open", o), ("high", h), ("low", l), ("close", c)):
                try:
                    prices.append(float(raw))
                except (TypeError, ValueError):
                    issues.append(ValidationIssue(row_no, "error", f"INVALID_{name.upper()}",
                                                  f"{name} is not numeric: {raw!r}"))
                    prices.append(float("nan"))
            vol = None
            if v is not None and not pd.isna(v):
                try:
                    vol = float(v)
                except (TypeError, ValueError):
                    issues.append(ValidationIssue(row_no, "warning", "INVALID_VOLUME",
                                                  f"volume is not numeric: {v!r}"))
            candles.append(Candle(time=t, open=prices[0], high=prices[1], low=prices[2],
                                  close=prices[3], volume=vol))
        if any(i.severity == "error" for i in issues):
            raise DataValidationError([i for i in issues if i.severity == "error"], f"{symbol}/{timeframe}")
        return cls(candles, symbol=symbol, timeframe=timeframe, validate=validate)

    @classmethod
    def from_csv(cls, path: Path, symbol: str, timeframe: str, validate: bool = True,
                 epoch_unit: Optional[str] = None) -> "OHLCSeries":
        """Load a CSV with a header row: time,open,high,low,close[,volume]. Source is read-only."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"OHLC file not found: {path}")
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        return cls.from_dataframe(df, symbol, timeframe, validate=validate, epoch_unit=epoch_unit)

    @classmethod
    def from_records(cls, records: Sequence[dict], symbol: str, timeframe: str,
                     validate: bool = True) -> "OHLCSeries":
        return cls.from_dataframe(pd.DataFrame(list(records)), symbol, timeframe, validate=validate)


def _parse_time(raw, epoch_unit: Optional[str]) -> Optional[datetime]:
    """Parse one timestamp; None if it cannot be parsed. Numeric values need ``epoch_unit``."""
    if raw is None or (not isinstance(raw, str) and pd.isna(raw)):
        return None
    try:
        if isinstance(raw, (int, float, np.integer, np.floating)) and not isinstance(raw, bool):
            if epoch_unit is None:
                return None
            ts = pd.to_datetime(raw, unit=epoch_unit)
        elif isinstance(raw, str) and epoch_unit is not None and raw.strip().lstrip("-").isdigit():
            ts = pd.to_datetime(int(raw), unit=epoch_unit)
        else:
            ts = pd.to_datetime(raw)
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except (ValueError, TypeError, OverflowError):
        return None
