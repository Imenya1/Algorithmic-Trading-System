"""Non-repainting n-bar fractal Swing Detector.

Specification (Build Order v3.4 + writing-block.md):
- A swing high / low is identified using an n-bar fractal.
- A swing is confirmed n bars after its pivot closes.
- Detector must not repaint confirmed historical output.
- Uses only candles closed at or before the current processing candle.
- Confirmation time = close of the candle that completes the event
  (i.e. the candle that is n bars after the pivot).

Unresolved specification decisions (Research Spec not supplied):
- Equal-high / equal-low tie handling inside the fractal calculation.
- Ambiguous or overlapping swing candidates.
- Any minimum-leg calculation not defined in the governing specs.
- Suppression or supersession behaviour where the specification does not define it.

Implementation decision (documented, not claimed as Research Spec rule):
- For the fractal comparison we use strict inequality (high > left and right neighbours).
  Equal highs/lows do not form a classic fractal pivot under this rule.
  This is flagged as an unresolved decision; change only after Research Spec clarification.
- No minimum-leg filter is applied (not defined).
- No suppression/supersession of prior swings (not defined).
- Overlapping candidates are both emitted if both satisfy the fractal rule independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Deque
from collections import deque
import numpy as np

from .base import BaseDetector, DetectorState, register_detector
from ..events import DetectorEvent, make_swing_high, make_swing_low
from ..data.ohlc import Candle


@dataclass
class SwingDetectorState(DetectorState):
    """State for the n-bar fractal swing detector."""

    # Rolling window of recent highs/lows/times/indices (size up to 2*n + 1 needed)
    # We keep a deque of the last (2*n) candles' data for the fractal check.
    window_highs: Deque[float] = field(default_factory=deque)
    window_lows: Deque[float] = field(default_factory=deque)
    window_times: Deque[datetime] = field(default_factory=deque)
    window_indices: Deque[int] = field(default_factory=deque)

    # Confirmed events already emitted (for audit / no-repaint checks)
    confirmed_events: List[DetectorEvent] = field(default_factory=list)

    # Counters for characterisation metrics
    swing_high_count: int = 0
    swing_low_count: int = 0


@register_detector("swing")
class SwingDetector(BaseDetector):
    """
    Non-repainting n-bar fractal swing detector.

    Parameters expected in `params`:
        n: int  — lookback / confirmation bars (e.g. 2 or 3)
    """

    name = "swing"

    def __init__(self, symbol: str, timeframe: str, params: Dict[str, Any]) -> None:
        if "n" not in params:
            raise ValueError("SwingDetector requires parameter 'n' (fractal lookback)")
        self.n: int = int(params["n"])
        if self.n < 1:
            raise ValueError(f"n must be >= 1, got {self.n}")
        super().__init__(symbol, timeframe, params)

    def _initial_state(self) -> SwingDetectorState:
        return SwingDetectorState(symbol=self.symbol, timeframe=self.timeframe)

    def step(self, candle: Candle) -> Tuple[SwingDetectorState, List[DetectorEvent]]:
        """
        Process one closed candle.

        Algorithm:
        1. Append the new candle to the rolling window.
        2. Once the window has at least 2*n + 1 bars, the bar that is
           exactly n positions from the end can be evaluated as a potential
           pivot (it now has n bars on its right).
        3. Check whether that candidate is a fractal high / low using
           the n bars to its left and the n bars to its right.
        4. If yes, emit a confirmed SWING_HIGH / SWING_LOW with
           confirmation_time = candle.time (the rightmost bar that just closed).
        5. Never revisit or alter previously confirmed events.
        """
        state = self._state  # type: SwingDetectorState
        events: List[DetectorEvent] = []

        # Guard: ignore candles that are not strictly after the last processed
        if state.last_processed_time is not None and candle.time <= state.last_processed_time:
            # Duplicate or out-of-order — do not process
            return state, events

        idx = candle.index if candle.index is not None else state.last_processed_index + 1

        # Append to window
        state.window_highs.append(candle.high)
        state.window_lows.append(candle.low)
        state.window_times.append(candle.time)
        state.window_indices.append(idx)

        # Keep window size bounded (we need at most 2*n + 1)
        max_window = 2 * self.n + 1
        while len(state.window_highs) > max_window:
            state.window_highs.popleft()
            state.window_lows.popleft()
            state.window_times.popleft()
            state.window_indices.popleft()

        state.last_processed_index = idx
        state.last_processed_time = candle.time

        # Can we evaluate a pivot? Need full left + pivot + full right = 2*n + 1
        if len(state.window_highs) < max_window:
            self._state = state
            return state, events

        # The candidate pivot is at position n (0-based) in the current window
        # Window layout: [0 .. n-1] left | n pivot | [n+1 .. 2n] right
        pivot_pos = self.n
        pivot_high = state.window_highs[pivot_pos]
        pivot_low = state.window_lows[pivot_pos]
        pivot_time = state.window_times[pivot_pos]
        pivot_index = state.window_indices[pivot_pos]

        # Fractal high: pivot high > all n left highs AND > all n right highs
        # (strict inequality — equal-high handling is an unresolved decision)
        is_swing_high = True
        is_swing_low = True

        for k in range(self.n):
            left = pivot_pos - 1 - k
            right = pivot_pos + 1 + k
            if state.window_highs[left] >= pivot_high or state.window_highs[right] >= pivot_high:
                is_swing_high = False
            if state.window_lows[left] <= pivot_low or state.window_lows[right] <= pivot_low:
                is_swing_low = False

        # Confirmation time is the close of the current (rightmost) candle
        confirmation_time = candle.time

        if is_swing_high:
            ev = make_swing_high(
                symbol=self.symbol,
                timeframe=self.timeframe,
                confirmation_time=confirmation_time,
                price=float(pivot_high),
                bar_index=int(pivot_index),
            )
            events.append(ev)
            state.confirmed_events.append(ev)
            state.swing_high_count += 1

        if is_swing_low:
            ev = make_swing_low(
                symbol=self.symbol,
                timeframe=self.timeframe,
                confirmation_time=confirmation_time,
                price=float(pivot_low),
                bar_index=int(pivot_index),
            )
            events.append(ev)
            state.confirmed_events.append(ev)
            state.swing_low_count += 1

        self._state = state
        return state, events

    def get_confirmed_events(self) -> List[DetectorEvent]:
        return list(self._state.confirmed_events)  # type: ignore
