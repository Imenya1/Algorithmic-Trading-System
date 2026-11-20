"""Fixed visual review set (Build Order Section 5).

Builds a versioned, reproducible list of windows for developer chart review:
largest-range bars, first and last bars of each year, windows around supplied cut points
(e.g. those used by the no-lookahead test) and a seeded random sample.

Not available without reference labels: "windows with the largest disagreement against
the reference-labelled sample". This is reported as pending, never invented.

Data restriction: every window must end at or before ``review_until``, the end of the
validation segment. The holdout segment must never be reviewed.
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .data.ohlc import OHLCSeries
from .events import DetectorEvent
from .visualiser import plot_swings

REVIEW_SET_VERSION = 1


def build_review_set(
    series: OHLCSeries,
    review_until: datetime,
    seed: int,
    cut_points: Sequence[int] = (),
    window_bars: int = 120,
    top_k_range: int = 5,
    n_random: int = 5,
) -> Dict[str, Any]:
    """Return the review set as a JSON-serialisable dict. ``cut_points`` are absolute bar indices."""
    allowed = [c for c in series if c.time <= review_until]
    if not allowed:
        raise ValueError("No candles at or before review_until; nothing may be reviewed")
    first_ok, last_ok = allowed[0].index, allowed[-1].index
    half = window_bars // 2

    def window_around(bar: int) -> Dict[str, int]:
        lo = max(first_ok, bar - half)
        hi = min(last_ok, lo + window_bars - 1)
        lo = max(first_ok, hi - window_bars + 1)
        return {"first_bar": int(lo), "last_bar": int(hi)}

    windows: List[Dict[str, Any]] = []
    by_range = sorted(allowed, key=lambda c: (-(c.high - c.low), c.index))[:top_k_range]
    for c in by_range:
        windows.append({"kind": "largest_range_bar", "centre_bar": int(c.index), **window_around(c.index)})

    years: Dict[int, List[int]] = {}
    for c in allowed:
        years.setdefault(c.time.year, []).append(c.index)
    for year in sorted(years):
        windows.append({"kind": "year_first_bars", "year": year,
                        "first_bar": int(years[year][0]),
                        "last_bar": int(min(years[year][0] + window_bars - 1, last_ok))})
        windows.append({"kind": "year_last_bars", "year": year,
                        "first_bar": int(max(years[year][-1] - window_bars + 1, first_ok)),
                        "last_bar": int(years[year][-1])})
    for cut in cut_points:
        if first_ok <= cut <= last_ok:
            windows.append({"kind": "cut_point", "centre_bar": int(cut), **window_around(cut)})

    rng = random.Random(seed)
    for centre in sorted(rng.sample(range(first_ok, last_ok + 1), min(n_random, last_ok - first_ok + 1))):
        windows.append({"kind": "random", "centre_bar": int(centre), **window_around(centre)})

    return {
        "version": REVIEW_SET_VERSION, "symbol": series.symbol, "timeframe": series.timeframe,
        "seed": seed, "review_until": review_until.isoformat(), "window_bars": window_bars,
        "windows": windows,
        "largest_disagreement_windows": "pending: no reference-labelled sample available",
    }


def render_review_set(series: OHLCSeries, events: Sequence[DetectorEvent],
                      review_set: Dict[str, Any], out_dir: Path) -> List[Path]:
    """Render every window to a PNG using the detector's actual events."""
    out_dir = Path(out_dir)
    paths: List[Path] = []
    for i, w in enumerate(review_set["windows"]):
        name = f"{series.symbol}_{series.timeframe}_{i:03d}_{w['kind']}.png"
        paths.append(plot_swings(series, events, window=(w["first_bar"], w["last_bar"]),
                                 save_path=out_dir / name, dpi=110,
                                 title=f"{series.symbol} . {series.timeframe} . review window {i} ({w['kind']})"))
    return paths
