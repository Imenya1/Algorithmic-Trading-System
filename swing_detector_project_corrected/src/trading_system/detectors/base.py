"""Shared detector step-function interface (Build Order v3.4 Section 4 and 9).

Every detector is a step function: its own state plus one closed candle in, updated state
plus newly confirmed events out. The same ``step`` serves batch, replay and streaming; batch
is a loop over it. State is held per (symbol, timeframe) and never shared.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type

from ..data.ohlc import Candle
from ..events import DetectorEvent


class DetectorInputError(ValueError):
    """A candle was rejected by a detector (invalid, out of order, conflicting duplicate)."""


class GapError(DetectorInputError):
    """A candle did not follow its predecessor by exactly one timeframe."""


@dataclass
class DetectorState:
    """Base state container. Subclasses add detector-specific fields."""

    symbol: str
    timeframe: str
    last_processed_index: int = -1
    last_processed_time: Optional[datetime] = None


class BaseDetector(ABC):
    """Abstract base for incremental detectors."""

    name: str = "base"

    def __init__(self, symbol: str, timeframe: str, params: Dict[str, Any]) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.params = dict(params)
        self._state: DetectorState = self._initial_state()

    @abstractmethod
    def _initial_state(self) -> DetectorState:
        """Return a fresh state object."""

    @abstractmethod
    def step(self, candle: Candle) -> Tuple[DetectorState, List[DetectorEvent]]:
        """Consume one closed candle; return (state, newly confirmed events).

        Uses only candles closed at or before ``candle``. Never looks ahead.
        """

    def snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError(f"{type(self).__name__} does not support snapshots")

    def restore(self, snapshot: Dict[str, Any]) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support snapshots")

    def reset(self) -> None:
        self._state = self._initial_state()

    @property
    def state(self) -> DetectorState:
        return self._state

    def process_series(self, candles: Sequence[Candle]) -> Tuple[DetectorState, List[DetectorEvent]]:
        """Batch = a loop over the same ``step``; there is no separate batch algorithm."""
        events: List[DetectorEvent] = []
        for candle in candles:
            _, new_events = self.step(candle)
            events.extend(new_events)
        return self._state, events


_DETECTOR_REGISTRY: Dict[str, Type[BaseDetector]] = {}


def register_detector(name: str):
    def decorator(cls: Type[BaseDetector]):
        _DETECTOR_REGISTRY[name] = cls
        cls.name = name
        return cls

    return decorator


def get_detector_class(name: str) -> Type[BaseDetector]:
    if name not in _DETECTOR_REGISTRY:
        raise KeyError(f"Unknown detector: {name}. Registered: {list(_DETECTOR_REGISTRY)}")
    return _DETECTOR_REGISTRY[name]


def list_detectors() -> List[str]:
    return list(_DETECTOR_REGISTRY.keys())
