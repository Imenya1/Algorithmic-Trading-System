"""No-lookahead test required by Phase 2.

Truncate historical data at hundreds of random cut points.
At every cut point, compare the earlier detector output with the
corresponding output from the full-history run.
Previously confirmed records must remain unchanged.
Must pass 100%.
"""

from __future__ import annotations

import sys
from pathlib import Path
import random

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_system.detectors.swing import SwingDetector
from trading_system.utils.synthetic import generate_random_walk
from trading_system.replay import replay_series


def test_no_lookahead_hundreds_of_cuts():
    series = generate_random_walk(n_bars=500, seed=2026, symbol="V75", timeframe="M5")
    n = len(series)
    assert n >= 100

    # Full history reference
    det_full = SwingDetector(symbol="V75", timeframe="M5", params={"n": 2})
    events_full = replay_series(series, det_full)
    full_map = {
        (e.type, e.payload["bar_index"]): (
            e.payload["price"],
            e.confirmation_time.isoformat(),
        )
        for e in events_full
    }

    rng = random.Random(42)
    # Hundreds of random cut points
    cuts = sorted(set(rng.randint(30, n - 10) for _ in range(200)))
    failures = []

    for cut in cuts:
        prefix = series.slice(0, cut)
        det = SwingDetector(symbol="V75", timeframe="M5", params={"n": 2})
        events_prefix = replay_series(prefix, det)

        for e in events_prefix:
            key = (e.type, e.payload["bar_index"])
            if key not in full_map:
                failures.append(f"cut={cut}: event {key} missing from full run")
                continue
            price_full, conf_full = full_map[key]
            if abs(price_full - e.payload["price"]) > 1e-9:
                failures.append(f"cut={cut}: price changed for {key}")
            if conf_full != e.confirmation_time.isoformat():
                failures.append(f"cut={cut}: confirmation_time changed for {key}")

    assert len(failures) == 0, (
        f"No-lookahead failed on {len(failures)} checks. "
        f"First few: {failures[:5]}"
    )
