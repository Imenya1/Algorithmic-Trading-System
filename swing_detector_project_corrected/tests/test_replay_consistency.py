"""Batch vs streaming equality, determinism, snapshot/restore, replay safety."""

from __future__ import annotations

import hashlib
import json

import pytest

from helpers import dicts, walk
from trading_system.detectors.base import DetectorInputError
from trading_system.detectors.swing import SwingDetector
from trading_system.replay import replay_series, stream_candles

CASES = [(2, 1, "M5"), (3, 2, "H1"), (2, 3, "M3"), (3, 4, "M15")]


@pytest.mark.parametrize("n,seed,tf", CASES)
def test_batch_equals_streaming_complete_event_records(n, seed, tf):
    series = walk(800, seed, "V75", tf)
    batch = replay_series(series, SwingDetector("V75", tf, {"n": n}))
    streamed = []
    for _, evs in stream_candles(list(series), SwingDetector("V75", tf, {"n": n})):
        streamed.extend(evs)
    assert batch  # non-trivial
    assert dicts(batch) == dicts(streamed)  # type, symbol, timeframe, confirmation time, whole payload, ordering


@pytest.mark.parametrize("n,seed,tf", CASES)
def test_chunked_feeding_equals_single_pass(n, seed, tf):
    series = walk(500, seed, "V75", tf)
    cs = list(series)
    whole = SwingDetector("V75", tf, {"n": n}).process_series(cs)[1]
    det, parts = SwingDetector("V75", tf, {"n": n}), []
    for start in range(0, len(cs), 37):
        parts += det.process_series(cs[start:start + 37])[1]
    assert dicts(whole) == dicts(parts)


@pytest.mark.parametrize("n,seed,tf", CASES)
def test_snapshot_restore_midstream_equals_uninterrupted_run(n, seed, tf):
    cs = list(walk(500, seed, "V75", tf))
    straight = SwingDetector("V75", tf, {"n": n}).process_series(cs)[1]
    for cut in (1, n, 2 * n, 2 * n + 1, 250, 499):
        first = SwingDetector("V75", tf, {"n": n})
        head = first.process_series(cs[:cut])[1]
        snap = json.loads(json.dumps(first.snapshot()))  # must survive JSON
        second = SwingDetector("V75", tf, {"n": n})
        second.restore(snap)
        tail = second.process_series(cs[cut:])[1]
        assert dicts(head + tail) == dicts(straight), f"cut={cut}"


def test_snapshot_refuses_other_symbol_timeframe_or_n():
    det = SwingDetector("V75", "M5", {"n": 2})
    det.process_series(list(walk(20, 1, "V75", "M5")))
    snap = det.snapshot()
    for other in (SwingDetector("V100", "M5", {"n": 2}), SwingDetector("V75", "M15", {"n": 2}),
                  SwingDetector("V75", "M5", {"n": 3})):
        with pytest.raises(ValueError):
            other.restore(snap)


def test_determinism_ten_repeated_runs_identical_hash():
    series = walk(1500, 20261118, "V75", "M5")
    hashes = set()
    for _ in range(10):
        ev = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
        hashes.add(hashlib.sha256(json.dumps(dicts(ev), sort_keys=True).encode()).hexdigest())
    assert len(hashes) == 1


def test_re_replaying_without_reset_is_rejected_not_silently_duplicated():
    series = walk(200, 9, "V75", "M5")
    det = SwingDetector("V75", "M5", {"n": 2})
    first = replay_series(series, det)
    assert len(first) > 0
    with pytest.raises(DetectorInputError):  # candle 0 is now far out of order
        replay_series(series, det, reset=False)
    assert det.state.swing_high_count + det.state.swing_low_count == len(first)  # nothing re-emitted


def test_redelivery_of_the_latest_candle_creates_no_duplicate_event():
    series = walk(200, 9, "V75", "M5")
    det = SwingDetector("V75", "M5", {"n": 2})
    cs = list(series)
    total = 0
    for c in cs:
        total += len(det.step(c)[1])
        assert det.step(c)[1] == []  # at-least-once delivery: immediate redelivery is a no-op
    assert total == det.state.swing_high_count + det.state.swing_low_count
    assert det.state.duplicates_ignored == len(cs)


def test_replay_refuses_series_for_a_different_symbol_or_timeframe():
    series = walk(50, 1, "V100", "M3")
    with pytest.raises(ValueError):
        replay_series(series, SwingDetector("V75", "M3", {"n": 2}))
    with pytest.raises(ValueError):
        replay_series(series, SwingDetector("V100", "M5", {"n": 2}))
