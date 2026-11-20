"""Gap handling (agreed design): every gap is bridged, flagged and counted; nothing is
invented; each timeframe handles its own gaps; batch == streaming; no lookahead."""

from __future__ import annotations

import json
import random
from datetime import timedelta

import pytest

from checks import truncation_check, validity_check
from helpers import T0, dicts, oracle, walk
from trading_system.config import load_config, timeframe_seconds
from trading_system.data.ohlc import Candle, OHLCSeries
from trading_system.detectors.base import DetectorInputError
from trading_system.detectors.swing import GAP_LOG_SIZE, SwingDetector
from trading_system.events import SWING_HIGH
from trading_system.replay import DetectorBank, replay_series, stream_candles


def with_gaps(series: OHLCSeries, drop, mark_confirmed=()):
    """New series with the candles at the given positions removed (a genuine hole in the data).
    Candles in ``mark_confirmed`` (positions in the original) carry the upstream marker."""
    kept = []
    for i, c in enumerate(series):
        if i in drop:
            continue
        kept.append(Candle(c.time, c.open, c.high, c.low, c.close, c.volume, None, True, i in mark_confirmed))
    return OHLCSeries(kept, series.symbol, series.timeframe)


def expected_gap_flags(series: OHLCSeries, n: int, tf_s: int):
    """Independent expectation: swing (pivot p, confirmed p+n) spans a gap iff any two consecutive
    bars inside [p-n, p+n] are more than one timeframe apart. Returns {(type,p): missing}."""
    out = {}
    for typ, p, conf in oracle(series.highs, series.lows, n):
        miss = 0
        for j in range(p - n + 1, p + n + 1):
            d = (series[j].time - series[j - 1].time).total_seconds()
            miss += max(round(d / tf_s) - 1, 0)
        out[(typ, p)] = miss
    return out


@pytest.mark.parametrize("n,tf", [(2, "M5"), (3, "H1"), (2, "M3")])
def test_bridge_runs_over_gaps_of_any_size_and_matches_oracle_on_existing_bars(n, tf):
    base = walk(600, 3, "V75", tf)
    # a 1-candle, a 7-candle and a 200-candle (hours) hole
    series = with_gaps(base, set(range(100, 101)) | set(range(250, 257)) | set(range(400, 600 - 100)))
    events = replay_series(series, SwingDetector("V75", tf, {"n": n}))
    want = oracle(series.highs, series.lows, n)
    assert [(e.type, e.payload["bar_index"], e.payload["confirmation_bar_index"]) for e in events] == want
    st = None
    det = SwingDetector("V75", tf, {"n": n})
    replay_series(series, det)
    assert det.state.gaps_bridged == 3 and det.state.gaps_reset == 0 and det.state.missing_bars_total == 1 + 7 + 100
    assert [g.missing_bars for g in det.state.gap_log] == [1, 7, 100]


@pytest.mark.parametrize("n,tf", [(2, "M5"), (3, "H1")])
def test_only_swings_whose_window_spans_a_gap_are_flagged_with_exact_missing_count(n, tf):
    base = walk(500, 9, "V75", tf)
    series = with_gaps(base, {80, 81, 82, 300})
    events = replay_series(series, SwingDetector("V75", tf, {"n": n}))
    want = expected_gap_flags(series, n, timeframe_seconds(tf))
    assert any(v > 0 for v in want.values()) and any(v == 0 for v in want.values())
    for e in events:
        miss = want[(e.type, e.payload["bar_index"])]
        if miss:
            assert e.payload["spans_gap"] is True and e.payload["gap_missing_bars"] == miss
            assert e.payload["gap_status"] == "unverified"
        else:  # contract payload is unchanged for swings that span no gap
            assert not ({"spans_gap", "gap_missing_bars", "gap_status"} & set(e.payload))


def test_gap_confirmed_marker_gives_confirmed_status_and_mixed_gaps_are_unverified():
    base = walk(300, 4, "V75", "M5")
    # gap A (pos 100) marked confirmed upstream; gap B (pos 104) unmarked; gap C (pos 200) confirmed
    series = with_gaps(base, {99, 103, 199}, mark_confirmed={100, 200})
    events = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
    # After dropping originals 99, 103, 199 the gaps sit before new bars 99, 102 and 197.
    gap_before_bar = {99: True, 102: False, 197: True}          # bar -> marked confirmed upstream
    seen = set()
    for e in events:
        p, n = e.payload["bar_index"], 2
        spanned = [g for g in gap_before_bar if p - n + 1 <= g <= p + n]
        if not spanned:
            assert "gap_status" not in e.payload
            continue
        want = "confirmed" if all(gap_before_bar[g] for g in spanned) else "unverified"
        assert e.payload["gap_status"] == want and e.payload["gap_missing_bars"] == len(spanned)
        seen.add(want)
    assert seen == {"confirmed", "unverified"}
    det = SwingDetector("V75", "M5", {"n": 2})
    replay_series(series, det)
    assert (det.state.gaps_confirmed, det.state.gaps_unverified) == (2, 1)


