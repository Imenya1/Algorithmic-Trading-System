"""EQH/EQL (Equal Highs / Equal Lows) Detector - Detector 2.

Consumes confirmed Swing Detector events and closed candles (for ATR). It never recomputes
swings.

Rules taken from Build Order v3.4 (Section 8 instrument parameters, Section 9 catalogue):
  * Tolerance = max(tolerance_points * point_size, tolerance_atr_mult * ATR(14)), in PRICE UNITS.
      V75:  max(716 points,  0.10 ATR)
      V100: max(12 points,   0.10 ATR)
      V50:  max(115 points,  0.12 ATR)
    Section 8 states the fixed floor in POINTS while prices and ATR are in price units, so the
    per-instrument ``point_size`` (price units per point) converts it. The Build Order does not
    give the point size (Open Decision 8); it is configuration, never hard-coded here.
  * Minimum touches = 2; confirmation time = confirmation time of the second (newest) swing.
  * Output event EQ_CLUSTER, payload: side, level, inner_edge, touch_count, age.
  * ATR period = 14.

Causality contract (enforced, not assumed):
  A swing event is accepted only when its confirmation time equals the close time of the last
  candle this detector has processed. The tolerance therefore uses the ATR as it stood at the
  swing's own confirmation candle: never an older ATR, and never an ATR that already includes
  later candles. Drive the detector with ``step_with_swings(candle, swing_events)`` (candle
  first, then the swings that candle confirmed) or call ``step`` then ``on_swing_events``.

UNRESOLVED (not stated in the Build Order; no Research Specification is present in the project):
  * Multi-touch grouping beyond "prior same-side swings within tolerance of the newest swing".
  * Maximum age / expiry of a swing as an equal-level candidate (cluster lifecycle belongs to
    the Phase 4 POI lifecycle manager).
  * Minimum spacing between constituent swings (none applied).
  * Exact definitions of ``level`` (mean of constituents), ``inner_edge`` (min for highs, max
    for lows) and ``age`` (bars between first and last constituent).
  * Role of spread in the equality test (listed as an input in Section 9; no rule given; unused).
  * ATR smoothing (Wilder used) and ATR warm-up (no cluster until ATR(14) is defined).
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, List, NamedTuple, Optional, Sequence, Tuple

from ..config import CANDLE_TIME_CONVENTIONS, timeframe_seconds
from ..data.ohlc import Candle, candle_close_time
from ..data.validation import candle_issues
from ..events import DetectorEvent, EQ_CLUSTER, EQH, EQL, SWING_HIGH, SWING_LOW  # noqa: F401
from .base import BaseDetector, DetectorInputError, DetectorState, register_detector

logger = logging.getLogger(__name__)

_REQUIRED_PARAMS = ("tolerance_points", "tolerance_atr_mult", "point_size")
_ALLOWED_PARAMS = set(_REQUIRED_PARAMS) | {"atr_period", "min_touches", "max_swings_kept", "candle_time"}
_SNAPSHOT_VERSION = 2
_SNAPSHOT_KEYS = (
    "version", "symbol", "timeframe", "tolerance_points", "tolerance_atr_mult", "point_size",
    "atr_period", "min_touches", "max_swings_kept", "candle_time",
    "last_processed_index", "last_processed_time", "last_ohlc", "last_swing_confirmation",
    "swings_received", "clusters_emitted", "duplicate_swings_ignored", "duplicate_candles_ignored",
    "atr", "prev_close", "tr_window", "swing_highs", "swing_lows",
)


@dataclass
class EqhEqlDetectorState(DetectorState):
    """Bounded state: recent same-side swings + ATR window."""

    swing_highs: Deque[DetectorEvent] = field(default_factory=deque)
    swing_lows: Deque[DetectorEvent] = field(default_factory=deque)
    tr_window: Deque[float] = field(default_factory=deque)  # first ATR_PERIOD true ranges
    atr: Optional[float] = None
    prev_close: Optional[float] = None
    last_ohlc: Optional[Tuple[float, float, float, float]] = None
    swings_received: int = 0
    clusters_emitted: int = 0
    duplicate_swings_ignored: int = 0
    duplicate_candles_ignored: int = 0
    last_swing_confirmation: Optional[datetime] = None


class _Swing(NamedTuple):
    event: DetectorEvent
    price: float
    bar_index: int


def _within(diff: float, tol: float) -> bool:
    """``diff <= tol`` with float-noise protection at the boundary (0.12 vs 0.1200000000000045)."""
    return diff <= tol or math.isclose(diff, tol, rel_tol=1e-9, abs_tol=1e-12)


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@register_detector("eqh_eql")
class EqhEqlDetector(BaseDetector):
    """Incremental EQH/EQL detector for one (symbol, timeframe).

    params:
        tolerance_points    fixed tolerance floor in broker points (>= 0)             required
        tolerance_atr_mult  multiple of ATR(14) (>= 0)                                  required
        point_size          price units per point (> 0)                                 required
        atr_period          int >= 1 (default 14)
        min_touches         int >= 2 (default 2)
        max_swings_kept     int >= 1, bounded memory per side (default 500)
        candle_time         'open' (default) or 'close': meaning of Candle.time
    """

    name = "eqh_eql"

    def __init__(self, symbol: str, timeframe: str, params: Dict[str, Any]) -> None:
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            raise ValueError(f"Unknown eqh_eql parameter(s): {sorted(unknown)}")
        missing = [k for k in _REQUIRED_PARAMS if k not in params]
        if missing:
            raise ValueError(f"eqh_eql requires parameter(s) {missing}; no instrument defaults are assumed")
        tp, am, ps = (_num(params[k]) for k in _REQUIRED_PARAMS)
        if tp is None or tp < 0 or am is None or am < 0:
            raise ValueError("tolerance_points and tolerance_atr_mult must be finite non-negative numbers")
        if ps is None or ps <= 0:
            raise ValueError("point_size must be a finite positive number (price units per point)")
        defaults = {"atr_period": 14, "min_touches": 2, "max_swings_kept": 500}
        for key, lo in (("atr_period", 1), ("min_touches", 2), ("max_swings_kept", 1)):
            v = params.get(key, defaults[key])
            if _int(v) is None or v < lo:
                raise ValueError(f"{key} must be an integer >= {lo}, got {v!r}")
        self.tolerance_points: float = tp
        self.tolerance_atr_mult: float = am
        self.point_size: float = ps
        self.atr_period: int = params.get("atr_period", 14)
        self.min_touches: int = params.get("min_touches", 2)
        self.max_swings_kept: int = params.get("max_swings_kept", 500)
        self.candle_time: str = params.get("candle_time", "open")
        if self.candle_time not in CANDLE_TIME_CONVENTIONS:
            raise ValueError(f"candle_time must be one of {CANDLE_TIME_CONVENTIONS}")
        self._tf_seconds: int = timeframe_seconds(timeframe)  # also validates the label
        super().__init__(symbol, timeframe, params)

    def _initial_state(self) -> EqhEqlDetectorState:
        return EqhEqlDetectorState(symbol=self.symbol, timeframe=self.timeframe)

    # ------------------------------------------------------------------ tolerance
    def tolerance_floor(self) -> float:
        """Fixed tolerance floor converted from points to price units."""
        return self.tolerance_points * self.point_size

    def current_tolerance(self) -> Optional[float]:
        """max(points * point_size, mult * ATR) in price units; None while ATR is warming up."""
        atr = self._state.atr  # type: ignore[attr-defined]
        if atr is None:
            return None
        return max(self.tolerance_floor(), self.tolerance_atr_mult * atr)

    # ------------------------------------------------------------------ candle handling
    def _prepare_candle(self, candle: Candle) -> Optional[Dict[str, Any]]:
        """Validate a candle and compute the state it would produce. Mutates nothing.

        Returns None for an exact repeat of the last candle (idempotent)."""
        state: EqhEqlDetectorState = self._state  # type: ignore[assignment]
        if not isinstance(candle, Candle):
            raise DetectorInputError(f"{self.symbol}/{self.timeframe}: expected a Candle, got {type(candle).__name__}")
        if not candle.closed:
            raise DetectorInputError(f"{self.symbol}/{self.timeframe}: candle at {candle.time} is not closed")
        issues = candle_issues(candle)
        if issues:
            detail = "; ".join(f"[{i.code}] {i.message}" for i in issues)
            raise DetectorInputError(f"{self.symbol}/{self.timeframe}: invalid candle: {detail}")
        if state.last_processed_time is not None:
            if (candle.time.tzinfo is None) != (state.last_processed_time.tzinfo is None):
                raise DetectorInputError(f"{self.symbol}/{self.timeframe}: timestamps mix timezone-aware and naive values")
            if candle.time == state.last_processed_time:
                if (candle.open, candle.high, candle.low, candle.close) == state.last_ohlc:
                    return None
                raise DetectorInputError(
                    f"{self.symbol}/{self.timeframe}: conflicting duplicate candle at {candle.time}")
            if candle.time < state.last_processed_time:
                raise DetectorInputError(
                    f"{self.symbol}/{self.timeframe}: late/out-of-order candle "
                    f"{candle.time} after {state.last_processed_time}")
            delta = (candle.time - state.last_processed_time).total_seconds()
            if delta < self._tf_seconds - 0.5:
                raise DetectorInputError(
                    f"{self.symbol}/{self.timeframe}: candles {delta:g}s apart, shorter than the "
                    f"{self._tf_seconds}s timeframe")
        index = candle.index if candle.index is not None else state.last_processed_index + 1
        if index <= state.last_processed_index:
            raise DetectorInputError(
                f"{self.symbol}/{self.timeframe}: bar index {index} not after {state.last_processed_index}")

        # ATR (Wilder). First value = simple mean of the first atr_period true ranges.
        if state.prev_close is None:
            tr = candle.high - candle.low
        else:
            tr = max(candle.high - candle.low, abs(candle.high - state.prev_close),
                     abs(candle.low - state.prev_close))
        atr = state.atr
        append_tr = atr is None
        if atr is None:
            if len(state.tr_window) + 1 == self.atr_period:
                atr = (sum(state.tr_window) + tr) / self.atr_period
        else:
            atr = (atr * (self.atr_period - 1) + tr) / self.atr_period
        return {"index": index, "candle": candle, "tr": tr, "atr": atr, "append_tr": append_tr}

    def _commit_candle(self, prep: Dict[str, Any]) -> None:
        state: EqhEqlDetectorState = self._state  # type: ignore[assignment]
        candle: Candle = prep["candle"]
        if prep["append_tr"]:
            state.tr_window.append(prep["tr"])
        state.atr = prep["atr"]
        state.prev_close = candle.close
        state.last_processed_index = prep["index"]
        state.last_processed_time = candle.time
        state.last_ohlc = (candle.open, candle.high, candle.low, candle.close)

    def step(self, candle: Candle) -> Tuple[EqhEqlDetectorState, List[DetectorEvent]]:
        """Advance time and ATR with one closed candle. A candle alone never emits a cluster."""
        prep = self._prepare_candle(candle)
        if prep is None:
            self._state.duplicate_candles_ignored += 1  # type: ignore[attr-defined]
        else:
            self._commit_candle(prep)
        return self._state, []  # type: ignore[return-value]

    def step_with_swings(self, candle: Candle,
                         swing_events: Sequence[DetectorEvent] = ()) -> Tuple[EqhEqlDetectorState, List[DetectorEvent]]:
        """One incremental step: the candle first, then the swings that candle confirmed.

        Validates everything before changing anything, so a rejected input leaves the state
        exactly as it was. This is the single path used by replay and streaming."""
        state: EqhEqlDetectorState = self._state  # type: ignore[assignment]
        prep = self._prepare_candle(candle)
        if prep is None:
            close_time = self._close_time(state.last_processed_time)
        else:
            close_time = self._close_time(candle.time)
        accepted, dups = self._validate_swings(swing_events, close_time)
        if prep is None:
            state.duplicate_candles_ignored += 1
        else:
            self._commit_candle(prep)
        state.duplicate_swings_ignored += dups
        return state, self._apply_swings(accepted)

    # ------------------------------------------------------------------ swing handling
    def _close_time(self, candle_time: Optional[datetime]) -> Optional[datetime]:
        return None if candle_time is None else candle_close_time(candle_time, self._tf_seconds, self.candle_time)

    def _validate_swings(self, swing_events: Sequence[DetectorEvent],
                         close_time: Optional[datetime]) -> Tuple[List[_Swing], int]:
        """Check a whole batch of swing events. Mutates nothing. Returns (accepted, duplicates)."""
        state: EqhEqlDetectorState = self._state  # type: ignore[assignment]
        where = f"{self.symbol}/{self.timeframe}"
        seen: Dict[Tuple[str, int], float] = {}
        last_bar: Dict[str, Optional[int]] = {}
        for etype, dq in ((SWING_HIGH, state.swing_highs), (SWING_LOW, state.swing_lows)):
            for e in dq:
                seen[(etype, int(e.payload["bar_index"]))] = float(e.payload["price"])
            last_bar[etype] = int(dq[-1].payload["bar_index"]) if dq else None
        last_conf = state.last_swing_confirmation
        accepted: List[_Swing] = []
        dups = 0
        for ev in swing_events:
            if not isinstance(ev, DetectorEvent):
                raise DetectorInputError(f"{where}: expected a DetectorEvent, got {type(ev).__name__}")
            if ev.type not in (SWING_HIGH, SWING_LOW):
                continue  # other event types may share the stream; they are not inputs here
            if (ev.symbol, ev.timeframe) != (self.symbol, self.timeframe):
                raise DetectorInputError(
                    f"Swing event {ev.symbol}/{ev.timeframe} does not belong to this detector {where}")
            payload = ev.payload if isinstance(ev.payload, dict) else {}
            price, bar = _num(payload.get("price")), _int(payload.get("bar_index"))
            if price is None or bar is None or bar < 0:
                raise DetectorInputError(
                    f"{where}: malformed {ev.type} payload (needs finite numeric 'price' and integer "
                    f"'bar_index' >= 0): {payload!r}")
            key = (ev.type, bar)
            if key in seen:
                if seen[key] != price:
                    raise DetectorInputError(f"{where}: conflicting {ev.type} for bar {bar}: "
                                             f"{seen[key]} vs {price}")
                dups += 1  # exact redelivery: idempotent, never forms a cluster with itself
                continue
            if close_time is None:
                raise DetectorInputError(f"{where}: swing received before any candle was processed")
            try:
                same = ev.confirmation_time == close_time
                late = ev.confirmation_time < close_time
                backwards = last_conf is not None and ev.confirmation_time < last_conf
            except TypeError:
                raise DetectorInputError(f"{where}: swing confirmation time and candle time mix "
                                         f"timezone-aware and naive values") from None
            if backwards:
                raise DetectorInputError(
                    f"{where}: out-of-order swing confirmation {ev.confirmation_time} after {last_conf}")
            if not same:
                raise DetectorInputError(
                    f"{where}: swing confirmation time {ev.confirmation_time} must equal the close of the "
                    f"last processed candle {close_time} ("
                    + ("ATR would already include later candles: look-ahead" if late
                       else "swing arrived before its confirming candle was processed") + ")")
            prev = last_bar[ev.type]
            if prev is not None and bar <= prev:
                raise DetectorInputError(f"{where}: out-of-order {ev.type}: bar {bar} not after bar {prev}")
            seen[key] = price
            last_bar[ev.type] = bar
            last_conf = ev.confirmation_time
            accepted.append(_Swing(ev, price, bar))
        return accepted, dups

    def _apply_swings(self, accepted: Sequence[_Swing]) -> List[DetectorEvent]:
        """Apply validated swings and emit clusters. Cannot raise on bad input."""
        state: EqhEqlDetectorState = self._state  # type: ignore[assignment]
        out: List[DetectorEvent] = []
        for sw in accepted:
            ev = sw.event
            dq = state.swing_highs if ev.type == SWING_HIGH else state.swing_lows
            priors = list(dq)
            dq.append(ev)
            if len(dq) > self.max_swings_kept:
                dq.popleft()
            state.swings_received += 1
            state.last_swing_confirmation = ev.confirmation_time

            tol = self.current_tolerance()
            if tol is None:
                continue  # ATR warm-up: equality cannot be evaluated yet
            members = [p for p in priors if _within(abs(float(p.payload["price"]) - sw.price), tol)]
            if len(members) + 1 < self.min_touches:
                continue
            cluster = sorted(members + [ev], key=lambda e: int(e.payload["bar_index"]))
            prices = [float(e.payload["price"]) for e in cluster]
            bars = [int(e.payload["bar_index"]) for e in cluster]
            side = "high" if ev.type == SWING_HIGH else "low"
            payload: Dict[str, Any] = {
                "side": side,
                "level": float(sum(prices) / len(prices)),
                "inner_edge": float(min(prices) if side == "high" else max(prices)),
                "touch_count": len(cluster),
                "age": int(bars[-1] - bars[0]),
                "timeframe": self.timeframe,
                "tolerance_used": float(tol),
                "tolerance_used_points": float(tol / self.point_size),
                "constituent_bar_indices": bars,
                "constituent_prices": prices,
            }
            cbi = _int(ev.payload.get("confirmation_bar_index"))
            if cbi is not None:
                payload["confirmation_bar_index"] = cbi
            out.append(DetectorEvent(type=EQ_CLUSTER, symbol=self.symbol, timeframe=self.timeframe,
                                     confirmation_time=ev.confirmation_time, payload=payload))
            state.clusters_emitted += 1
        return out

    def on_swing_events(self, swing_events: Sequence[DetectorEvent]) -> List[DetectorEvent]:
        """Consume swings confirmed by the candle most recently passed to ``step``.

        All-or-nothing: if any event in the batch is rejected, none is applied."""
        state: EqhEqlDetectorState = self._state  # type: ignore[assignment]
        accepted, dups = self._validate_swings(swing_events, self._close_time(state.last_processed_time))
        state.duplicate_swings_ignored += dups
        return self._apply_swings(accepted)

    # ------------------------------------------------------------------ inspection helpers
    def pending_swing_highs(self) -> List[DetectorEvent]:
        return list(self._state.swing_highs)  # type: ignore[attr-defined]

    def pending_swing_lows(self) -> List[DetectorEvent]:
        return list(self._state.swing_lows)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ snapshot / restore
    def snapshot(self) -> Dict[str, Any]:
        """JSON-serialisable copy of the complete detector state."""
        s: EqhEqlDetectorState = self._state  # type: ignore[assignment]
        return {
            "version": _SNAPSHOT_VERSION,
            "symbol": self.symbol, "timeframe": self.timeframe,
            "tolerance_points": self.tolerance_points, "tolerance_atr_mult": self.tolerance_atr_mult,
            "point_size": self.point_size, "atr_period": self.atr_period,
            "min_touches": self.min_touches, "max_swings_kept": self.max_swings_kept,
            "candle_time": self.candle_time,
            "last_processed_index": s.last_processed_index,
            "last_processed_time": None if s.last_processed_time is None else s.last_processed_time.isoformat(),
            "last_ohlc": None if s.last_ohlc is None else list(s.last_ohlc),
            "last_swing_confirmation": None if s.last_swing_confirmation is None else s.last_swing_confirmation.isoformat(),
            "swings_received": s.swings_received, "clusters_emitted": s.clusters_emitted,
            "duplicate_swings_ignored": s.duplicate_swings_ignored,
            "duplicate_candles_ignored": s.duplicate_candles_ignored,
            "atr": s.atr, "prev_close": s.prev_close, "tr_window": list(s.tr_window),
            "swing_highs": [e.to_dict() for e in s.swing_highs],
            "swing_lows": [e.to_dict() for e in s.swing_lows],
        }

    def restore(self, snapshot: Dict[str, Any]) -> None:
        """Restore from ``snapshot``. Refuses incomplete snapshots and any identity or parameter
        mismatch; on any failure the current state is left untouched."""
        if not isinstance(snapshot, dict):
            raise ValueError("Snapshot must be a dict")
        missing = [k for k in _SNAPSHOT_KEYS if k not in snapshot]
        if missing:
            raise ValueError(f"Snapshot is incomplete, missing {missing}")
        if snapshot["version"] != _SNAPSHOT_VERSION:
            raise ValueError(f"Unsupported snapshot version {snapshot['version']!r}")
        for key in ("symbol", "timeframe", "tolerance_points", "tolerance_atr_mult", "point_size",
                    "atr_period", "min_touches", "max_swings_kept", "candle_time"):
            if snapshot[key] != getattr(self, key):
                raise ValueError(f"Snapshot {key}={snapshot[key]!r} does not match detector "
                                 f"{key}={getattr(self, key)!r}")
        try:
            lpt, lsc = snapshot["last_processed_time"], snapshot["last_swing_confirmation"]
            lo = snapshot["last_ohlc"]
            highs = deque(DetectorEvent.from_dict(d) for d in snapshot["swing_highs"])
            lows = deque(DetectorEvent.from_dict(d) for d in snapshot["swing_lows"])
            for etype, dq in ((SWING_HIGH, highs), (SWING_LOW, lows)):
                for e in dq:
                    if (e.type, e.symbol, e.timeframe) != (etype, self.symbol, self.timeframe) \
                            or _num(e.payload.get("price")) is None or _int(e.payload.get("bar_index")) is None:
                        raise ValueError(f"invalid retained swing {e.type} {e.payload!r}")
            tr_window = deque(float(x) for x in snapshot["tr_window"])
            atr, prev_close = snapshot["atr"], snapshot["prev_close"]
            new_state = EqhEqlDetectorState(
                symbol=self.symbol, timeframe=self.timeframe,
                last_processed_index=int(snapshot["last_processed_index"]),
                last_processed_time=None if lpt is None else datetime.fromisoformat(lpt),
                last_swing_confirmation=None if lsc is None else datetime.fromisoformat(lsc),
                last_ohlc=None if lo is None else tuple(float(x) for x in lo),
                swings_received=int(snapshot["swings_received"]),
                clusters_emitted=int(snapshot["clusters_emitted"]),
                duplicate_swings_ignored=int(snapshot["duplicate_swings_ignored"]),
                duplicate_candles_ignored=int(snapshot["duplicate_candles_ignored"]),
                atr=None if atr is None else float(atr),
                prev_close=None if prev_close is None else float(prev_close),
                tr_window=tr_window, swing_highs=highs, swing_lows=lows,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Snapshot is corrupt: {exc}") from exc
        self._state = new_state


def make_eq_cluster_event(
    symbol: str,
    timeframe: str,
    confirmation_time: datetime,
    side: str,
    level: float,
    inner_edge: float,
    touch_count: int,
    age: int,
    extra: Optional[Dict[str, Any]] = None,
) -> DetectorEvent:
    """Factory for EQ_CLUSTER events."""
    payload: Dict[str, Any] = {
        "side": side,
        "level": float(level),
        "inner_edge": float(inner_edge),
        "touch_count": int(touch_count),
        "age": int(age),
        "timeframe": timeframe,
    }
    if extra:
        payload.update(extra)
    return DetectorEvent(
        type=EQ_CLUSTER,
        symbol=symbol,
        timeframe=timeframe,
        confirmation_time=confirmation_time,
        payload=payload,
    )
