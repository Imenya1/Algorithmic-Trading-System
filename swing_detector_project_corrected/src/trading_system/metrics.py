"""Characterisation metrics for swing output, per symbol, timeframe and year.

Reported (Build Order Phase 2): swing density, leg-size distribution, confirmation lag.
Not computed, with the reason recorded in the output rather than a made-up number:
  * suppression / supersession rate: no suppression or supersession rule is defined.
  * largest-bar share: the Build Order does not define it.
  * EQH/EQL frequency: belongs to the separate EQH/EQL detector, not implemented here.

Implementation choices (not specification rules):
  * A "leg" is the absolute price difference between two swings that are consecutive in
    pivot order and of opposite type (high then low, or low then high).
  * A swing is assigned to the year of its pivot candle; bars to the year of their timestamp.
  * Confirmation lag is measured from the events (confirmation bar minus pivot bar), not assumed.
    ``pivot_to_confirmation_s_*`` is confirmation_time minus the pivot timestamp in seconds; it
    exceeds the nominal value across a data gap, which is why gap-spanning swings are counted.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from collections import defaultdict
from typing import Any, Dict, List, Sequence

from .data.ohlc import OHLCSeries
from .events import SWING_HIGH, SWING_LOW, DetectorEvent

NOT_COMPUTED = {
    "suppression_rate": "not applicable: no suppression rule is defined in the governing documents",
    "supersession_rate": "not applicable: no supersession rule is defined in the governing documents",
    "largest_bar_share": "unresolved: metric is not defined in the governing documents",
    "eqh_eql_frequency": "out of scope: belongs to the separate EQH/EQL detector",
}


def _percentile(sorted_values: List[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("empty")
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def characterise(series: OHLCSeries, events: Sequence[DetectorEvent]) -> List[Dict[str, Any]]:
    """One row per calendar year present in ``series`` for this symbol and timeframe."""
    for e in events:
        if (e.symbol, e.timeframe) != (series.symbol, series.timeframe):
            raise ValueError("Events do not belong to this series' symbol/timeframe")

    bars_by_year: Dict[int, int] = defaultdict(int)
    year_of_bar: Dict[int, int] = {}
    for c in series:
        bars_by_year[c.time.year] += 1
        year_of_bar[c.index] = c.time.year

    swings = sorted((e for e in events if e.type in (SWING_HIGH, SWING_LOW)),
                    key=lambda e: (e.payload["bar_index"], e.type != SWING_HIGH))
    legs_by_year: Dict[int, List[float]] = defaultdict(list)
    for prev, cur in zip(swings, swings[1:]):
        if prev.type != cur.type and cur.payload["bar_index"] in year_of_bar:
            legs_by_year[year_of_bar[cur.payload["bar_index"]]].append(
                abs(cur.payload["price"] - prev.payload["price"]))

    rows: List[Dict[str, Any]] = []
    for year in sorted(bars_by_year):
        ys = [e for e in swings if year_of_bar.get(e.payload["bar_index"]) == year]
        lags = [e.payload["confirmation_bar_index"] - e.payload["bar_index"] for e in ys]
        spans = [(e.confirmation_time - datetime.fromisoformat(e.payload["pivot_time"])).total_seconds() for e in ys]
        legs = sorted(legs_by_year[year])
        rows.append({
            "symbol": series.symbol, "timeframe": series.timeframe, "year": year,
            "bars": bars_by_year[year],
            "swing_highs": sum(e.type == SWING_HIGH for e in ys),
            "swing_lows": sum(e.type == SWING_LOW for e in ys),
            "swing_density": round(len(ys) / bars_by_year[year], 6),
            "confirmation_lag_bars_min": min(lags) if lags else None,
            "confirmation_lag_bars_max": max(lags) if lags else None,
            "swings_spanning_gap": sum(bool(e.payload.get("spans_gap")) for e in ys),
            "swings_gap_confirmed": sum(e.payload.get("gap_status") == "confirmed" for e in ys),
            "swings_gap_unverified": sum(e.payload.get("gap_status") == "unverified" for e in ys),
            "pivot_to_confirmation_s_median": (statistics.median(spans) if spans else None),
            "pivot_to_confirmation_s_max": (max(spans) if spans else None),
            "legs": len(legs),
            "leg_mean": round(statistics.fmean(legs), 6) if legs else None,
            "leg_p10": round(_percentile(legs, 0.10), 6) if legs else None,
            "leg_median": round(_percentile(legs, 0.50), 6) if legs else None,
            "leg_p90": round(_percentile(legs, 0.90), 6) if legs else None,
            **{k: None for k in NOT_COMPUTED},
        })
    return rows
