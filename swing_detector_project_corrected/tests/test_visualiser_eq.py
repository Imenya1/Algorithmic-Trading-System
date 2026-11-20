"""Chart verification for EQH/EQL levels: drawn only from detector events, never before confirmation."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from tests.helpers import tie_rich_series
from trading_system.config import load_config
from trading_system.events import DetectorEvent, EQ_CLUSTER
from trading_system.replay import create_eqh_eql_detector, create_swing_detector, replay_swing_eqh_eql
from trading_system.visualiser import compute_eq_levels, plot_swings, select_window

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def data():
    cfg = load_config()
    s = tie_rich_series(900, 5, "V75", "M5")
    swings, clusters = replay_swing_eqh_eql(s, create_swing_detector(cfg, "V75", "M5"), create_eqh_eql_detector(cfg, "V75", "M5"))
    assert clusters
    return s, swings, clusters


def test_levels_equal_detector_clusters_completed_in_view_and_start_at_confirmation(data):
    s, swings, clusters = data
    view = select_window(s, (300, 520))
    levels, hidden = compute_eq_levels(view, clusters)
    expected = [c for c in clusters
                if view.has_index(c.payload["constituent_bar_indices"][-1])
                and c.payload["confirmation_bar_index"] <= 520]
    assert len(levels) == len(expected) > 0
    for lv, ev in zip(levels, expected):
        assert lv.level == ev.payload["level"] and lv.side == ev.payload["side"]
        assert lv.start_x == view.position_of(ev.payload["confirmation_bar_index"])      # not before confirmation
        assert lv.start_x > view.position_of(ev.payload["constituent_bar_indices"][-1])  # strictly after the swing's pivot


def test_unconfirmed_clusters_are_hidden_not_drawn(data):
    s, _, clusters = data
    pending = [c for c in clusters if c.payload["confirmation_bar_index"] > 400 >= c.payload["constituent_bar_indices"][-1]]
    assert pending, "fixture must contain a cluster whose swing pivot is in view but confirms after the window"
    view = select_window(s, (300, 400))
    levels, hidden = compute_eq_levels(view, clusters)
    assert hidden == len(pending)
    assert all(lv.confirmation_bar_index <= 400 for lv in levels)


def test_as_of_hides_clusters_confirmed_later(data):
    s, _, clusters = data
    view = select_window(s, (300, 520))
    t = view[100].time
    levels, hidden = compute_eq_levels(view, clusters, as_of=t)
    assert hidden > 0 and all(clusters_ev.confirmation_time <= t for clusters_ev in
                              [c for c in clusters if c.payload["confirmation_bar_index"] in {l.confirmation_bar_index for l in levels}])


def test_no_fabricated_levels_when_there_are_no_clusters(data):
    s, swings, _ = data
    levels, hidden = compute_eq_levels(select_window(s, (300, 520)), swings)  # swings only
    assert levels == [] and hidden == 0


def test_eqh_and_eql_are_distinguishable_and_validated(data):
    s, _, clusters = data
    levels, _ = compute_eq_levels(select_window(s, (0, 899)), clusters)
    assert {l.side for l in levels} == {"high", "low"}
    view = select_window(s, (300, 520))
    c = next(c for c in clusters if view.has_index(c.payload["constituent_bar_indices"][-1]))
    foreign = DetectorEvent(EQ_CLUSTER, "V100", "M3", c.confirmation_time, c.payload)
    with pytest.raises(ValueError, match="cannot be drawn"):
        compute_eq_levels(view, [foreign])
    bad_price = dict(c.payload, constituent_prices=[p + 1.0 for p in c.payload["constituent_prices"]])
    with pytest.raises(ValueError, match="does not match"):
        compute_eq_levels(view, [DetectorEvent(EQ_CLUSTER, c.symbol, c.timeframe, c.confirmation_time, bad_price)])
    no_conf = {k: v for k, v in c.payload.items() if k != "confirmation_bar_index"}
    with pytest.raises(ValueError, match="confirmation_bar_index"):
        compute_eq_levels(view, [DetectorEvent(EQ_CLUSTER, c.symbol, c.timeframe, c.confirmation_time, no_conf)])
    bad_side = dict(c.payload, side="up")
    with pytest.raises(ValueError, match="side"):
        compute_eq_levels(view, [DetectorEvent(EQ_CLUSTER, c.symbol, c.timeframe, c.confirmation_time, bad_side)])


def test_chart_renders_with_eq_levels_and_existing_swing_chart_unchanged(data, tmp_path):
    s, swings, clusters = data
    with_eq = plot_swings(s, swings + clusters, save_path=tmp_path / "eq.png", window=(300, 520))
    swing_only = plot_swings(s, swings, save_path=tmp_path / "sw.png", window=(300, 520))
    assert with_eq.stat().st_size > 5000 and swing_only.stat().st_size > 5000
    assert with_eq.read_bytes() != swing_only.read_bytes()   # the levels are actually drawn


def test_cli_replay_eq_and_chart_eq(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import swing_cli
    s = tie_rich_series(400, 8, "V75", "M5")
    csv = tmp_path / "x.csv"
    df = s.to_dataframe()[["time", "open", "high", "low", "close"]]
    df.to_csv(csv, index=False)
    out = tmp_path / "ev.json"
    assert swing_cli.main(["replay-eq", "--csv", str(csv), "--symbol", "V75", "--timeframe", "M5", "--out", str(out)]) == 0
    types = {e["type"] for e in json.loads(out.read_text())}
    assert {"SWING_HIGH", "SWING_LOW", "EQ_CLUSTER"} <= types
    png = tmp_path / "c.png"
    assert swing_cli.main(["chart-eq", "--csv", str(csv), "--symbol", "V75", "--timeframe", "M5", "--out", str(png),
                           "--window", "100", "250"]) == 0 and png.exists()
