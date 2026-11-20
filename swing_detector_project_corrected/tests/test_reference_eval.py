"""The evaluator is tested with hand-made TOY labels. These are test fixtures, not the
reference-labelled sample required by the Build Order, which has not been supplied."""

from __future__ import annotations

from trading_system.events import SWING_HIGH, SWING_LOW
from trading_system.reference_eval import score
from trading_system.replay import replay_series
from trading_system.detectors.swing import SwingDetector
from helpers import walk


def test_perfect_labels_pass_and_corrupted_labels_fail():
    series = walk(400, 3, "V75", "M5")
    events = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
    windows = [{"symbol": "V75", "timeframe": "M5", "first_bar": 0, "last_bar": 199, "window": "normal"},
               {"symbol": "V75", "timeframe": "M5", "first_bar": 200, "last_bar": 399, "window": "high_vol"}]
    labels = [{"symbol": "V75", "timeframe": "M5", "bar_index": e.payload["bar_index"], "type": e.type}
              for e in events]
    res = score(events, labels, windows, "V75", "M5")
    assert res["normal"]["precision"] == res["normal"]["recall"] == 1.0 and res["normal"]["status"] == "PASS"
    assert res["high_vol"]["status"] == "PASS"

    # Human labelled only 2/3 of what the detector found: the rest count as false positives.
    fewer = [l for i, l in enumerate(labels) if i % 3]
    res = score(events, fewer, windows, "V75", "M5")
    assert res["normal"]["status"] == "FAIL" and res["normal"]["precision"] < 0.9 and res["normal"]["recall"] == 1.0

    # Human labelled swings the detector never emitted: those are false negatives.
    detected = {(l["type"], l["bar_index"]) for l in labels}
    extra = [{"symbol": "V75", "timeframe": "M5", "bar_index": b, "type": SWING_HIGH}
             for b in range(0, 199) if (SWING_HIGH, b) not in detected][:60]
    res = score(events, labels + extra, windows, "V75", "M5")
    assert res["normal"]["status"] == "FAIL" and res["normal"]["recall"] < 0.9 and res["normal"]["precision"] == 1.0


def test_no_windows_means_pending_never_a_made_up_number():
    res = score([], [], [], "V75", "M5")
    assert res["normal"]["status"] == "PENDING" and res["high_vol"]["status"] == "PENDING"
