"""Data validation utilities for OHLC series.

Reports failures clearly; does not silently discard invalid records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence
from datetime import datetime
import numpy as np

from .ohlc import Candle, OHLCSeries


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

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "error":
            self.valid = False

    def summary(self) -> str:
        if self.valid and not self.issues:
            return f"{self.symbol}/{self.timeframe}: OK ({len(self.issues)} issues)"
        errors = sum(1 for i in self.issues if i.severity == "error")
        warnings = sum(1 for i in self.issues if i.severity == "warning")
        return (
            f"{self.symbol}/{self.timeframe}: "
            f"{'VALID' if self.valid else 'INVALID'} "
            f"({errors} errors, {warnings} warnings)"
        )


def validate_candles(
    candles: Sequence[Candle],
    symbol: str = "UNKNOWN",
    timeframe: str = "UNKNOWN",
    allow_gaps: bool = True,
    expected_delta_seconds: Optional[float] = None,
) -> ValidationReport:
    """
    Validate a sequence of candles.

    Checks:
    - Missing required fields
    - Invalid numerical values
    - Invalid OHLC relationships
    - Duplicate timestamps
    - Incorrect timestamp ordering
    - Gaps (reported as warnings if allow_gaps=True)
    """
    report = ValidationReport(symbol=symbol, timeframe=timeframe)

    if not candles:
        report.add(
            ValidationIssue(
                index=None,
                severity="warning",
                code="EMPTY",
                message="No candles provided",
            )
        )
        return report

    seen_times = set()
    prev_time: Optional[datetime] = None

    for i, c in enumerate(candles):
        # Required fields
        if c.time is None:
            report.add(
                ValidationIssue(i, "error", "MISSING_TIME", "Missing timestamp")
            )
            continue
        for name, val in [
            ("open", c.open),
            ("high", c.high),
            ("low", c.low),
            ("close", c.close),
        ]:
            if val is None or (isinstance(val, float) and not np.isfinite(val)):
                report.add(
                    ValidationIssue(
                        i, "error", f"INVALID_{name.upper()}", f"Invalid {name}={val}"
                    )
                )

        # OHLC relationships
        if c.low > c.high:
            report.add(
                ValidationIssue(
                    i, "error", "LOW_GT_HIGH", f"low ({c.low}) > high ({c.high})"
                )
            )
        if not (c.low <= c.open <= c.high):
            report.add(
                ValidationIssue(
                    i, "error", "OPEN_OUT_OF_RANGE", f"open ({c.open}) outside [low, high]"
                )
            )
        if not (c.low <= c.close <= c.high):
            report.add(
                ValidationIssue(
                    i,
                    "error",
                    "CLOSE_OUT_OF_RANGE",
                    f"close ({c.close}) outside [low, high]",
                )
            )

        # Duplicates and ordering
        if c.time in seen_times:
            report.add(
                ValidationIssue(
                    i, "error", "DUPLICATE_TIME", f"Duplicate timestamp {c.time}"
                )
            )
        seen_times.add(c.time)

        if prev_time is not None:
            if c.time < prev_time:
                report.add(
                    ValidationIssue(
                        i,
                        "error",
                        "OUT_OF_ORDER",
                        f"time {c.time} is before previous {prev_time}",
                    )
                )
            elif c.time == prev_time:
                # already covered by duplicate
                pass
            elif expected_delta_seconds is not None:
                delta = (c.time - prev_time).total_seconds()
                if abs(delta - expected_delta_seconds) > 1.0:  # 1s tolerance
                    report.add(
                        ValidationIssue(
                            i,
                            "warning" if allow_gaps else "error",
                            "GAP_OR_IRREGULAR",
                            f"expected ~{expected_delta_seconds}s, got {delta}s",
                        )
                    )
        prev_time = c.time

    return report


def validate_series(series: OHLCSeries) -> ValidationReport:
    return validate_candles(
        list(series),
        symbol=series.symbol,
        timeframe=series.timeframe,
    )
