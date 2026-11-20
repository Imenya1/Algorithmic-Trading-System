"""Non-repainting n-bar fractal Swing Detector (Build Order v3.4, Phase 2 and Section 9).

Rule (Section 9): SWING_HIGH / SWING_LOW, confirmed n bars after the pivot closes.

  * Pivot candle p is a swing high if high[p] is strictly greater than the highs of the n
    candles before it and the n candles after it. A swing low is the mirror image on lows.
  * The swing is emitted exactly when candle p+n closes, never earlier and never later.
    Its confirmation time is the CLOSE time of candle p+n (not its open time).
  * Once emitted an event is never revised, moved or retracted, and no later candle can
    change it, because the decision uses only candles p-n .. p+n.

Documented decisions where the supplied documents are silent (the Research Specification
was not supplied, so these are UNRESOLVED and reported, not invented):
  * Ties (config ``detectors.swing.tie_rule``):
      - ``strict`` (default): strict inequality on both sides. An equal high/low on either
        side means no pivot.
      - ``mt5``: the MetaTrader 5 Fractals convention. The pivot must be strictly beyond the n
        bars AFTER it and equal-or-beyond the n bars BEFORE it. For n=2 this is the standard
        Williams 5-bar fractal as drawn by MT5. Still past-only and non-repainting: the
        decision uses only candles p-n .. p+n.
  * No minimum-leg filter, no suppression, no supersession.
  * One candle can be both a swing high and a swing low (outside bar); both are emitted,
    high first, so ordering is deterministic.

Gaps (agreed design). Gaps can be genuine (platform maintenance, no ticks) and the
detector never stops for one. Connection handling and backfill belong to the ingestion
layer, not here. The detector only sees candles:
  * gap_policy 'bridge' (default): the next candle is treated as the next bar in sequence,
    for any gap size. Swings whose n-bar window spans a gap carry ``spans_gap``,
    ``gap_missing_bars`` and ``gap_status`` ('confirmed' when every spanned gap was marked
    by upstream via ``Candle.gap_confirmed``, else 'unverified'). Nothing is invented.
  * 'reset' clears the window at a gap; 'error' raises GapError (optional strict mode).
  * Every gap is counted and kept in a bounded log. Each (symbol, timeframe) detector
    handles its own gaps only. Batch and streaming use this same step function.

Input discipline (nothing is repaired or silently dropped):
  * Unclosed, non-finite or structurally invalid candles raise DetectorInputError.
  * An exact repeat of the last candle is idempotent: ignored and counted.
  * A same-timestamp candle with different prices, an earlier timestamp (a late candle that
    must be backfilled upstream BEFORE delivery, because confirmed events are never revised)
    or a spacing shorter than the timeframe raises DetectorInputError.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, List, NamedTuple, Optional, Tuple

from ..config import CANDLE_TIME_CONVENTIONS, GAP_POLICIES, TIE_RULES, timeframe_seconds
from ..data.ohlc import Candle, candle_close_time
from ..data.validation import candle_issues
from ..events import SWING_HIGH, SWING_LOW, DetectorEvent, make_swing_event
from .base import BaseDetector, DetectorInputError, DetectorState, GapError, register_detector

logger = logging.getLogger(__name__)

_ALLOWED_PARAMS = {"n", "candle_time", "gap_policy", "tie_rule"}
GAP_LOG_SIZE = 1000


class _Bar(NamedTuple):
    index: int
    time: datetime
    high: float
    low: float
    gap_before: int = 0          # candles missing between the previous bar and this one
    gap_confirmed: bool = False  # upstream marked that gap as genuine (backfill failed)


class GapRecord(NamedTuple):
    before: datetime
    after: datetime
    missing_bars: int
    status: str  # 'confirmed' | 'unverified'


@dataclass
class SwingDetectorState(DetectorState):
    """Bounded state: at most 2n+1 bars, GAP_LOG_SIZE gap records and counters."""

    window: Deque[_Bar] = field(default_factory=deque)
    last_ohlc: Optional[Tuple[float, float, float, float]] = None
    swing_high_count: int = 0
    swing_low_count: int = 0
    duplicates_ignored: int = 0
    gaps_reset: int = 0
    gaps_bridged: int = 0
    gaps_confirmed: int = 0
    gaps_unverified: int = 0
    missing_bars_total: int = 0
    gap_log: Deque[GapRecord] = field(default_factory=lambda: deque(maxlen=GAP_LOG_SIZE))
    window_gap_total: int = 0  # sum of gap_before over bars currently in the window (kept O(1))


@register_detector("swing")
class SwingDetector(BaseDetector):
    """Incremental n-bar fractal swing detector for one (symbol, timeframe).

    params:
        n            int >= 1, fractal lookback / confirmation delay in bars (required)
        candle_time  'open' (default) or 'close': meaning of Candle.time
        gap_policy   'bridge' (default), 'reset' or 'error'
        tie_rule     'strict' (default) or 'mt5' (see module docstring)
    """

    name = "swing"

    def __init__(self, symbol: str, timeframe: str, params: Dict[str, Any]) -> None:
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            raise ValueError(f"Unknown swing parameter(s): {sorted(unknown)}")
        if "n" not in params:
            raise ValueError("SwingDetector requires parameter 'n' (fractal lookback)")
        n = params["n"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"n must be an integer >= 1, got {n!r}")
        self.n: int = n
        self.candle_time: str = params.get("candle_time", "open")
        if self.candle_time not in CANDLE_TIME_CONVENTIONS:
            raise ValueError(f"candle_time must be one of {CANDLE_TIME_CONVENTIONS}")
        self.gap_policy: str = params.get("gap_policy", "bridge")
        if self.gap_policy not in GAP_POLICIES:
            raise ValueError(f"gap_policy must be one of {GAP_POLICIES}")
        self.tie_rule: str = params.get("tie_rule", "strict")
        if self.tie_rule not in TIE_RULES:
            raise ValueError(f"tie_rule must be one of {TIE_RULES}")
        self._tf_seconds: int = timeframe_seconds(timeframe)  # also validates the label
        super().__init__(symbol, timeframe, params)

    def _initial_state(self) -> SwingDetectorState:
        return SwingDetectorState(symbol=self.symbol, timeframe=self.timeframe)

    # ------------------------------------------------------------------ step
    def step(self, candle: Candle) -> Tuple[SwingDetectorState, List[DetectorEvent]]:
        """Consume one closed candle and return any swing it confirms.

        The state is updated in place and also returned. When the candle is rejected the
        state is left exactly as it was.
        """
        state: SwingDetectorState = self._state  # type: ignore[assignment]
        self._check_candle(candle)

        missing = 0
        if state.last_processed_time is not None:
            if (candle.time.tzinfo is None) != (state.last_processed_time.tzinfo is None):
                raise DetectorInputError(
                    f"{self.symbol}/{self.timeframe}: timestamps mix timezone-aware and naive values")
            if candle.time == state.last_processed_time:
                if (candle.open, candle.high, candle.low, candle.close) == state.last_ohlc:
                    state.duplicates_ignored += 1
                    return state, []
                raise DetectorInputError(
                    f"{self.symbol}/{self.timeframe}: conflicting duplicate candle at {candle.time}")
            if candle.time < state.last_processed_time:
                raise DetectorInputError(
                    f"{self.symbol}/{self.timeframe}: late/out-of-order candle {candle.time} after "
                    f"{state.last_processed_time}; backfill must complete before delivery")
            delta = (candle.time - state.last_processed_time).total_seconds()
            if delta < self._tf_seconds - 0.5:
                raise DetectorInputError(
                    f"{self.symbol}/{self.timeframe}: candles {delta:g}s apart, shorter than the "
                    f"{self._tf_seconds}s timeframe")
            missing = max(int(round(delta / self._tf_seconds)) - 1, 0) if abs(delta - self._tf_seconds) > 0.5 else 0
            if missing == 0 and abs(delta - self._tf_seconds) > 0.5:
                missing = 1  # misaligned spacing between one and two bars: count at least one

        index = candle.index if candle.index is not None else state.last_processed_index + 1
        if index <= state.last_processed_index:
            raise DetectorInputError(
                f"{self.symbol}/{self.timeframe}: bar index {index} not after {state.last_processed_index}")
        if missing and self.gap_policy == "error":
            raise GapError(
                f"{self.symbol}/{self.timeframe}: {missing} missing candle(s) between "
                f"{state.last_processed_time} and {candle.time}")

        # All checks passed: from here on the state is mutated.
        if missing:
            status = "confirmed" if candle.gap_confirmed else "unverified"
            state.gap_log.append(GapRecord(state.last_processed_time, candle.time, missing, status))
            state.missing_bars_total += missing
            if status == "confirmed":
                state.gaps_confirmed += 1
            else:
                state.gaps_unverified += 1
            logger.warning("%s/%s gap: %d missing candle(s) before %s (%s, policy=%s)",
                           self.symbol, self.timeframe, missing, candle.time, status, self.gap_policy)
            if self.gap_policy == "reset":
                state.window.clear()
                state.window_gap_total = 0
                state.gaps_reset += 1
            else:
                state.gaps_bridged += 1
        bar = _Bar(index, candle.time, candle.high, candle.low,
                   missing if self.gap_policy == "bridge" else 0, bool(missing and candle.gap_confirmed))
        state.window.append(bar)
        state.window_gap_total += bar.gap_before
        if len(state.window) > 2 * self.n + 1:
            state.window_gap_total -= state.window.popleft().gap_before
        state.last_processed_index = index
        state.last_processed_time = candle.time
        state.last_ohlc = (candle.open, candle.high, candle.low, candle.close)

        if len(state.window) < 2 * self.n + 1:
            return state, []

        # Window layout: n bars left | pivot | n bars right (the candle that just closed).
        pivot = state.window[self.n]
        others = [b for j, b in enumerate(state.window) if j != self.n]
        confirmation_time = candle_close_time(candle.time, self._tf_seconds, self.candle_time)
        # A gap "inside" the window is the gap before any bar except the oldest one. The running
        # total makes the common no-gap case O(1); the scan below runs only when a gap is present.
        gap_missing = state.window_gap_total - state.window[0].gap_before
        gap_status = None
        if gap_missing > 0:
            inside = [b for b in list(state.window)[1:] if b.gap_before > 0]
            gap_status = "confirmed" if all(b.gap_confirmed for b in inside) else "unverified"

        events: List[DetectorEvent] = []
        # others[:n] are the bars before the pivot, others[n:] the bars after it.
        if self.tie_rule == "strict":
            is_high = all(pivot.high > b.high for b in others)
            is_low = all(pivot.low < b.low for b in others)
        else:  # "mt5": equal allowed on the left, strict on the right
            left, right = others[:self.n], others[self.n:]
            is_high = all(pivot.high >= b.high for b in left) and all(pivot.high > b.high for b in right)
            is_low = all(pivot.low <= b.low for b in left) and all(pivot.low < b.low for b in right)
        if is_high:
            events.append(self._make(SWING_HIGH, pivot, pivot.high, confirmation_time, index, gap_missing, gap_status))
            state.swing_high_count += 1
        if is_low:
            events.append(self._make(SWING_LOW, pivot, pivot.low, confirmation_time, index, gap_missing, gap_status))
            state.swing_low_count += 1
        return state, events

    def _check_candle(self, candle: Candle) -> None:
        if not candle.closed:
            raise DetectorInputError(
                f"{self.symbol}/{self.timeframe}: candle at {candle.time} is not closed")
        issues = candle_issues(candle)
        if issues:
            detail = "; ".join(f"[{i.code}] {i.message}" for i in issues)
            raise DetectorInputError(f"{self.symbol}/{self.timeframe}: invalid candle: {detail}")

    def _make(self, event_type: str, pivot: _Bar, price: float, confirmation_time: datetime,
              confirmation_index: int, gap_missing: int, gap_status: Optional[str]) -> DetectorEvent:
        return make_swing_event(
            event_type, self.symbol, self.timeframe, confirmation_time, price,
            pivot.index, pivot.time, confirmation_index, gap_missing, gap_status)

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> Dict[str, Any]:
        """JSON-serialisable copy of the full detector state."""
        s: SwingDetectorState = self._state  # type: ignore[assignment]
        return {
            "symbol": self.symbol, "timeframe": self.timeframe, "n": self.n,
            "candle_time": self.candle_time, "gap_policy": self.gap_policy,
            "tie_rule": self.tie_rule,
            "last_processed_index": s.last_processed_index,
            "last_processed_time": None if s.last_processed_time is None else s.last_processed_time.isoformat(),
            "last_ohlc": None if s.last_ohlc is None else list(s.last_ohlc),
            "window": [[b.index, b.time.isoformat(), b.high, b.low, b.gap_before, b.gap_confirmed] for b in s.window],
            "swing_high_count": s.swing_high_count, "swing_low_count": s.swing_low_count,
            "duplicates_ignored": s.duplicates_ignored, "gaps_reset": s.gaps_reset,
            "gaps_bridged": s.gaps_bridged, "gaps_confirmed": s.gaps_confirmed,
            "gaps_unverified": s.gaps_unverified, "missing_bars_total": s.missing_bars_total,
            "gap_log": [[g.before.isoformat(), g.after.isoformat(), g.missing_bars, g.status] for g in s.gap_log],
        }

    def restore(self, snapshot: Dict[str, Any]) -> None:
        """Restore state from ``snapshot``. Refuses a snapshot from another symbol/timeframe/n."""
        for key, mine in (("symbol", self.symbol), ("timeframe", self.timeframe), ("n", self.n)):
            if snapshot.get(key) != mine:
                raise ValueError(f"Snapshot {key}={snapshot.get(key)!r} does not match detector {key}={mine!r}")
        if snapshot.get("tie_rule", "strict") != self.tie_rule:  # snapshots from before tie_rule existed are 'strict'
            raise ValueError(f"Snapshot tie_rule={snapshot.get('tie_rule', 'strict')!r} does not match detector tie_rule={self.tie_rule!r}")
        lpt = snapshot["last_processed_time"]
        self._state = SwingDetectorState(
            symbol=self.symbol, timeframe=self.timeframe,
            last_processed_index=snapshot["last_processed_index"],
            last_processed_time=None if lpt is None else datetime.fromisoformat(lpt),
            window=deque(_Bar(i, datetime.fromisoformat(t), h, l, gb, gc)
                         for i, t, h, l, gb, gc in snapshot["window"]),
            last_ohlc=None if snapshot["last_ohlc"] is None else tuple(snapshot["last_ohlc"]),
            swing_high_count=snapshot["swing_high_count"], swing_low_count=snapshot["swing_low_count"],
            duplicates_ignored=snapshot["duplicates_ignored"], gaps_reset=snapshot["gaps_reset"],
            gaps_bridged=snapshot["gaps_bridged"], gaps_confirmed=snapshot["gaps_confirmed"],
            gaps_unverified=snapshot["gaps_unverified"], missing_bars_total=snapshot["missing_bars_total"],
            window_gap_total=sum(w[4] for w in snapshot["window"]),
            gap_log=deque((GapRecord(datetime.fromisoformat(b), datetime.fromisoformat(a), m, st)
                           for b, a, m, st in snapshot["gap_log"]), maxlen=GAP_LOG_SIZE),
        )
