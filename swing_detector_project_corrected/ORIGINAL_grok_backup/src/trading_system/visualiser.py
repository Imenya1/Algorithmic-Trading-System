"""Chart visualiser for confirmed swing highs and lows.

Keeps charting completely separate from the detector engine.
Never displays unconfirmed candidates as confirmed swings.
Does not imply a swing was known before its confirmation candle closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.lines import Line2D
import numpy as np

from .data.ohlc import OHLCSeries
from .events import DetectorEvent


# Clean professional style
plt.rcParams.update({
    "figure.facecolor": "#0d1117",
    "axes.facecolor": "#0d1117",
    "axes.edgecolor": "#30363d",
    "axes.labelcolor": "#e6edf3",
    "xtick.color": "#8b949e",
    "ytick.color": "#8b949e",
    "text.color": "#e6edf3",
    "grid.color": "#21262d",
    "grid.linewidth": 0.6,
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "legend.fontsize": 9,
    "figure.dpi": 150,
    "savefig.dpi": 220,
    "savefig.facecolor": "#0d1117",
    "savefig.edgecolor": "none",
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.25,
})


def plot_swings(
    series: OHLCSeries,
    events: Sequence[DetectorEvent],
    title: Optional[str] = None,
    save_path: Optional[Path] = None,
    show: bool = False,
    max_bars: Optional[int] = 180,
    dpi: int = 220,
) -> Path:
    """
    High-quality candlestick chart with confirmed swing markers only.

    - Clean dark theme
    - Sharp rendering (high DPI)
    - Swing markers offset above/below the candle so they stay readable
    - No unconfirmed candidates are shown
    """
    # Limit bars for clarity (most recent window)
    if max_bars is not None and len(series) > max_bars:
        series = series.slice(len(series) - max_bars)

    n = len(series)
    if n == 0:
        raise ValueError("Empty series — nothing to plot")

    opens = series.opens
    highs = series.highs
    lows = series.lows
    closes = series.closes
    times = series.times

    # Figure size scales with number of bars for better readability
    width_inches = max(12, min(22, n * 0.085))
    fig, ax = plt.subplots(figsize=(width_inches, 7.5))

    # ----- Candlesticks (vector-friendly, clean edges) -----
    candle_width = 0.65

    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        is_bull = c >= o
        body_color = "#3fb950" if is_bull else "#f85149"   # GitHub-style green/red
        wick_color = "#8b949e"

        # Wick
        ax.plot([i, i], [l, h], color=wick_color, linewidth=1.1, solid_capstyle="round", zorder=2)

        # Body
        body_bottom = min(o, c)
        body_height = abs(c - o)
        if body_height < (h - l) * 0.002:          # doji / very small body
            body_height = (h - l) * 0.002 or 0.0001

        rect = Rectangle(
            (i - candle_width / 2, body_bottom),
            candle_width,
            body_height,
            facecolor=body_color,
            edgecolor=body_color,
            linewidth=0.6,
            antialiased=True,
            zorder=3,
        )
        ax.add_patch(rect)

    # ----- Confirmed swing markers (offset so they never sit on the candle) -----
    index_to_x = {c.index: i for i, c in enumerate(series)}

    # Price range for offset calculation
    price_range = float(np.nanmax(highs) - np.nanmin(lows)) or 1.0
    offset = price_range * 0.012          # small visual gap above/below

    swing_highs_x, swing_highs_y = [], []
    swing_lows_x, swing_lows_y = [], []

    for ev in events:
        bi = ev.payload.get("bar_index")
        price = ev.payload.get("price")
        if bi is None or price is None:
            continue
        if bi not in index_to_x:
            continue
        x = index_to_x[bi]

        if ev.type == "SWING_HIGH":
            swing_highs_x.append(x)
            swing_highs_y.append(price + offset)
        elif ev.type == "SWING_LOW":
            swing_lows_x.append(x)
            swing_lows_y.append(price - offset)

    # Markers – larger, clean, with slight edge for definition
    if swing_highs_x:
        ax.scatter(
            swing_highs_x,
            swing_highs_y,
            marker="v",
            s=110,
            c="#ff7b72",
            edgecolors="#0d1117",
            linewidths=0.8,
            zorder=6,
            label=f"SWING_HIGH  ({len(swing_highs_x)})",
        )
    if swing_lows_x:
        ax.scatter(
            swing_lows_x,
            swing_lows_y,
            marker="^",
            s=110,
            c="#58a6ff",
            edgecolors="#0d1117",
            linewidths=0.8,
            zorder=6,
            label=f"SWING_LOW  ({len(swing_lows_x)})",
        )

    # ----- Axes & styling -----
    ax.set_xlim(-1, n)
    # Add a little padding top/bottom
    y_min = float(np.nanmin(lows)) - price_range * 0.04
    y_max = float(np.nanmax(highs)) + price_range * 0.04
    ax.set_ylim(y_min, y_max)

    ax.set_xlabel("Bar (most recent window)")
    ax.set_ylabel("Price")
    ax.set_title(
        title
        or f"{series.symbol}  ·  {series.timeframe}  ·  Confirmed Swings only",
        pad=12,
        fontweight="medium",
    )

    # Clean legend
    legend = ax.legend(
        loc="upper left",
        frameon=True,
        fancybox=False,
        edgecolor="#30363d",
        facecolor="#161b22",
        framealpha=0.92,
    )
    for text in legend.get_texts():
        text.set_color("#e6edf3")

    ax.grid(True, which="major", linestyle="-", alpha=0.5)
    ax.set_axisbelow(True)

    # X-axis labels – show a few timestamps cleanly
    n_ticks = min(9, n)
    tick_pos = np.linspace(0, n - 1, n_ticks, dtype=int)
    ax.set_xticks(tick_pos)
    labels = []
    for i in tick_pos:
        t = times[i]
        if hasattr(t, "strftime"):
            labels.append(t.strftime("%m-%d %H:%M"))
        else:
            labels.append(str(t)[:16])
    ax.set_xticklabels(labels, rotation=25, ha="right")

    # Remove top/right spines for a cleaner look
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#30363d")
    ax.spines["bottom"].set_color("#30363d")

    fig.tight_layout()

    if save_path is None:
        save_path = Path("reports") / f"swing_chart_{series.symbol}_{series.timeframe}.png"
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # High-quality save
    fig.savefig(
        save_path,
        dpi=dpi,
        facecolor=fig.get_facecolor(),
        edgecolor="none",
        bbox_inches="tight",
        pad_inches=0.3,
    )
    if show:
        plt.show()
    plt.close(fig)
    return save_path
