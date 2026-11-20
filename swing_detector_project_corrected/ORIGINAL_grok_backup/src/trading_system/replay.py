"""Historical replay and streaming processing engine.

Uses the same detector.step() for both batch and streaming, guaranteeing
batch output == streaming output by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Iterator, Tuple
from pathlib import Path
import json
import hashlib
import time

from .data.ohlc import OHLCSeries, Candle
from .detectors.base import BaseDetector
from .detectors.swing import SwingDetector
from .events import DetectorEvent
from .config import config_hash, dataset_hash


@dataclass
class RunManifest:
    """Reproducibility manifest required by Build Order v3.4 Section 10."""

    code_version: str
    dataset_hash: str
    configuration_hash: str
    random_seed: Optional[int]
    date_range: Tuple[str, str]
    symbol: str
    timeframe: str
    detector_parameters: Dict[str, Any]
    test_results: Dict[str, Any] = field(default_factory=dict)
    performance_benchmark: Dict[str, Any] = field(default_factory=dict)
    regression_snapshot_ref: Optional[str] = None
    visual_review_status: str = "pending"
    outstanding_validation_issues: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code_version": self.code_version,
            "dataset_hash": self.dataset_hash,
            "configuration_hash": self.configuration_hash,
            "random_seed": self.random_seed,
            "date_range": list(self.date_range),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "detector_parameters": self.detector_parameters,
            "test_results": self.test_results,
            "performance_benchmark": self.performance_benchmark,
            "regression_snapshot_ref": self.regression_snapshot_ref,
            "visual_review_status": self.visual_review_status,
            "outstanding_validation_issues": self.outstanding_validation_issues,
            "created_at": self.created_at,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)


def create_swing_detector(
    symbol: str, timeframe: str, n: int
) -> SwingDetector:
    return SwingDetector(symbol=symbol, timeframe=timeframe, params={"n": n})


def replay_series(
    series: OHLCSeries,
    detector: BaseDetector,
    reset: bool = True,
) -> List[DetectorEvent]:
    """
    Feed an entire OHLCSeries through the detector one candle at a time.
    Returns all confirmed events in order of confirmation.
    """
    if reset:
        detector.reset()
    _, events = detector.process_series(list(series))
    return events


def stream_candles(
    candles: List[Candle],
    detector: BaseDetector,
) -> Iterator[Tuple[Candle, List[DetectorEvent]]]:
    """Streaming interface: yield (candle, newly_confirmed_events) for each step."""
    detector.reset()
    for candle in candles:
        _, events = detector.step(candle)
        yield candle, events


def measure_throughput(
    series: OHLCSeries,
    detector: BaseDetector,
    repeats: int = 3,
) -> Dict[str, float]:
    """
    Measure replay throughput (candles / second).
    Returns mean, min, max over the repeats.
    """
    times = []
    for _ in range(repeats):
        detector.reset()
        t0 = time.perf_counter()
        detector.process_series(list(series))
        t1 = time.perf_counter()
        times.append(t1 - t0)
    n = len(series)
    rates = [n / t if t > 0 else float("inf") for t in times]
    return {
        "candles": n,
        "mean_seconds": float(sum(times) / len(times)),
        "mean_candles_per_sec": float(sum(rates) / len(rates)),
        "min_candles_per_sec": float(min(rates)),
        "max_candles_per_sec": float(max(rates)),
    }


def save_regression_snapshot(
    events: List[DetectorEvent],
    path: Path,
) -> str:
    """Save a deterministic snapshot of detector output for future regression."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [e.to_dict() for e in events]
    text = json.dumps(payload, sort_keys=True, default=str, indent=2)
    path.write_text(text, encoding="utf-8")
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return h
