"""Data validation, fault injection, and configuration tests."""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from trading_system.data.ohlc import Candle, OHLCSeries
from trading_system.data.validation import validate_candles
from trading_system.config import load_config, get_swing_params, list_symbols, ConfigError
from trading_system.detectors.swing import SwingDetector
from trading_system.utils.synthetic import generate_random_walk
from trading_system.replay import replay_series


def test_config_loads_and_params():
    cfg = load_config()
    symbols = list_symbols(cfg)
    assert set(symbols) == {"V75", "V100", "V50"}

    p = get_swing_params(cfg, "V75", "H1")
    assert p["n"] == 3
    p = get_swing_params(cfg, "V75", "M5")
    assert p["n"] == 2
    p = get_swing_params(cfg, "V100", "M15")
    assert p["n"] == 3
    p = get_swing_params(cfg, "V100", "M3")
    assert p["n"] == 2
    p = get_swing_params(cfg, "V50", "H1")
    assert p["n"] == 3
    p = get_swing_params(cfg, "V50", "M15")
    assert p["n"] == 2


def test_config_rejects_unknown_symbol():
    cfg = load_config()
    with pytest.raises(ConfigError):
        get_swing_params(cfg, "BOOM", "M5")


def test_invalid_ohlc_raises():
    bad = [
        Candle(time=datetime(2024, 1, 1), open=10, high=9, low=8, close=9),  # high < open
    ]
    with pytest.raises(ValueError):
        OHLCSeries(bad, symbol="X", timeframe="M5", validate=True)


def test_duplicate_time_reported():
    t = datetime(2024, 1, 1)
    candles = [
        Candle(time=t, open=1, high=2, low=0.5, close=1.5),
        Candle(time=t, open=1.5, high=2.5, low=1, close=2),
    ]
    report = validate_candles(candles, symbol="X", timeframe="M5")
    assert not report.valid
    assert any(i.code == "DUPLICATE_TIME" for i in report.issues)


def test_out_of_order_reported():
    candles = [
        Candle(time=datetime(2024, 1, 2), open=1, high=2, low=0.5, close=1.5),
        Candle(time=datetime(2024, 1, 1), open=1, high=2, low=0.5, close=1.5),
    ]
    report = validate_candles(candles, symbol="X", timeframe="M5")
    assert not report.valid
    assert any(i.code == "OUT_OF_ORDER" for i in report.issues)


def test_detector_ignores_duplicate_candle():
    series = generate_random_walk(n_bars=50, seed=1)
    det = SwingDetector(symbol="T", timeframe="M5", params={"n": 2})
    events1 = replay_series(series, det)

    # Re-process the last candle (should be ignored)
    last = series[-1]
    state, events2 = det.step(last)
    assert events2 == []
    assert len(det.get_confirmed_events()) == len(events1)


def test_scalability_new_symbol_via_config_only():
    """
    Architecture test: a new test symbol can be processed through the
    same detector class with only configuration / constructor arguments.
    This does NOT authorize adding the symbol to the v3.4 research scope.
    """
    # Simulate "config-only" by constructing a detector for a new symbol
    series = generate_random_walk(n_bars=60, seed=99, symbol="TEST_NEW", timeframe="M5")
    det = SwingDetector(symbol="TEST_NEW", timeframe="M5", params={"n": 2})
    events = replay_series(series, det)
    assert all(e.symbol == "TEST_NEW" for e in events)
    # No code change to SwingDetector or replay engine was required
