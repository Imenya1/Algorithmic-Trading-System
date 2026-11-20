"""Shared event schema for all detectors.

Event schema (from Build Order v3.4 and writing-block):
- type
- symbol
- timeframe
- confirmation_time
- payload
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Optional
import json


@dataclass(frozen=True)
class DetectorEvent:
    """Immutable detector event following the shared schema."""

    type: str  # e.g. "SWING_HIGH", "SWING_LOW"
    symbol: str  # e.g. "V75"
    timeframe: str  # e.g. "H1", "M5"
    confirmation_time: datetime  # close time of the confirming candle
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["confirmation_time"] = self.confirmation_time.isoformat()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectorEvent":
        conf = data["confirmation_time"]
        if isinstance(conf, str):
            conf = datetime.fromisoformat(conf)
        return cls(
            type=data["type"],
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            confirmation_time=conf,
            payload=data.get("payload", {}),
        )


# Convenience constructors for Swing events
def make_swing_high(
    symbol: str,
    timeframe: str,
    confirmation_time: datetime,
    price: float,
    bar_index: int,
) -> DetectorEvent:
    return DetectorEvent(
        type="SWING_HIGH",
        symbol=symbol,
        timeframe=timeframe,
        confirmation_time=confirmation_time,
        payload={
            "price": price,
            "bar_index": bar_index,
            "timeframe": timeframe,
        },
    )


def make_swing_low(
    symbol: str,
    timeframe: str,
    confirmation_time: datetime,
    price: float,
    bar_index: int,
) -> DetectorEvent:
    return DetectorEvent(
        type="SWING_LOW",
        symbol=symbol,
        timeframe=timeframe,
        confirmation_time=confirmation_time,
        payload={
            "price": price,
            "bar_index": bar_index,
            "timeframe": timeframe,
        },
    )
