"""Shared event schema for all detectors (Build Order v3.4 Section 1 and 9).

Every event carries: type, symbol, timeframe, confirmation time, payload.

SWING_HIGH / SWING_LOW payload (Section 9): ``price``, ``bar_index``, ``timeframe``.
Additional fields are additive to the contract; consumers that follow the contract may
ignore them: ``pivot_time`` (timestamp of the pivot candle as supplied),
``confirmation_bar_index`` (index of the candle whose close confirmed the swing) and,
only when the swing's n-bar window spans a data gap, ``spans_gap`` (True),
``gap_missing_bars`` (candles missing inside the window) and ``gap_status``
('confirmed' or 'unverified').
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

SWING_HIGH = "SWING_HIGH"
SWING_LOW = "SWING_LOW"
EQ_CLUSTER = "EQ_CLUSTER"
EQH = "EQH"   # legacy alias; prefer EQ_CLUSTER with side="high"
EQL = "EQL"   # legacy alias; prefer EQ_CLUSTER with side="low"


@dataclass(frozen=True)
class DetectorEvent:
    """Immutable detector event following the shared schema."""

    type: str
    symbol: str
    timeframe: str
    confirmation_time: datetime  # close time of the candle that completes the event
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["payload"] = copy.deepcopy(self.payload)
        d["confirmation_time"] = self.confirmation_time.isoformat()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectorEvent":
        conf = data["confirmation_time"]
        if isinstance(conf, str):
            conf = datetime.fromisoformat(conf)
        return cls(type=data["type"], symbol=data["symbol"], timeframe=data["timeframe"],
                   confirmation_time=conf, payload=dict(data.get("payload", {})))


def make_swing_event(
    event_type: str,
    symbol: str,
    timeframe: str,
    confirmation_time: datetime,
    price: float,
    bar_index: int,
    pivot_time: datetime,
    confirmation_bar_index: int,
    gap_missing_bars: int = 0,
    gap_status: Optional[str] = None,
) -> DetectorEvent:
    """Build a SWING_HIGH or SWING_LOW event."""
    if event_type not in (SWING_HIGH, SWING_LOW):
        raise ValueError(f"Not a swing event type: {event_type!r}")
    payload: Dict[str, Any] = {
        "price": float(price),
        "bar_index": int(bar_index),
        "timeframe": timeframe,
        "pivot_time": pivot_time.isoformat(),
        "confirmation_bar_index": int(confirmation_bar_index),
    }
    if gap_missing_bars > 0:
        payload.update(spans_gap=True, gap_missing_bars=int(gap_missing_bars), gap_status=gap_status)
    return DetectorEvent(type=event_type, symbol=symbol, timeframe=timeframe,
                         confirmation_time=confirmation_time, payload=payload)
