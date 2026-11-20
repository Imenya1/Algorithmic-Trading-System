"""Replay, streaming, snapshots, manifests and benchmarks for detectors.

Batch, replay and streaming all go through ``detector.step``; there is no second algorithm.
``DetectorBank`` holds one detector per (symbol, timeframe) so state is never shared.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .config import get_eqh_eql_params, get_swing_params, list_symbols, list_timeframes
from .data.ohlc import Candle, OHLCSeries
from .detectors.base import BaseDetector
from .detectors.eqh_eql import EqhEqlDetector
from .detectors.swing import SwingDetector
from .events import DetectorEvent

VISUAL_DECISIONS = ("accepted", "accepted with defects logged", "rejected")


# --------------------------------------------------------------------------- factory / bank
def create_swing_detector(config: Dict[str, Any], symbol: str, timeframe: str) -> SwingDetector:
    """Swing detector for (symbol, timeframe), with every parameter taken from configuration."""
    return SwingDetector(symbol, timeframe, get_swing_params(config, symbol, timeframe))


def create_eqh_eql_detector(config: Dict[str, Any], symbol: str, timeframe: str) -> EqhEqlDetector:
    """EQH/EQL detector for (symbol, timeframe), with every parameter taken from configuration."""
    return EqhEqlDetector(symbol, timeframe, get_eqh_eql_params(config, symbol, timeframe))


class DetectorBank:
    """One independent swing detector per configured (symbol, timeframe).

    Candles are routed by key, so interleaving symbols or timeframes cannot mix state.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._detectors: Dict[Tuple[str, str], SwingDetector] = {}
        for symbol in list_symbols(config):
            for timeframe in list_timeframes(config, symbol):
                self._detectors[(symbol, timeframe)] = create_swing_detector(config, symbol, timeframe)

    def keys(self) -> List[Tuple[str, str]]:
        return list(self._detectors)

    def detector(self, symbol: str, timeframe: str) -> SwingDetector:
        try:
            return self._detectors[(symbol, timeframe)]
        except KeyError:
            raise KeyError(f"No detector configured for ({symbol!r}, {timeframe!r}); "
                           f"configured: {self.keys()}") from None

    def on_candle(self, symbol: str, timeframe: str, candle: Candle) -> List[DetectorEvent]:
        _, events = self.detector(symbol, timeframe).step(candle)
        return events

    def snapshot(self) -> Dict[str, Any]:
        return {f"{s}|{t}": d.snapshot() for (s, t), d in self._detectors.items()}

    def restore(self, snapshot: Dict[str, Any]) -> None:
        for key, snap in snapshot.items():
            symbol, timeframe = key.split("|")
            self.detector(symbol, timeframe).restore(snap)


# --------------------------------------------------------------------------- replay / streaming
def _check_match(series_symbol: str, series_tf: str, detector: BaseDetector) -> None:
    if (series_symbol, series_tf) != (detector.symbol, detector.timeframe):
        raise ValueError(
            f"Series {series_symbol}/{series_tf} does not match detector "
            f"{detector.symbol}/{detector.timeframe}; refusing to mix state")


def replay_series(series: OHLCSeries, detector: BaseDetector, reset: bool = True) -> List[DetectorEvent]:
    """Feed a whole series through ``detector.step``; return events in confirmation order."""
    _check_match(series.symbol, series.timeframe, detector)
    if reset:
        detector.reset()
    _, events = detector.process_series(list(series))
    return events


def stream_candles(candles: Sequence[Candle], detector: BaseDetector,
                   reset: bool = True) -> Iterator[Tuple[Candle, List[DetectorEvent]]]:
    """Streaming interface: yield (candle, newly confirmed events) per closed candle."""
    if reset:
        detector.reset()
    for candle in candles:
        _, events = detector.step(candle)
        yield candle, events


def stream_swing_eqh_eql(candles: Sequence[Candle], swing: SwingDetector, eq: EqhEqlDetector,
                         reset: bool = True) -> Iterator[Tuple[Candle, List[DetectorEvent], List[DetectorEvent]]]:
    """Streaming Swing -> EQH/EQL pipeline: yield (candle, swings, eq_clusters) per closed candle.

    Order per candle is fixed: the swing detector sees the candle, then the EQ detector sees the
    same candle together with the swings it just confirmed, so the tolerance always uses the ATR
    as of the confirming candle. Both detectors must be for the same (symbol, timeframe)."""
    if (swing.symbol, swing.timeframe) != (eq.symbol, eq.timeframe):
        raise ValueError(f"Swing {swing.symbol}/{swing.timeframe} and EQ {eq.symbol}/{eq.timeframe} "
                         f"detectors do not match; refusing to mix state")
    if reset:
        swing.reset()
        eq.reset()
    for candle in candles:
        _, swings = swing.step(candle)
        _, clusters = eq.step_with_swings(candle, swings)
        yield candle, swings, clusters


def replay_swing_eqh_eql(series: OHLCSeries, swing: SwingDetector, eq: EqhEqlDetector,
                         reset: bool = True) -> Tuple[List[DetectorEvent], List[DetectorEvent]]:
    """Batch Swing -> EQH/EQL replay: a loop over the same streaming path. Returns
    (swing_events, eq_cluster_events) in confirmation order."""
    _check_match(series.symbol, series.timeframe, swing)
    _check_match(series.symbol, series.timeframe, eq)
    swings: List[DetectorEvent] = []
    clusters: List[DetectorEvent] = []
    for _, sw, cl in stream_swing_eqh_eql(list(series), swing, eq, reset=reset):
        swings.extend(sw)
        clusters.extend(cl)
    return swings, clusters


