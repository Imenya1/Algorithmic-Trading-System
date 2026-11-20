"""Tests for the EQH/EQL detector (Build Order v3.4 Section 8 parameters, Section 9 catalogue).

Fixtures are built so that every swing is delivered on the candle that confirms it (the
detector enforces that contract). Independent oracles are used where a result is computed.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np
import pytest
import yaml

from tests.helpers import tie_rich_series, walk
from trading_system.config import ConfigError, get_eqh_eql_params, load_config, timeframe_seconds
from trading_system.data.ohlc import Candle, OHLCSeries
from trading_system.detectors import DetectorInputError, EqhEqlDetector, get_detector_class, list_detectors
from trading_system.detectors.eqh_eql import EQ_CLUSTER
from trading_system.events import DetectorEvent, SWING_HIGH, SWING_LOW, make_swing_event
from trading_system.replay import (create_eqh_eql_detector, create_swing_detector,
                                   replay_swing_eqh_eql, stream_swing_eqh_eql)

UTC = timezone.utc
T0 = datetime(2025, 1, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def config():
    return load_config()


class Rig:
    """Feeds flat candles (true range == ``tr`` so ATR == ``tr``) and delivers swings on the
    candle that confirms them."""

    def __init__(self, symbol="V75", tf="M5", params=None, config=None, base=1000.0):
        self.symbol, self.tf = symbol, tf
        self.params = params if params is not None else get_eqh_eql_params(config, symbol, tf)
        self.d = EqhEqlDetector(symbol, tf, self.params)
        self.sec = timeframe_seconds(tf)
        self.i = 0
        self.base = base
        self.tr = 100.0

    def candle(self, tr=None) -> Candle:
        tr = self.tr if tr is None else tr
        c = Candle(T0 + timedelta(seconds=self.i * self.sec), self.base, self.base + tr / 2,
                   self.base - tr / 2, self.base, 1.0, self.i, True)
        self.i += 1
        return c

    def close_of(self, c: Candle) -> datetime:
        return c.time + timedelta(seconds=self.sec)

    def warm(self, n=20, tr=100.0):
        self.tr = tr
        for _ in range(n):
            self.d.step(self.candle(tr))

    def swing(self, etype, price, bar, conf) -> DetectorEvent:
        return make_swing_event(etype, self.symbol, self.tf, conf, price, bar,
                                pivot_time=conf - timedelta(seconds=2 * self.sec),
                                confirmation_bar_index=bar + 2)

    def confirm(self, specs: Sequence[Tuple[str, float, int]], tr=None) -> List[DetectorEvent]:
        """Advance one candle and deliver swings (type, price, pivot bar) that it confirms."""
        c = self.candle(tr)
        evs = [self.swing(t, p, b, self.close_of(c)) for t, p, b in specs]
        return self.d.step_with_swings(c, evs)[1]


def params(points=716, mult=0.10, point_size=0.01, **kw):
    return dict(tolerance_points=points, tolerance_atr_mult=mult, point_size=point_size, **kw)


# =========================================================================== registration / config
def test_registered():
    assert "eqh_eql" in list_detectors()
    assert get_detector_class("eqh_eql") is EqhEqlDetector


@pytest.mark.parametrize("sym,tf,points,mult", [("V75", "M5", 716, 0.10), ("V75", "H1", 716, 0.10),
                                                ("V100", "M3", 12, 0.10), ("V100", "M15", 12, 0.10),
                                                ("V50", "M15", 115, 0.12), ("V50", "H1", 115, 0.12)])
def test_section8_tolerances_reach_the_running_detector(config, sym, tf, points, mult):
    p = get_eqh_eql_params(config, sym, tf)
    assert (p["tolerance_points"], p["tolerance_atr_mult"], p["atr_period"], p["min_touches"]) == (points, mult, 14, 2)
    det = create_eqh_eql_detector(config, sym, tf)  # the object the pipeline actually runs
    assert (det.tolerance_points, det.tolerance_atr_mult, det.point_size) == (points, mult, p["point_size"])


def test_each_instrument_gets_its_own_values_no_silent_fallback(config):
    got = {s: get_eqh_eql_params(config, s, config["instruments"][s]["ltf"]) for s in ("V75", "V100", "V50")}
    assert len({(g["tolerance_points"], g["tolerance_atr_mult"]) for g in got.values()}) == 3
    with pytest.raises(ConfigError):
        get_eqh_eql_params(config, "V10", "M5")  # unknown symbol is an error, not V75's values
    with pytest.raises(ConfigError):
        get_eqh_eql_params(config, "V75", "M30")  # timeframe that is neither HTF nor LTF


def _cfg_without(tmp_path, symbol, key):
    raw = yaml.safe_load((ROOT / "config" / "instruments.yaml").read_text())
    del raw["instruments"][symbol][key]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


@pytest.mark.parametrize("key", ["eqh_eql_tolerance_points", "eqh_eql_tolerance_atr_mult", "point_size"])
def test_missing_instrument_key_rejected(tmp_path, key):
    with pytest.raises(ConfigError, match=key):
        load_config(_cfg_without(tmp_path, "V50", key))


@pytest.mark.parametrize("key,bad", [("eqh_eql_tolerance_points", -1), ("eqh_eql_tolerance_points", "716"),
                                     ("eqh_eql_tolerance_atr_mult", float("nan")), ("eqh_eql_tolerance_atr_mult", True),
                                     ("point_size", 0), ("point_size", -0.01), ("point_size", float("inf")),
                                     ("point_size", None)])
def test_invalid_instrument_values_rejected(tmp_path, key, bad):
    raw = yaml.safe_load((ROOT / "config" / "instruments.yaml").read_text())
    raw["instruments"]["V75"][key] = bad
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match=key):
        load_config(p)


def test_invalid_detector_section_values_rejected(config):
    for bad in ({"atr_period": 14.5}, {"min_touches": "2"}, {"max_swings_kept": True}, {"bogus": 1}):
        cfg = copy.deepcopy(config)
        cfg["detectors"]["eqh_eql"] = bad
        with pytest.raises(ConfigError):
            get_eqh_eql_params(cfg, "V75", "M5")


def test_detector_constructor_requires_every_tolerance_parameter_and_validates():
    with pytest.raises(ValueError, match="requires"):
        EqhEqlDetector("V75", "M5", {"tolerance_points": 716, "tolerance_atr_mult": 0.1})  # no point_size
    with pytest.raises(ValueError, match="Unknown"):
        EqhEqlDetector("V75", "M5", params(bogus=1))
    for bad in (dict(point_size=0), dict(point_size=float("nan")), dict(points=-1), dict(mult=float("inf")),
                dict(atr_period=0), dict(min_touches=1), dict(max_swings_kept=0), dict(candle_time="mid")):
        with pytest.raises(ValueError):
            EqhEqlDetector("V75", "M5", params(**bad))
    with pytest.raises(Exception):
        EqhEqlDetector("V75", "5min", params())  # invalid timeframe label


# =========================================================================== ATR
def _wilder_atr(h, l, c, period=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(h))]
    out = [None] * len(h)
    atr = sum(tr[:period]) / period
    out[period - 1] = atr
    for i in range(period, len(h)):
        atr = (atr * (period - 1) + tr[i]) / period
        out[i] = atr
    return out


def test_atr_matches_independent_wilder_implementation_and_warmup():
    s = walk(120, 5, "V75", "M5")
    d = EqhEqlDetector("V75", "M5", params())
    ref = _wilder_atr(list(s.highs), list(s.lows), list(s.closes))
    for i, c in enumerate(s):
        d.step(c)
        if i < 13:
            assert d.state.atr is None and d.current_tolerance() is None
        else:
            assert d.state.atr == pytest.approx(ref[i], rel=1e-12)


def test_no_cluster_during_atr_warmup():
    r = Rig(params=params())
    r.warm(5)
    assert r.d.current_tolerance() is None
    assert r.confirm([(SWING_HIGH, 1000.0, 1)]) == []
    assert r.confirm([(SWING_HIGH, 1000.0, 2)]) == []  # still warming (7 candles)
    assert r.d.state.swings_received == 2


# =========================================================================== tolerance and units
def test_tolerance_is_max_of_floor_in_price_units_and_atr_component():
    r = Rig(params=params(716, 0.10, 0.01))
    r.warm(20, tr=20.0)  # 0.1*ATR = 2.0 ; floor = 716*0.01 = 7.16 price units
    assert r.d.tolerance_floor() == pytest.approx(7.16)
    assert r.d.current_tolerance() == pytest.approx(7.16)
    r2 = Rig(params=params(716, 0.10, 0.01))
    r2.warm(20, tr=100.0)  # 0.1*ATR = 10.0 > floor
    assert r2.d.current_tolerance() == pytest.approx(10.0)


def test_points_are_converted_to_price_units_not_compared_raw():
    """716 'points' must not be treated as 716 price units (the original defect)."""
    r = Rig(params=params(716, 0.0, 0.01))
    r.warm(20, tr=1.0)  # ATR component disabled -> tolerance is exactly 7.16 price units
    assert r.confirm([(SWING_HIGH, 5000.0, 20)]) == []
    assert r.confirm([(SWING_HIGH, 5100.0, 22)]) == []      # 100 price units apart: NOT equal
    out = r.confirm([(SWING_HIGH, 5005.0, 24)])              # 5 from the first one: equal
    assert len(out) == 1 and out[0].payload["constituent_bar_indices"] == [20, 24]
    assert out[0].payload["tolerance_used"] == pytest.approx(7.16)
    assert out[0].payload["tolerance_used_points"] == pytest.approx(716.0)


def test_tolerance_boundary_inside_exact_and_outside_with_float_noise():
    for delta, expect in ((0.1199, True), (0.12, True), (0.1200001, False)):
        r = Rig(params=params(12, 0.0, 0.01), base=500.0)
        r.warm(20, tr=0.5)
        r.confirm([(SWING_HIGH, 1000.00, 20)])
        out = r.confirm([(SWING_HIGH, 1000.00 + delta, 22)])
        assert bool(out) is expect, delta  # 1000.12-1000.00 == 0.1200000000000045 in floats


def test_atr_component_binds_and_is_instrument_specific(config):
    r = Rig("V50", "M15", config=config, base=90.0)
    r.warm(20, tr=2.0)  # 0.12*2.0 = 0.24 > floor 115*0.0001
    assert r.d.current_tolerance() == pytest.approx(0.24)
    assert r.confirm([(SWING_LOW, 90.0, 20)]) == []
    assert len(r.confirm([(SWING_LOW, 90.2, 22)])) == 1
    r2 = Rig("V50", "M15", config=config, base=90.0)
    r2.warm(20, tr=2.0)
    r2.confirm([(SWING_LOW, 90.0, 20)])
    assert r2.confirm([(SWING_LOW, 90.3, 22)]) == []


def test_eqh_and_eql_payloads(config):
    r = Rig(config=config)
    r.warm(20)
    r.confirm([(SWING_HIGH, 5000.0, 20)])
    eqh = r.confirm([(SWING_HIGH, 5004.0, 22)])[0]
    r.confirm([(SWING_LOW, 4000.0, 24)])
    eql = r.confirm([(SWING_LOW, 4003.0, 26)])[0]
    for ev, side, level, edge in ((eqh, "high", 5002.0, 5000.0), (eql, "low", 4001.5, 4003.0)):
        assert ev.type == EQ_CLUSTER and (ev.symbol, ev.timeframe) == ("V75", "M5")
        p = ev.payload
        assert p["side"] == side and p["touch_count"] == 2 and p["age"] == 2
        assert p["level"] == pytest.approx(level) and p["inner_edge"] == edge and p["timeframe"] == "M5"
        assert p["confirmation_bar_index"] == p["constituent_bar_indices"][-1] + 2
    assert eqh.confirmation_time > T0


def test_highs_and_lows_are_never_mixed():
    r = Rig(params=params(716, 0.0, 0.01))
    r.warm(20, tr=1.0)
    r.confirm([(SWING_HIGH, 5000.0, 20)])
    assert r.confirm([(SWING_LOW, 5000.0, 22)]) == []
    assert r.d.state.clusters_emitted == 0
    assert len(r.d.pending_swing_highs()) == 1 and len(r.d.pending_swing_lows()) == 1


def test_min_touches_and_multi_touch_counts():
    r = Rig(params=params(716, 0.0, 0.01))
    r.warm(20, tr=1.0)
    assert r.confirm([(SWING_HIGH, 5000.0, 20)]) == []
    two = r.confirm([(SWING_HIGH, 5001.0, 22)])
    three = r.confirm([(SWING_HIGH, 5002.0, 24)])
    assert [e.payload["touch_count"] for e in two + three] == [2, 3]
    r3 = Rig(params=params(716, 0.0, 0.01, min_touches=3))
    r3.warm(20, tr=1.0)
    r3.confirm([(SWING_HIGH, 5000.0, 20)])
    assert r3.confirm([(SWING_HIGH, 5001.0, 22)]) == []
    assert r3.confirm([(SWING_HIGH, 5002.0, 24)])[0].payload["touch_count"] == 3


# =========================================================================== swing-input discipline
def test_duplicate_swing_never_forms_a_cluster_with_itself():
    r = Rig(params=params(716, 0.0, 0.01))
    r.warm(20, tr=1.0)
    c = r.candle(1.0)
    ev = r.swing(SWING_HIGH, 5000.0, 20, r.close_of(c))
    assert r.d.step_with_swings(c, [ev, ev])[1] == []           # same event twice in one batch
    assert r.d.state.clusters_emitted == 0 and len(r.d.pending_swing_highs()) == 1
    assert r.d.state.duplicate_swings_ignored == 1
    assert r.d.on_swing_events([ev]) == []                      # and redelivered later
    assert len(r.d.pending_swing_highs()) == 1 and r.d.state.duplicate_swings_ignored == 2


def test_conflicting_duplicate_swing_rejected_without_state_change():
    r = Rig(params=params())
    r.warm(20)
    c = r.candle()
    a = r.swing(SWING_HIGH, 5000.0, 20, r.close_of(c))
    r.d.step_with_swings(c, [a])
    before = r.d.snapshot()
    with pytest.raises(DetectorInputError, match="conflicting"):
        r.d.on_swing_events([r.swing(SWING_HIGH, 5001.0, 20, r.close_of(c))])
    assert r.d.snapshot() == before


@pytest.mark.parametrize("payload", [{}, {"price": 5000.0}, {"bar_index": 5}, {"price": float("nan"), "bar_index": 5},
                                     {"price": float("inf"), "bar_index": 5}, {"price": "5000", "bar_index": 5},
                                     {"price": 5000.0, "bar_index": "5"}, {"price": 5000.0, "bar_index": -1},
                                     {"price": True, "bar_index": 5}, {"price": 5000.0, "bar_index": 5.5}])
def test_malformed_swing_payload_rejected_and_state_untouched(payload):
    r = Rig(params=params())
    r.warm(20)
    c = r.candle()
    r.d.step(c)
    good = r.swing(SWING_HIGH, 5000.0, 20, r.close_of(c))
    bad = DetectorEvent(SWING_HIGH, "V75", "M5", r.close_of(c), payload)
    before = r.d.snapshot()
    with pytest.raises(DetectorInputError, match="malformed"):
        r.d.on_swing_events([good, bad])  # the valid first event must not be half-applied
    assert r.d.snapshot() == before


def test_batch_is_atomic_when_a_later_event_is_rejected():
    r = Rig(params=params(716, 0.0, 0.01))
    r.warm(20, tr=1.0)
    c = r.candle(1.0)
    ok = r.swing(SWING_HIGH, 5000.0, 20, r.close_of(c))
    foreign = r.swing(SWING_LOW, 4000.0, 21, r.close_of(c))
    foreign = DetectorEvent(foreign.type, "V100", "M3", foreign.confirmation_time, foreign.payload)
    before = r.d.snapshot()
    with pytest.raises(DetectorInputError, match="does not belong"):
        r.d.step_with_swings(c, [ok, foreign])
    assert r.d.snapshot() == before  # neither the candle nor the first swing was applied
    r.d.step_with_swings(c, [ok])    # a correct retry works and is not seen as a duplicate
    assert len(r.d.pending_swing_highs()) == 1


def test_wrong_symbol_or_timeframe_and_non_swing_events():
    r = Rig(params=params())
    r.warm(20)
    c = r.candle()
    for sym, tf in (("V100", "M5"), ("V75", "H1")):
        ev = make_swing_event(SWING_HIGH, sym, tf, r.close_of(c), 5000.0, 20, c.time, 22)
        with pytest.raises(DetectorInputError, match="does not belong"):
            r.d.step_with_swings(c, [ev])
    noise = DetectorEvent("SOMETHING_ELSE", "V75", "M5", r.close_of(c), {})
    assert r.d.step_with_swings(c, [noise])[1] == [] and r.d.state.swings_received == 0
    with pytest.raises(DetectorInputError):
        r.d.on_swing_events(["not an event"])  # type: ignore[list-item]


def test_out_of_order_pivots_rejected():
    r = Rig(params=params())
    r.warm(20)
    r.confirm([(SWING_HIGH, 5000.0, 30)])
    with pytest.raises(DetectorInputError, match="out-of-order"):
        r.confirm([(SWING_HIGH, 5001.0, 25)])


def test_swing_before_any_candle_rejected():
    d = EqhEqlDetector("V75", "M5", params())
    ev = make_swing_event(SWING_HIGH, "V75", "M5", T0, 5000.0, 2, T0, 4)
    with pytest.raises(DetectorInputError, match="before any candle"):
        d.on_swing_events([ev])


# =========================================================================== causality
def test_swing_must_be_confirmed_by_the_last_processed_candle():
    r = Rig(params=params())
    r.warm(20)
    c = r.candle()
    early = r.swing(SWING_HIGH, 5000.0, 20, r.close_of(c) + timedelta(minutes=5))   # confirming candle not seen yet
    with pytest.raises(DetectorInputError, match="before its confirming candle"):
        r.d.step_with_swings(c, [early])
    r.d.step(c)
    stale = r.swing(SWING_HIGH, 5000.0, 20, r.close_of(c) - timedelta(minutes=5))
    with pytest.raises(DetectorInputError, match="look-ahead"):
        r.d.on_swing_events([stale])  # ATR would already include later candles


def test_delivering_old_swings_after_all_candles_is_rejected_not_silently_look_ahead():
    """The original detector accepted this and used the final ATR for every swing."""
    s = walk(200, 3, "V75", "M5")
    sd = create_swing_detector(load_config(), "V75", "M5")
    swings = [e for c in s for e in sd.step(c)[1]]
    d = create_eqh_eql_detector(load_config(), "V75", "M5")
    for c in s:
        d.step(c)
    with pytest.raises(DetectorInputError):
        d.on_swing_events(swings)


def test_tolerance_uses_atr_at_the_confirmation_candle_not_later_volatility():
    r = Rig(params=params(1, 1.0, 0.01))  # floor 0.01, tolerance ~= ATR
    r.warm(20, tr=10.0)
    r.confirm([(SWING_HIGH, 5000.0, 20)], tr=10.0)
    ev = r.confirm([(SWING_HIGH, 5008.0, 22)], tr=10.0)[0]
    assert ev.payload["tolerance_used"] == pytest.approx(10.0)   # ATR at that candle
    r.warm(30, tr=1000.0)                                        # later turbulence...
    assert r.d.current_tolerance() > 100
    assert ev.payload["tolerance_used"] == pytest.approx(10.0)   # ...changes nothing already emitted


def test_confirmation_time_is_the_second_swings_and_event_is_not_premature():
    r = Rig(params=params(716, 0.0, 0.01))
    r.warm(20, tr=1.0)
    assert r.confirm([(SWING_HIGH, 5000.0, 20)]) == []
    c = r.candle(1.0)
    second = r.swing(SWING_HIGH, 5001.0, 25, r.close_of(c))
    assert r.d.step(c)[1] == []                       # candle alone never emits
    out = r.d.on_swing_events([second])
    assert out[0].confirmation_time == second.confirmation_time == r.close_of(c)


def _oracle(series, cfg, symbol, tf, max_kept=500):
    """Independent brute-force EQ computation from raw swings + an independent Wilder ATR."""
    p = get_eqh_eql_params(cfg, symbol, tf)
    sd = create_swing_detector(cfg, symbol, tf)
    atr = _wilder_atr(list(series.highs), list(series.lows), list(series.closes))
    pos = {int(b): i for i, b in enumerate(series.indices)}
    out, hist = [], {SWING_HIGH: [], SWING_LOW: []}
    for c in series:
        for e in sd.step(c)[1]:
            a = atr[pos[e.payload["confirmation_bar_index"]]]
            price = e.payload["price"]
            prior = hist[e.type][-max_kept:]
            if a is not None:
                tol = max(p["tolerance_points"] * p["point_size"], p["tolerance_atr_mult"] * a)
                mem = [q for q in prior if abs(q[1] - price) <= tol + 1e-9]
                if mem:
                    out.append((e.type, e.confirmation_time, sorted([q[0] for q in mem] + [e.payload["bar_index"]]), len(mem) + 1))
            hist[e.type].append((e.payload["bar_index"], price))
    return out


@pytest.mark.parametrize("seed,sym,tf", [(1, "V75", "M5"), (2, "V100", "M3"), (3, "V50", "M15"), (4, "V75", "H1")])
def test_pipeline_equals_independent_oracle(config, seed, sym, tf):
    s = walk(1500, seed, sym, tf)
    _, clusters = replay_swing_eqh_eql(s, create_swing_detector(config, sym, tf), create_eqh_eql_detector(config, sym, tf))
    got = [("SWING_HIGH" if e.payload["side"] == "high" else "SWING_LOW", e.confirmation_time,
            e.payload["constituent_bar_indices"], e.payload["touch_count"]) for e in clusters]
    want = _oracle(s, config, sym, tf)
    assert len(want) > 20 and got == want


@pytest.mark.parametrize("cut", [150, 333, 777, 1100])
def test_truncation_no_lookahead_through_the_real_pipeline(config, cut):
    s = walk(1500, 11, "V75", "M5")
    full = replay_swing_eqh_eql(s, create_swing_detector(config, "V75", "M5"), create_eqh_eql_detector(config, "V75", "M5"))[1]
    part = replay_swing_eqh_eql(s.slice(0, cut), create_swing_detector(config, "V75", "M5"),
                                create_eqh_eql_detector(config, "V75", "M5"))[1]
    close_cut = s[cut - 1].time + timedelta(seconds=300)
    expected = [e for e in full if e.confirmation_time <= close_cut]
    assert [e.to_dict() for e in part] == [e.to_dict() for e in expected] and expected


# =========================================================================== streaming / batch / determinism / snapshot
def _events_json(evs):
    return json.dumps([e.to_dict() for e in evs], sort_keys=True)


def test_batch_equals_streaming_equals_manual_loop(config):
    s = walk(1200, 7, "V100", "M3")
    mk = lambda: (create_swing_detector(config, "V100", "M3"), create_eqh_eql_detector(config, "V100", "M3"))
    batch = replay_swing_eqh_eql(s, *mk())
    sw, eq = mk()
    streamed_s, streamed_e = [], []
    for _, a, b in stream_swing_eqh_eql(list(s), sw, eq):
        streamed_s += a
        streamed_e += b
    sw2, eq2 = mk()
    manual = []
    for c in s:
        manual += eq2.step_with_swings(c, sw2.step(c)[1])[1]
    assert _events_json(batch[1]) == _events_json(streamed_e) == _events_json(manual) and batch[1]
    assert _events_json(batch[0]) == _events_json(streamed_s)


def test_deterministic_across_runs(config):
    s = walk(900, 9, "V50", "M15")
    run = lambda: replay_swing_eqh_eql(s, create_swing_detector(config, "V50", "M15"), create_eqh_eql_detector(config, "V50", "M15"))
    (_, a), (_, b) = run(), run()
    assert _events_json(a) == _events_json(b) and a


def test_snapshot_restore_mid_stream_equals_uninterrupted_and_never_duplicates(config):
    s = tie_rich_series(1500, 4, "V75", "M5")
    candles = list(s)
    mk = lambda: (create_swing_detector(config, "V75", "M5"), create_eqh_eql_detector(config, "V75", "M5"))
    sw, eq = mk()
    whole = []
    for c in candles:
        whole += eq.step_with_swings(c, sw.step(c)[1])[1]
    assert len(whole) > 50
    for cut in (97, 600, 1203):
        sw, eq = mk()
        first = []
        for c in candles[:cut]:
            first += eq.step_with_swings(c, sw.step(c)[1])[1]
        snap_s, snap_e = sw.snapshot(), json.loads(json.dumps(eq.snapshot()))  # must survive JSON
        sw2, eq2 = mk()
        sw2.restore(snap_s)
        eq2.restore(snap_e)
        second = []
        for c in candles[cut:]:
            second += eq2.step_with_swings(c, sw2.step(c)[1])[1]
        assert _events_json(first + second) == _events_json(whole), cut
        # re-feeding the last pre-snapshot candle after restore must not duplicate anything
        sw3, eq3 = mk()
        sw3.restore(snap_s)
        eq3.restore(snap_e)
        assert eq3.step_with_swings(candles[cut - 1], sw3.step(candles[cut - 1])[1])[1] == []
        assert eq3.snapshot()["clusters_emitted"] == snap_e["clusters_emitted"]


def test_snapshot_captures_state_and_reset_clears_it(config):
    r = Rig(config=config)
    r.warm(20)
    r.confirm([(SWING_HIGH, 5000.0, 20)])
    r.confirm([(SWING_HIGH, 5001.0, 22)])
    snap = r.d.snapshot()
    assert snap["clusters_emitted"] == 1 and len(snap["swing_highs"]) == 2 and snap["atr"] is not None
    assert snap["last_ohlc"] is not None and len(snap["tr_window"]) == 14
    r.d.reset()
    assert r.d.state.clusters_emitted == 0 and r.d.state.atr is None and not r.d.pending_swing_highs()
    r.d.restore(snap)
    assert r.d.snapshot() == snap


def test_restore_rejects_mismatch_incomplete_and_corrupt_and_keeps_state(config):
    r = Rig(config=config)
    r.warm(20)
    snap = r.d.snapshot()
    other = Rig("V100", "M3", config=config)
    for target, s in ((other.d, snap),):
        with pytest.raises(ValueError, match="does not match"):
            target.restore(s)
    wrong_tf = EqhEqlDetector("V75", "H1", get_eqh_eql_params(config, "V75", "H1"))
    with pytest.raises(ValueError, match="does not match"):
        wrong_tf.restore(snap)
    changed = Rig("V75", "M5", params=params(100, 0.1, 0.01))
    with pytest.raises(ValueError, match="does not match"):
        changed.d.restore(snap)  # same instrument, different tolerance parameters
    before = r.d.snapshot()
    with pytest.raises(ValueError, match="incomplete"):
        r.d.restore({"symbol": "V75", "timeframe": "M5"})
    with pytest.raises(ValueError, match="incomplete"):
        r.d.restore({k: v for k, v in snap.items() if k != "atr"})
    bad = dict(snap, tr_window=["x"])
    with pytest.raises(ValueError, match="corrupt"):
        r.d.restore(bad)
    bad = dict(snap, swing_highs=[{"type": "SWING_LOW", "symbol": "V75", "timeframe": "M5",
                                   "confirmation_time": T0.isoformat(), "payload": {"price": 1.0, "bar_index": 1}}])
    with pytest.raises(ValueError, match="corrupt"):
        r.d.restore(bad)
    with pytest.raises(ValueError):
        r.d.restore("nope")  # type: ignore[arg-type]
    assert r.d.snapshot() == before


# =========================================================================== state isolation
def test_state_isolated_across_instruments_and_timeframes(config):
    pairs = [("V75", "M5"), ("V75", "H1"), ("V100", "M3"), ("V50", "M15")]
    series = {k: walk(700, 20 + i, *k) for i, k in enumerate(pairs)}
    solo = {}
    for k in pairs:
        solo[k] = _events_json(replay_swing_eqh_eql(series[k], create_swing_detector(config, *k), create_eqh_eql_detector(config, *k))[1])
    sw = {k: create_swing_detector(config, *k) for k in pairs}
    eq = {k: create_eqh_eql_detector(config, *k) for k in pairs}
    got = {k: [] for k in pairs}
    for i in range(700):             # interleave every stream candle by candle
        for k in pairs:
            c = series[k][i]
            got[k] += eq[k].step_with_swings(c, sw[k].step(c)[1])[1]
    for k in pairs:
        assert _events_json(got[k]) == solo[k] and got[k], k
    assert eq[("V75", "M5")].state is not eq[("V75", "H1")].state


def test_pipeline_refuses_mismatched_detectors(config):
    s = walk(100, 1, "V75", "M5")
    with pytest.raises(ValueError):
        replay_swing_eqh_eql(s, create_swing_detector(config, "V75", "M5"), create_eqh_eql_detector(config, "V100", "M3"))
    with pytest.raises(ValueError):
        replay_swing_eqh_eql(s, create_swing_detector(config, "V100", "M3"), create_eqh_eql_detector(config, "V75", "M5"))


# =========================================================================== candle input reliability
def test_invalid_candles_rejected_with_state_untouched():
    d = EqhEqlDetector("V75", "M5", params())
    cs = [Candle(T0 + timedelta(minutes=5 * i), 100, 101, 99, 100, 1.0, i, True) for i in range(20)]
    for c in cs[:16]:
        d.step(c)
    before = d.snapshot()
    nxt = T0 + timedelta(minutes=5 * 16)
    bads = [Candle(nxt, 100, float("nan"), 99, 100, 1.0, 16, True),
            Candle(nxt, 100, 98, 99, 100, 1.0, 16, True),                       # high < low
            Candle(nxt, 100, 101, 99, 100, 1.0, 16, False),                     # not closed
            Candle(T0 + timedelta(minutes=5 * 5), 100, 101, 99, 100, 1.0, 5, True),   # late
            Candle(nxt, 100, 101, 99, 100, 1.0, 10, True),                      # index goes backwards
            Candle(T0 + timedelta(minutes=5 * 15 + 1), 100, 101, 99, 100, 1.0, 16, True)]  # shorter than tf
    for bad in bads:
        with pytest.raises(DetectorInputError):
            d.step(bad)
        assert d.snapshot() == before
    with pytest.raises(DetectorInputError):
        d.step(None)  # type: ignore[arg-type]
    d.step(cs[16])
    assert math.isfinite(d.state.atr)  # a rejected NaN candle did not poison ATR


def test_duplicate_candle_idempotent_but_conflicting_duplicate_rejected():
    d = EqhEqlDetector("V75", "M5", params())
    c = Candle(T0, 100, 101, 99, 100, 1.0, 0, True)
    d.step(c)
    d.step(c)
    assert d.state.duplicate_candles_ignored == 1 and d.state.last_processed_index == 0
    with pytest.raises(DetectorInputError, match="conflicting duplicate"):
        d.step(Candle(T0, 100, 105, 99, 100, 1.0, 0, True))


# =========================================================================== integration with the real swing detector / data
def test_real_swing_detector_feeds_eqh_eql_end_to_end(config):
    s = tie_rich_series(800, 2, "V75", "M5")
    swings, clusters = replay_swing_eqh_eql(s, create_swing_detector(config, "V75", "M5"), create_eqh_eql_detector(config, "V75", "M5"))
    assert swings and clusters
    by_conf = {}
    for e in swings:
        by_conf.setdefault((e.type, e.payload["bar_index"]), e)
    for cl in clusters:
        side = SWING_HIGH if cl.payload["side"] == "high" else SWING_LOW
        newest = cl.payload["constituent_bar_indices"][-1]
        src = by_conf[(side, newest)]                       # every cluster traces to a real swing event
        assert cl.confirmation_time == src.confirmation_time
        assert cl.payload["confirmation_bar_index"] == src.payload["confirmation_bar_index"]
        for b, p in zip(cl.payload["constituent_bar_indices"], cl.payload["constituent_prices"]):
            assert by_conf[(side, b)].payload["price"] == p
            assert abs(p - src.payload["price"]) <= cl.payload["tolerance_used"] + 1e-9


def test_eqh_eql_does_not_change_swing_output(config):
    s = walk(800, 6, "V50", "M15")
    sd = create_swing_detector(config, "V50", "M15")
    ref = [e.to_dict() for e in sd.process_series(list(s))[1]]
    swings, _ = replay_swing_eqh_eql(s, create_swing_detector(config, "V50", "M15"), create_eqh_eql_detector(config, "V50", "M15"))
    assert [e.to_dict() for e in swings] == ref


def test_real_supplied_data_pipeline_runs_and_is_consistent(config):
    csv = ROOT / "data" / "V50_H1.csv"
    if not csv.exists():
        pytest.skip("supplied data not present")
    s = OHLCSeries.from_csv(csv, "V50", "H1")
    swings, clusters = replay_swing_eqh_eql(s, create_swing_detector(config, "V50", "H1"), create_eqh_eql_detector(config, "V50", "H1"))
    assert 0 < len(clusters) < len(swings)
    assert _events_json(clusters) == _events_json(replay_swing_eqh_eql(
        s, create_swing_detector(config, "V50", "H1"), create_eqh_eql_detector(config, "V50", "H1"))[1])