def test_a_multi_hour_gap_does_not_stop_the_stream():
    base = walk(200, 1, "V100", "M3")
    series = with_gaps(base, set(range(50, 150)))  # 100 candles = 5 hours of M3
    det = SwingDetector("V100", "M3", {"n": 2})
    events = replay_series(series, det)
    assert events and det.state.missing_bars_total == 100
    spanning = [e for e in events if e.payload.get("spans_gap")]
    assert spanning and all(e.payload["gap_missing_bars"] == 100 for e in spanning)


def test_batch_equals_streaming_with_gaps_and_snapshot_restore_midstream():
    series = with_gaps(walk(800, 6, "V75", "M5"), {50, 51, 300, 301, 302, 303, 304, 600}, mark_confirmed={52, 305})
    cs = list(series)
    batch = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
    streamed = []
    for _, ev in stream_candles(cs, SwingDetector("V75", "M5", {"n": 2})):
        streamed += ev
    assert dicts(batch) == dicts(streamed)
    for cut in (10, 50, 51, 53, 200, 301, 304, 307, 500, 700):
        a = SwingDetector("V75", "M5", {"n": 2})
        head = a.process_series(cs[:cut])[1]
        b = SwingDetector("V75", "M5", {"n": 2})
        b.restore(json.loads(json.dumps(a.snapshot())))
        assert dicts(head + b.process_series(cs[cut:])[1]) == dicts(batch), f"cut={cut}"
        assert b.snapshot()["gap_log"] is not None


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_lookahead_and_no_repaint_on_gappy_data(seed):
    rng = random.Random(seed)
    base = walk(700, seed, "V75", "M5")
    series = with_gaps(base, {i for i in range(700) if rng.random() < 0.03})
    run = lambda s: replay_series(s, SwingDetector("V75", "M5", {"n": 2}))
    cuts = sorted(rng.sample(range(1, len(series)), 250))
    assert truncation_check(run, series, cuts) == []
    assert validity_check(run, series, 2) == []   # consecutive existing bars: still a true fractal


def test_each_timeframe_handles_its_own_gaps_and_is_not_affected_by_another():
    cfg = load_config()
    m5 = with_gaps(walk(400, 2, "V75", "M5"), {100, 101, 102})   # outage visible on M5 only
    h1 = walk(300, 3, "V75", "H1")                                # H1 for the same symbol is intact
    bank = DetectorBank(cfg)
    got_m5, got_h1 = [], []
    m5c, h1c = list(m5), list(h1)
    for i in range(max(len(m5c), len(h1c))):                       # interleave both streams
        if i < len(m5c):
            got_m5 += bank.on_candle("V75", "M5", m5c[i])
        if i < len(h1c):
            got_h1 += bank.on_candle("V75", "H1", h1c[i])
    solo_h1 = replay_series(h1, DetectorBank(cfg).detector("V75", "H1"))
    assert dicts(got_h1) == dicts(solo_h1)
    assert not any(e.payload.get("spans_gap") for e in got_h1)
    assert bank.detector("V75", "H1").state.gaps_bridged == 0
    assert bank.detector("V75", "M5").state.gaps_bridged == 1
    assert any(e.payload.get("spans_gap") for e in got_m5)


def test_gap_log_is_bounded():
    n_gaps = GAP_LOG_SIZE + 50
    t, cs = T0, []
    for i in range(n_gaps + 1):
        cs.append(Candle(t, 10, 12, 8, 11))
        t += timedelta(minutes=10)               # every step skips one M5 candle
    det = SwingDetector("T", "M5", {"n": 1})
    det.process_series(cs)
    assert det.state.gaps_bridged == n_gaps and len(det.state.gap_log) == GAP_LOG_SIZE


def test_spacing_shorter_than_timeframe_is_rejected_not_treated_as_a_gap():
    det = SwingDetector("T", "M5", {"n": 2})
    det.step(Candle(T0, 10, 12, 8, 11))
    with pytest.raises(DetectorInputError, match="shorter than"):
        det.step(Candle(T0 + timedelta(minutes=2), 10, 12, 8, 11))


def test_late_candle_after_a_bridged_gap_is_rejected_and_confirmed_swings_are_unchanged():
    base = walk(120, 8, "V75", "M5")
    cs = list(base)
    det = SwingDetector("V75", "M5", {"n": 2})
    events = []
    for i, c in enumerate(cs):
        if i == 60:
            continue                                   # candle 60 is delayed
        events += det.step(c)[1]
    before = det.snapshot()
    with pytest.raises(DetectorInputError, match="backfill"):
        det.step(cs[60])                               # arrives late: rejected, no repaint
    assert det.snapshot() == before


def test_reset_policy_never_flags_and_loses_swings_near_the_gap():
    series = with_gaps(walk(300, 5, "V75", "M5"), {100})
    bridged = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
    reset = replay_series(series, SwingDetector("V75", "M5", {"n": 2, "gap_policy": "reset"}))
    assert len(reset) <= len(bridged) and not any(e.payload.get("spans_gap") for e in reset)
    lost = {(e.type, e.payload["bar_index"]) for e in bridged} - {(e.type, e.payload["bar_index"]) for e in reset}
    assert lost == {(e.type, e.payload["bar_index"]) for e in bridged if e.payload.get("spans_gap")}


def test_shipped_default_is_bridge_and_config_policy_flows_to_detector():
    from trading_system.replay import create_swing_detector
    assert create_swing_detector(load_config(), "V75", "M5").gap_policy == "bridge"
