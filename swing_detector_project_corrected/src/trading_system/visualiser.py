"""Chart visualiser for confirmed swing and EQH/EQL events.

The chart is a pure rendering of detector output: it never recalculates swings. Marker
placement is separated from drawing (``compute_markers``) so it can be tested without
inspecting pixels.

Rules enforced here:
  * Only events supplied by the detector are drawn, on the pivot candle they refer to.
  * A swing is shown only if it was confirmed by the end of the displayed window (or by
    ``as_of`` when given); swings confirmed later are hidden and counted, so the chart
    never shows information that was not yet known at the window's last close.
  * Each swing gets a confirmation tick on the candle that confirmed it, so confirmation
    timing is visible and cannot be mistaken for pivot time.
  * Events for another symbol/timeframe, or whose price does not match the candle they
    point at, raise an error instead of being drawn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")  # headless rendering; set before pyplot is imported
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .data.ohlc import OHLCSeries  # noqa: E402
from .events import SWING_HIGH, SWING_LOW, EQ_CLUSTER, DetectorEvent  # noqa: E402

_STYLE = {
    "figure.facecolor": "#0d1117", "axes.facecolor": "#0d1117", "axes.edgecolor": "#30363d",
    "axes.labelcolor": "#e6edf3", "xtick.color": "#8b949e", "ytick.color": "#8b949e",
    "text.color": "#e6edf3", "grid.color": "#21262d", "grid.linewidth": 0.6,
    "font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 13,
    "axes.labelsize": 11, "legend.fontsize": 9,
}


@dataclass(frozen=True)
class SwingMarker:
    event_type: str
    bar_index: int      # absolute pivot bar index
    x: int              # pivot position inside the displayed window
    price: float
    confirm_x: int      # position of the confirming candle inside the window
    spans_gap: bool = False


def select_window(series: OHLCSeries, window: Optional[Tuple[int, int]] = None,
                  max_bars: Optional[int] = 180) -> OHLCSeries:
    """Sub-series to display. ``window`` is (first_bar_index, last_bar_index), absolute and
    inclusive; otherwise the last ``max_bars`` candles. Absolute bar indices are preserved."""
    if window is not None:
        first, last = window
        if not (series.has_index(first) and series.has_index(last)) or first > last:
            raise ValueError(f"Window {window} is not inside the series "
                             f"(bar indices {int(series.indices[0])}..{int(series.indices[-1])})")
        return series.slice(series.position_of(first), series.position_of(last) + 1)
    if max_bars is not None and len(series) > max_bars:
        return series.slice(len(series) - max_bars)
    return series


def compute_markers(
    view: OHLCSeries,
    events: Sequence[DetectorEvent],
    as_of: Optional[datetime] = None,
) -> Tuple[List[SwingMarker], int]:
    """Markers for the swings visible in ``view``, and the number hidden as not yet confirmed."""
    last_bar = int(view.indices[-1])
    markers: List[SwingMarker] = []
    hidden = 0
    for ev in events:
        if ev.type not in (SWING_HIGH, SWING_LOW):
            continue
        if (ev.symbol, ev.timeframe) != (view.symbol, view.timeframe):
            raise ValueError(f"Event for {ev.symbol}/{ev.timeframe} cannot be drawn on "
                             f"{view.symbol}/{view.timeframe}")
        bar = ev.payload["bar_index"]
        conf_bar = ev.payload["confirmation_bar_index"]
        if not view.has_index(bar):
            continue
        if conf_bar > last_bar or (as_of is not None and ev.confirmation_time > as_of):
            hidden += 1
            continue
        pos = view.position_of(bar)
        expected = view.highs[pos] if ev.type == SWING_HIGH else view.lows[pos]
        if abs(expected - ev.payload["price"]) > 1e-9:
            raise ValueError(
                f"{ev.type} at bar {bar}: event price {ev.payload['price']} does not match the "
                f"series ({expected}); the chart data is not the data the detector saw")
        markers.append(SwingMarker(ev.type, bar, pos, float(ev.payload["price"]),
                                   view.position_of(conf_bar), bool(ev.payload.get("spans_gap", False))))
    return markers, hidden


@dataclass(frozen=True)
class EqLevel:
    side: str            # "high" (EQH) or "low" (EQL)
    level: float
    start_x: int         # first bar position (inside the view) at which the level is known
    touch_count: int
    confirmation_bar_index: int


def compute_eq_levels(
    view: OHLCSeries,
    events: Sequence[DetectorEvent],
    as_of: Optional[datetime] = None,
) -> Tuple[List[EqLevel], int]:
    """EQ_CLUSTER levels drawable in ``view`` and the number hidden as not yet confirmed.

    Only clusters whose newest constituent swing lies inside ``view`` are drawn. A level starts at its confirmation candle, never earlier, so the chart cannot show a level
    before the detector knew it. Constituent prices are checked against the candles they point
    at; an event for another symbol/timeframe, or one that disagrees with the data, raises."""
    last_bar = int(view.indices[-1])
    first_bar = int(view.indices[0])
    levels: List[EqLevel] = []
    hidden = 0
    for ev in events:
        if ev.type != EQ_CLUSTER:
            continue
        if (ev.symbol, ev.timeframe) != (view.symbol, view.timeframe):
            raise ValueError(f"Event for {ev.symbol}/{ev.timeframe} cannot be drawn on "
                             f"{view.symbol}/{view.timeframe}")
        side = ev.payload.get("side")
        if side not in ("high", "low"):
            raise ValueError(f"EQ_CLUSTER with invalid side {side!r}")
        conf_bar = ev.payload.get("confirmation_bar_index")
        if conf_bar is None:
            raise ValueError("EQ_CLUSTER has no confirmation_bar_index; cannot place it without look-ahead")
        newest = ev.payload["constituent_bar_indices"][-1]
        if not view.has_index(newest):
            continue  # formed outside this window; only clusters completed inside it are drawn
        if conf_bar > last_bar or (as_of is not None and ev.confirmation_time > as_of):
            hidden += 1
            continue
        for bar, price in zip(ev.payload["constituent_bar_indices"], ev.payload["constituent_prices"]):
            if view.has_index(bar):
                pos = view.position_of(bar)
                expected = view.highs[pos] if side == "high" else view.lows[pos]
                if abs(expected - price) > 1e-9:
                    raise ValueError(f"EQ_CLUSTER constituent at bar {bar}: price {price} does not match "
                                     f"the series ({expected}); the chart data is not the data the detector saw")
        start_x = view.position_of(conf_bar) if conf_bar >= first_bar else 0
        levels.append(EqLevel(side, float(ev.payload["level"]), start_x,
                              int(ev.payload["touch_count"]), int(conf_bar)))
    return levels, hidden


def plot_swings(
    series: OHLCSeries,
    events: Sequence[DetectorEvent],
    title: Optional[str] = None,
    save_path: Optional[Path] = None,
    show: bool = False,
    max_bars: Optional[int] = 180,
    window: Optional[Tuple[int, int]] = None,
    as_of: Optional[datetime] = None,
    dpi: int = 200,
) -> Path:
    """Candlestick chart with detector-confirmed swings. Returns the saved image path."""
    if len(series) == 0:
        raise ValueError("Empty series - nothing to plot")
    view = select_window(series, window, max_bars)
    markers, hidden = compute_markers(view, events, as_of)

    n = len(view)
    highs, lows = view.highs, view.lows
    price_range = float(np.nanmax(highs) - np.nanmin(lows)) or 1.0
    offset = price_range * 0.012

    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(max(12, min(22, n * 0.085)), 7.5))
        for i in range(n):
            o, h, l, c = view.opens[i], highs[i], lows[i], view.closes[i]
            color = "#3fb950" if c >= o else "#f85149"
            ax.plot([i, i], [l, h], color="#8b949e", linewidth=1.1, zorder=2)
            body = max(abs(c - o), (h - l) * 0.002 or 1e-4)
            ax.add_patch(Rectangle((i - 0.325, min(o, c)), 0.65, body, facecolor=color,
                                   edgecolor=color, linewidth=0.6, zorder=3))

        for kind, marker, color, sign, label in (
            (SWING_HIGH, "v", "#ff7b72", 1, "SWING_HIGH (pivot candle)"),
            (SWING_LOW, "^", "#58a6ff", -1, "SWING_LOW (pivot candle)"),
        ):
            ms = [m for m in markers if m.event_type == kind]
            if not ms:
                continue
            ys = [m.price + sign * offset for m in ms]
            ax.scatter([m.x for m in ms], ys, marker=marker, s=110, c=color, edgecolors="#0d1117",
                       linewidths=0.8, zorder=6, label=f"{label}  ({len(ms)})")
            for m, y in zip(ms, ys):  # dotted link to the confirming candle, with a tick there
                ax.plot([m.x, m.confirm_x], [y, y], linestyle=":", linewidth=0.9, color=color, zorder=4)
                ax.scatter([m.confirm_x], [y], marker="|", s=60, c=color, zorder=5)
        gap_ms = [m for m in markers if m.spans_gap]
        if gap_ms:
            ys = [m.price + (1 if m.event_type == SWING_HIGH else -1) * offset * 2.6 for m in gap_ms]
            ax.scatter([m.x for m in gap_ms], ys, marker="D", s=28, c="#d29922", zorder=6,
                       label=f"swing window spans a data gap  ({len(gap_ms)})")

        # EQ_CLUSTER levels (Build Order Section 9). Each level is drawn from its confirmation
        # candle to the right edge only: it was not known before that candle closed.
        eq_levels, eq_hidden = compute_eq_levels(view, events, as_of)
        for side, color, label in (("high", "#ffa657", "EQH level"), ("low", "#79c0ff", "EQL level")):
            lv = [e for e in eq_levels if e.side == side]
            if not lv:
                continue
            ax.hlines([e.level for e in lv], [e.start_x for e in lv], n - 1, colors=color,
                      linestyles="--", linewidth=1.0, alpha=0.85, zorder=3)
            ax.plot([], [], linestyle="--", color=color, label=f"{label}  ({len(lv)}, from confirmation)")

        ax.plot([], [], linestyle=":", color="#8b949e", label="| = candle that confirmed the swing")

        ax.set_xlim(-1, n)
        ax.set_ylim(float(np.nanmin(lows)) - price_range * 0.04, float(np.nanmax(highs)) + price_range * 0.04)
        ax.set_xlabel("Bar")
        ax.set_ylabel("Price")
        hidden_total = hidden + eq_hidden
        suffix = f"  -  {hidden_total} swing/EQ event(s) not yet confirmed, hidden" if hidden_total else ""
        ax.set_title((title or f"{view.symbol}  .  {view.timeframe}  .  confirmed swings only") + suffix,
                     pad=12)
        legend = ax.legend(loc="upper left", frameon=True, fancybox=False, edgecolor="#30363d",
                           facecolor="#161b22", framealpha=0.92)
        for text in legend.get_texts():
            text.set_color("#e6edf3")
        ax.grid(True, alpha=0.5)
        ax.set_axisbelow(True)

        ticks = np.linspace(0, n - 1, min(9, n), dtype=int)
        ax.set_xticks(ticks)
        ax.set_xticklabels([f"{int(view.indices[i])}\n{view.times[i]:%m-%d %H:%M}" for i in ticks],
                           rotation=25, ha="right")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.tight_layout()

        save_path = Path(save_path) if save_path else Path("reports") / f"swing_chart_{view.symbol}_{view.timeframe}.png"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=dpi, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.3)
        if show:
            plt.show()
        plt.close(fig)
    return save_path
