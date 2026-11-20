"""detectors.swing.tie_rule: 'strict' (default) and 'mt5' (Fractals.mq5 convention).

mt5 rule: pivot strictly beyond the n bars AFTER it, equal-or-beyond the n bars BEFORE it.
The oracle below is written independently of the detector (plain loops over arrays).
"""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from tests.helpers import make_series, tie_rich_series, walk
from trading_system.config import ConfigError, get_swing_params, load_config, validate_config
from trading_system.data.ohlc import OHLCSeries
from trading_system.detectors.swing import SwingDetector
from trading_system.replay import create_swing_detector, replay_series

ROOT = Path(__file__).resolve().parents[1]


def oracle(series, n, rule):
    h = [c.high for c in series]
    l = [c.low for c in series]
    highs, lows = set(), set()
    for p in range(n, len(h) - n):
        left_h, right_h = h[p - n:p], h[p + 1:p + n + 1]
        left_l, right_l = l[p - n:p], l[p + 1:p + n + 1]
        if rule == "strict":
            if all(h[p] > x for x in left_h + right_h):
                highs.add(p)
            if all(l[p] < x for x in left_l + right_l):
                lows.add(p)
        else:
            if all(h[p] >= x for x in left_h) and all(h[p] > x for x in right_h):
                highs.add(p)
            if all(l[p] <= x for x in left_l) and all(l[p] < x for x in right_l):
                lows.add(p)
    return highs, lows


def got(events):
    return ({e.payload["bar_index"] for e in events if e.type == "SWING_HIGH"},
            {e.payload["bar_index"] for e in events if e.type == "SWING_LOW"})


@pytest.mark.parametrize("rule", ["strict", "mt5"])
@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_matches_independent_oracle_on_tie_rich_data(rule, n, seed):
    s = tie_rich_series(400, seed)
    det = SwingDetector("T", "M5", {"n": n, "tie_rule": rule})
    assert got(replay_series(s, det)) == oracle(s, n, rule)


def test_default_is_strict_and_unchanged():
    s = tie_rich_series(400, 7)
    a = replay_series(s, SwingDetector("T", "M5", {"n": 2}))
    b = replay_series(s, SwingDetector("T", "M5", {"n": 2, "tie_rule": "strict"}))
    assert [e.to_dict() for e in a] == [e.to_dict() for e in b]
    assert SwingDetector("T", "M5", {"n": 2}).tie_rule == "strict"


def test_every_strict_swing_is_also_an_mt5_swing():
    s = tie_rich_series(600, 11)
    sh, sl = got(replay_series(s, SwingDetector("T", "M5", {"n": 2, "tie_rule": "strict"})))
    mh, ml = got(replay_series(s, SwingDetector("T", "M5", {"n": 2, "tie_rule": "mt5"})))
    assert sh <= mh and sl <= ml


def test_mt5_equal_on_left_allowed_equal_on_right_rejected():
    #           0  1  2  3  4
    highs_left_eq = [9, 5, 9, 4, 3]     # equal high 2 bars before the pivot -> allowed in mt5
    highs_right_eq = [3, 4, 9, 5, 9]    # equal high 2 bars after the pivot  -> rejected in mt5
    lows = [1] * 5
    for rule, expect_left, expect_right in (("strict", set(), set()), ("mt5", {2}, set())):
        d1 = SwingDetector("T", "M5", {"n": 2, "tie_rule": rule})
        d2 = SwingDetector("T", "M5", {"n": 2, "tie_rule": rule})
        assert got(replay_series(make_series(highs_left_eq, lows), d1))[0] == expect_left
        assert got(replay_series(make_series(highs_right_eq, lows), d2))[0] == expect_right


@pytest.mark.parametrize("rule", ["strict", "mt5"])
def test_streaming_equals_batch_and_no_lookahead(rule):
    s = tie_rich_series(300, 5)
    full = replay_series(s, SwingDetector("T", "M5", {"n": 2, "tie_rule": rule}))
    rng = np.random.default_rng(0)
    for cut in rng.integers(20, 299, 40):
        part = OHLCSeries(list(s)[:int(cut)], "T", "M5")
        ev = replay_series(part, SwingDetector("T", "M5", {"n": 2, "tie_rule": rule}))
        assert [e.to_dict() for e in ev] == [e.to_dict() for e in full][:len(ev)]


@pytest.mark.parametrize("rule", ["strict", "mt5"])
def test_snapshot_roundtrip_and_mismatch_rejected(rule):
    s = walk(200, 3)
    det = SwingDetector("T", "M5", {"n": 2, "tie_rule": rule})
    for c in list(s)[:100]:
        det.step(c)
    snap = json.loads(json.dumps(det.snapshot()))
    assert snap["tie_rule"] == rule
    other = "mt5" if rule == "strict" else "strict"
    with pytest.raises(ValueError):
        SwingDetector("T", "M5", {"n": 2, "tie_rule": other}).restore(snap)
    fresh = SwingDetector("T", "M5", {"n": 2, "tie_rule": rule})
    fresh.restore(snap)
    rest_a = [e for c in list(s)[100:] for e in det.step(c)[1]]
    rest_b = [e for c in list(s)[100:] for e in fresh.step(c)[1]]
    assert [e.to_dict() for e in rest_a] == [e.to_dict() for e in rest_b]


def test_old_snapshot_without_tie_rule_restores_into_strict_only():
    det = SwingDetector("T", "M5", {"n": 2})
    for c in walk(50, 1):
        det.step(c)
    snap = det.snapshot()
    del snap["tie_rule"]
    SwingDetector("T", "M5", {"n": 2}).restore(snap)
    with pytest.raises(ValueError):
        SwingDetector("T", "M5", {"n": 2, "tie_rule": "mt5"}).restore(snap)


def test_invalid_tie_rule_rejected_everywhere():
    with pytest.raises(ValueError):
        SwingDetector("T", "M5", {"n": 2, "tie_rule": "loose"})
    cfg = copy.deepcopy(load_config())
    cfg.setdefault("detectors", {}).setdefault("swing", {})["tie_rule"] = "loose"
    with pytest.raises(ConfigError, match="tie_rule"):
        validate_config(cfg)


def test_config_flows_into_detector():
    cfg = copy.deepcopy(load_config())
    assert get_swing_params(cfg, "V75", "H1")["tie_rule"] == "strict"
    cfg["detectors"]["swing"]["tie_rule"] = "mt5"
    assert create_swing_detector(validate_config(cfg), "V75", "H1").tie_rule == "mt5"


def test_mt5_n2_on_supplied_v75_h1_matches_oracle():
    s = OHLCSeries.from_csv(ROOT / "data" / "V75_H1.csv", "V75", "H1")
    ev = replay_series(s, SwingDetector("V75", "H1", {"n": 2, "tie_rule": "mt5"}))
    assert got(ev) == oracle(s, 2, "mt5")
