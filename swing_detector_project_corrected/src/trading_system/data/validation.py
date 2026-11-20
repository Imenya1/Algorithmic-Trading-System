"""OHLC data validation.

Reports every problem it finds, clearly and with the candle index. It never repairs,
drops or invents market data. The single-candle check (`candle_issues`) is shared by the
series validator, the OHLCSeries constructor and the detector's input guard, so all three
apply identical rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional, Sequence

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime import cycle
    from .ohlc import Candle, OHLCSeries

_PRICE_FIELDS = ("open", "high", "low", "close")


class DataValidationError(ValueError):
    """Raised when OHLC data fails validation. Carries the full list of issues."""

    def __init__(self, issues: List["ValidationIssue"], context: str = "") -> None:
        self.issues = issues
        shown = "; ".join(f"[{i.code}] candle {i.index}: {i.message}" for i in issues[:10])
        more = f" (+{len(issues) - 10} more)" if len(issues) > 10 else ""
        prefix = f"{context}: " if context else ""
        super().__init__(f"{prefix}{len(issues)} validation error(s): {shown}{more}")


@dataclass
class ValidationIssue:
    index: Optional[int]
    severity: str  # "error" | "warning"
    code: str
    message: str


@dataclass
class ValidationReport:
    symbol: str
    timeframe: str
    issues: List[ValidationIssue] = field(default_factory=list)
    valid: bool = True
    n_candles: int = 0

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "error":
            self.valid = False

    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def count(self, code: str) -> int:
        return sum(1 for i in self.issues if i.code == code)

    def summary(self) -> str:
        errors = len(self.errors())
        warnings = len(self.issues) - errors
        status = "VALID" if self.valid else "INVALID"
        return (f"{self.symbol}/{self.timeframe}: {status} "
                f"({self.n_candles} candles, {errors} errors, {warnings} warnings)")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool)


def candle_issues(c: "Candle", index: Optional[int] = None) -> List[ValidationIssue]:
    """Validate one candle in isolation: timestamp, price types, finiteness, OHLC relations."""
    out: List[ValidationIssue] = []
    t = c.time
    if t is None or not isinstance(t, datetime) or pd.isna(t):
        out.append(ValidationIssue(index, "error", "INVALID_TIME", f"Missing or invalid timestamp {t!r}"))

    bad_price = False
    for name in _PRICE_FIELDS:
        val = getattr(c, name)
        if val is None:
            out.append(ValidationIssue(index, "error", f"MISSING_{name.upper()}", f"Missing {name}"))
            bad_price = True
        elif not _is_number(val):
            out.append(ValidationIssue(index, "error", f"INVALID_{name.upper()}",
                                       f"{name} is not numeric: {val!r}"))
            bad_price = True
        elif not math.isfinite(float(val)):
            out.append(ValidationIssue(index, "error", f"INVALID_{name.upper()}",
                                       f"{name} is not finite: {val!r}"))
            bad_price = True
    if bad_price:
        return out  # relationships are meaningless with unusable prices

    if c.low > c.high:
        out.append(ValidationIssue(index, "error", "LOW_GT_HIGH", f"low ({c.low}) > high ({c.high})"))
    if not (c.low <= c.open <= c.high):
        out.append(ValidationIssue(index, "error", "OPEN_OUT_OF_RANGE",
                                   f"open ({c.open}) outside [low {c.low}, high {c.high}]"))
    if not (c.low <= c.close <= c.high):
        out.append(ValidationIssue(index, "error", "CLOSE_OUT_OF_RANGE",
                                   f"close ({c.close}) outside [low {c.low}, high {c.high}]"))
    return out


def validate_candles(
    candles: Sequence["Candle"],
    symbol: str = "UNKNOWN",
    timeframe: str = "UNKNOWN",
    allow_gaps: bool = True,
    expected_delta_seconds: Optional[float] = None,
    min_bars: Optional[int] = None,
) -> ValidationReport:
    """Validate a candle sequence.

    Checks: empty input, timestamps, numeric types and finiteness, OHLC relationships,
    duplicate timestamps, out-of-order timestamps, mixed naive/aware timestamps, gaps
    (when ``expected_delta_seconds`` is given) and insufficient history (``min_bars``).
    Gaps are warnings by default (the detector bridges them); pass ``allow_gaps=False`` for strict
    mode, which reports them as errors.
    """
    report = ValidationReport(symbol=symbol, timeframe=timeframe, n_candles=len(candles))

    if len(candles) == 0:
        report.add(ValidationIssue(None, "error", "EMPTY", "No candles provided"))
        return report

    seen_times = set()
    prev_time: Optional[datetime] = None
    tz_modes = set()

    for i, c in enumerate(candles):
        issues = candle_issues(c, i)
        for issue in issues:
            report.add(issue)
        if any(x.code == "INVALID_TIME" for x in issues):
            continue

        tz_modes.add(c.time.tzinfo is None)
        if len(tz_modes) > 1:
            report.add(ValidationIssue(i, "error", "MIXED_TIMEZONE",
                                       "Timestamps mix timezone-aware and naive values"))
            break  # ordering comparisons below would raise TypeError

        if c.time in seen_times:
            report.add(ValidationIssue(i, "error", "DUPLICATE_TIME", f"Duplicate timestamp {c.time}"))
        seen_times.add(c.time)

        if prev_time is not None:
            if c.time < prev_time:
                report.add(ValidationIssue(i, "error", "OUT_OF_ORDER",
                                           f"time {c.time} is before previous {prev_time}"))
            elif c.time > prev_time and expected_delta_seconds is not None:
                delta = (c.time - prev_time).total_seconds()
                if abs(delta - expected_delta_seconds) > 0.5:
                    missing = max(int(round(delta / expected_delta_seconds)) - 1, 0)
                    report.add(ValidationIssue(
                        i, "warning" if allow_gaps else "error", "GAP",
                        f"expected {expected_delta_seconds:g}s after {prev_time}, got {delta:g}s "
                        f"(~{missing} missing candle(s))"))
        prev_time = c.time

    if min_bars is not None and len(candles) < min_bars:
        report.add(ValidationIssue(None, "warning", "INSUFFICIENT_HISTORY",
                                   f"{len(candles)} candles supplied, at least {min_bars} needed "
                                   f"for the detector to be able to emit any swing"))
    return report


def validate_series(series: "OHLCSeries", allow_gaps: bool = True,
                    min_bars: Optional[int] = None) -> ValidationReport:
    """Validate a series using the expected spacing implied by its timeframe label."""
    from ..config import ConfigError, timeframe_seconds  # local import: config has no data deps

    try:
        expected = float(timeframe_seconds(series.timeframe))
    except ConfigError:
        expected = None  # unknown label: gap detection is skipped, not guessed
    return validate_candles(
        list(series), symbol=series.symbol, timeframe=series.timeframe,
        allow_gaps=allow_gaps, expected_delta_seconds=expected, min_bars=min_bars,
    )