# --------------------------------------------------------------------------- benchmark
def measure_throughput(series: OHLCSeries, detector: BaseDetector, repeats: int = 5,
                       warmup: int = 1) -> Dict[str, Any]:
    """Replay throughput in candles/second. Only ``process_series`` is inside the timed region."""
    _check_match(series.symbol, series.timeframe, detector)
    candles = list(series)
    for _ in range(warmup):
        detector.reset()
        detector.process_series(candles)
    secs: List[float] = []
    for _ in range(repeats):
        detector.reset()
        t0 = time.perf_counter()
        detector.process_series(candles)
        secs.append(time.perf_counter() - t0)
    rates = [len(candles) / s for s in secs if s > 0]
    return {
        "symbol": series.symbol, "timeframe": series.timeframe, "candles": len(candles),
        "repeats": repeats,
        "median_candles_per_sec": float(sorted(rates)[len(rates) // 2]),
        "min_candles_per_sec": float(min(rates)), "max_candles_per_sec": float(max(rates)),
        "python": sys.version.split()[0], "platform": platform.platform(),
    }


def check_throughput_regression(current: Dict[str, Any], baseline: Optional[Dict[str, Any]],
                                tolerance: float = 0.20) -> Dict[str, Any]:
    """Build Order Section 4 / Open Decision 5: fail if throughput falls more than ``tolerance``
    below the previous baseline for the same configuration. No baseline -> baseline recorded."""
    cur = current["median_candles_per_sec"]
    if baseline is None:
        return {"status": "BASELINE_RECORDED", "current": cur, "baseline": None, "change": None}
    base = baseline["median_candles_per_sec"]
    change = (cur - base) / base
    return {"status": "FAIL" if change < -tolerance else "PASS",
            "current": cur, "baseline": base, "change": change, "tolerance": tolerance}


# --------------------------------------------------------------------------- regression snapshot
def _snapshot_text(events: Sequence[DetectorEvent]) -> str:
    return json.dumps([e.to_dict() for e in events], sort_keys=True, indent=2)


def save_regression_snapshot(events: Sequence[DetectorEvent], path: Path) -> str:
    """Save deterministic detector output; returns its content hash."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _snapshot_text(events)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compare_regression_snapshot(events: Sequence[DetectorEvent], path: Path) -> List[str]:
    """Differences between current output and a saved snapshot; empty list means identical."""
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    current = json.loads(_snapshot_text(events))
    diffs: List[str] = []
    if len(saved) != len(current):
        diffs.append(f"event count changed: saved {len(saved)}, current {len(current)}")
    for i, (a, b) in enumerate(zip(saved, current)):
        if a != b:
            diffs.append(f"event {i} differs: saved {json.dumps(a, sort_keys=True)} "
                         f"current {json.dumps(b, sort_keys=True)}")
    return diffs


# --------------------------------------------------------------------------- run manifest
@dataclass
class RunManifest:
    """Reproducibility manifest (Build Order Section 1: code version, dataset hash,
    configuration hash, random seed, date range) plus the Section 5 visual sign-off record."""

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
    data_origin: str = "unspecified"
    review_set_ref: Optional[str] = None
    review_seed: Optional[int] = None
    # None until a human records a sign-off with record_visual_signoff().
    visual_signoff: Optional[Dict[str, Any]] = None
    outstanding_validation_issues: List[str] = field(default_factory=list)
    # Requirements explicitly waived by the project owner (never counted as passed).
    waivers: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def visual_review_status(self) -> str:
        if self.visual_signoff is None:
            return "pending - no developer sign-off recorded"
        return f"signed off: {self.visual_signoff['decision']}"

    def record_visual_signoff(self, reviewer: str, date: str, windows_inspected: List[Any],
                              defects: List[Dict[str, Any]], decision: str,
                              second_reviewer: Optional[str] = None) -> None:
        """Record a human review (Section 5). Must only be called with a real review's outcome."""
        if not reviewer or not date or not windows_inspected:
            raise ValueError("Sign-off needs reviewer, date and the windows inspected")
        if decision not in VISUAL_DECISIONS:
            raise ValueError(f"decision must be one of {VISUAL_DECISIONS}")
        if decision == "accepted with defects logged" and not defects:
            raise ValueError("'accepted with defects logged' requires the defects to be listed")
        self.visual_signoff = {
            "reviewer": reviewer, "second_reviewer": second_reviewer, "date": date,
            "windows_inspected": windows_inspected, "defects": defects, "decision": decision,
        }

    def record_waiver(self, requirement: str, waived_by: str, date: str, reason: str) -> None:
        """Record that the owner waived a requirement. A waiver is not a pass and is shown as such."""
        if not (requirement and waived_by and date and reason):
            raise ValueError("A waiver needs requirement, waived_by, date and reason")
        self.waivers.append({"requirement": requirement, "waived_by": waived_by, "date": date,
                             "reason": reason, "status": "WAIVED (not passed)"})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code_version": self.code_version, "dataset_hash": self.dataset_hash,
            "configuration_hash": self.configuration_hash, "random_seed": self.random_seed,
            "date_range": list(self.date_range), "symbol": self.symbol, "timeframe": self.timeframe,
            "detector_parameters": self.detector_parameters, "data_origin": self.data_origin,
            "test_results": self.test_results, "performance_benchmark": self.performance_benchmark,
            "regression_snapshot_ref": self.regression_snapshot_ref,
            "review_set_ref": self.review_set_ref, "review_seed": self.review_seed,
            "visual_review_status": self.visual_review_status,
            "visual_signoff": self.visual_signoff,
            "outstanding_validation_issues": self.outstanding_validation_issues,
            "waivers": self.waivers,
            "created_at": self.created_at,
        }

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
