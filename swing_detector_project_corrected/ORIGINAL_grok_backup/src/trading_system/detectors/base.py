"""Shared detector step-function interface.

Every detector:
  Input:  own current state + one closed candle + configured parameters
  Output: updated state + zero or more newly confirmed events

Same implementation used for batch, historical replay, and streaming.
State is isolated per (symbol, timeframe).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type
from datetime import datetime

from ..events import DetectorEvent
from ..data.ohlc import Candle


@dataclass
class DetectorState:
    """Base state container. Subclasses hold detector-specific fields."""

    symbol: str
    timeframe: str
    last_processed_index: int = -1
    last_processed_time: Optional[datetime] = None
    # Subclasses add their own fields


class BaseDetector(ABC):
    """
    Abstract base for all detectors.

    Implements the incremental step interface required by Build Order v3.4.
    """

    name: str = "base"

    def __init__(self, symbol: str, timeframe: str, params: Dict[str, Any]) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.params = dict(params)
        self._state: DetectorState = self._initial_state()

    @abstractmethod
    def _initial_state(self) -> DetectorState:
        """Return a fresh state object for this detector."""
        ...

    @abstractmethod
    def step(self, candle: Candle) -> Tuple[DetectorState, List[DetectorEvent]]:
        """
        Process one closed candle.

        Must use only candles closed at or before the current candle.
        Must not look ahead.
        Returns (updated_state, list_of_newly_confirmed_events).
        """
        ...

    def reset(self) -> None:
        """Reset to initial state (for new runs / new symbols)."""
        self._state = self._initial_state()

    @property
    def state(self) -> DetectorState:
        return self._state

    def process_series(
        self, candles: List[Candle]
    ) -> Tuple[DetectorState, List[DetectorEvent]]:
        """
        Batch convenience: feed candles one-by-one using the same step().
        Guarantees batch == streaming by construction.
        """
        all_events: List[DetectorEvent] = []
        for candle in candles:
            self._state, events = self.step(candle)
            all_events.extend(events)
        return self._state, all_events


# Simple registry for future detectors (Phase 2 only has Swing)
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
