"""Precision / recall of detector swings against a hand-labelled reference sample.

Build Order Phase 2: ~200 hand-labelled bars per symbol and timeframe, including
high-volatility windows. Targets: >= 90% precision and recall on normal windows and
>= 80% on high-volatility review windows.

No labels are shipped with this project and none may be fabricated. Until a real label
file exists the corresponding validation is PENDING. This module only computes the
scores once labels are supplied.

Label CSV columns: symbol,timeframe,bar_index,type,window   (type = SWING_HIGH | SWING_LOW,
window = normal | high_vol). Every bar in a labelled window is judged: the label file lists
the swings a human marked; any detector swing inside a labelled window that is not in the
file is a false positive. Windows are given as a second CSV: symbol,timeframe,first_bar,
last_bar,window.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

import pandas as pd

from .events import SWING_HIGH, SWING_LOW, DetectorEvent

TARGETS = {"normal": 0.90, "high_vol": 0.80}


def load_labels(labels_csv: Path, windows_csv: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    labels = pd.read_csv(labels_csv).to_dict("records")
    windows = pd.read_csv(windows_csv).to_dict("records")
    for lab in labels:
        if lab["type"] not in (SWING_HIGH, SWING_LOW):
            raise ValueError(f"Unknown label type {lab['type']!r}")
    for w in windows:
        if w["window"] not in TARGETS:
            raise ValueError(f"window must be one of {list(TARGETS)}, got {w['window']!r}")
    return labels, windows


def score(events: Sequence[DetectorEvent], labels: Iterable[Dict[str, Any]],
          windows: Iterable[Dict[str, Any]], symbol: str, timeframe: str) -> Dict[str, Dict[str, Any]]:
    """Precision/recall per window kind for one (symbol, timeframe). Pivot position is matched
    exactly on (type, bar_index)."""
    wins = [w for w in windows if (w["symbol"], w["timeframe"]) == (symbol, timeframe)]
    labs = [l for l in labels if (l["symbol"], l["timeframe"]) == (symbol, timeframe)]
    pred: Set[Tuple[str, int]] = {(e.type, e.payload["bar_index"]) for e in events
                                  if (e.symbol, e.timeframe) == (symbol, timeframe)}
    result: Dict[str, Dict[str, Any]] = {}
    for kind in TARGETS:
        kind_wins = [w for w in wins if w["window"] == kind]
        if not kind_wins:
            result[kind] = {"status": "PENDING", "reason": "no labelled windows of this kind"}
            continue

        def inside(bar: int) -> bool:
            return any(w["first_bar"] <= bar <= w["last_bar"] for w in kind_wins)

        truth = {(l["type"], int(l["bar_index"])) for l in labs if inside(int(l["bar_index"]))}
        got = {p for p in pred if inside(p[1])}
        tp = len(truth & got)
        precision = tp / len(got) if got else None
        recall = tp / len(truth) if truth else None
        ok = precision is not None and recall is not None and min(precision, recall) >= TARGETS[kind]
        result[kind] = {"true_positive": tp, "false_positive": len(got - truth),
                        "false_negative": len(truth - got), "precision": precision, "recall": recall,
                        "target": TARGETS[kind], "status": "PASS" if ok else "FAIL"}
    return result
