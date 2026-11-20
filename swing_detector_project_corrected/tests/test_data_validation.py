"""OHLC loading and validation, including fault injection (malformed, duplicate, missing candles)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from helpers import T0, walk
from trading_system.data.ohlc import Candle, OHLCSeries
from trading_system.data.validation import DataValidationError, validate_candles, validate_series
from trading_system.detectors.base import DetectorInputError, GapError
from trading_system.detectors.swing import SwingDetector
from trading_system.replay import replay_series


def _c(i, o=10, h=12, l=8, c=11, **kw):
    return Candle(T0 + timedelta(minutes=5 * i), o, h, l, c, **kw)


def _codes(report):
    return {i.code for i in report.issues}


# ------------------------------------------------------------------ structural faults
def test_invalid_ohlc_relationships_reported_with_index():
    r = validate_candles([_c(0), _c(1, h=9), _c(2, l=11.5), _c(3, c=13), _c(4, h=5, l=9)], "X", "M5")
    assert not r.valid
    assert {"LOW_GT_HIGH", "OPEN_OUT_OF_RANGE", "CLOSE_OUT_OF_RANGE"} <= _codes(r)
    assert {i.index for i in r.errors()} == {1, 2, 3, 4}  # the valid candle 0 is never flagged


def test_missing_nan_inf_and_non_numeric_prices_reported():
    cs = [_c(0), _c(1, h=float("nan")), _c(2, l=float("inf")), _c(3, o=None), _c(4, c="abc")]
    r = validate_candles(cs, "X", "M5")
    assert {"INVALID_HIGH", "INVALID_LOW", "MISSING_OPEN", "INVALID_CLOSE"} <= _codes(r)


def test_duplicate_and_out_of_order_timestamps_reported():
    cs = [_c(0), _c(1), _c(1, h=13), _c(0)]
    r = validate_candles(cs, "X", "M5")
    assert {"DUPLICATE_TIME", "OUT_OF_ORDER"} <= _codes(r)


def test_missing_candle_gap_is_reported_with_count_by_validate_series():
    cs = [_c(i) for i in range(10) if i not in (4, 5)]
    r = validate_series(OHLCSeries(cs, "X", "M5"))
    gap = [i for i in r.issues if i.code == "GAP"]
    assert len(gap) == 1 and "2 missing" in gap[0].message
    assert gap[0].severity == "warning" and r.valid          # default: the detector bridges gaps
    strict = validate_series(OHLCSeries(cs, "X", "M5"), allow_gaps=False)
    assert strict.errors() and not strict.valid               # strict mode reports them as errors


def test_empty_dataset_is_an_error_and_insufficient_history_is_reported():
    assert not validate_candles([], "X", "M5").valid
    r = validate_series(OHLCSeries([_c(i) for i in range(4)], "X", "M5"), min_bars=5)
    assert "INSUFFICIENT_HISTORY" in _codes(r)


def test_mixed_timezones_and_invalid_time_reported():
    from datetime import timezone
    cs = [_c(0), Candle(T0.replace(tzinfo=timezone.utc) + timedelta(minutes=5), 10, 12, 8, 11)]
    assert "MIXED_TIMEZONE" in _codes(validate_candles(cs, "X", "M5"))
    assert "INVALID_TIME" in _codes(validate_candles([Candle(pd.NaT, 1, 2, 0, 1)], "X", "M5"))


def test_series_constructor_rejects_invalid_data_listing_every_problem():
    with pytest.raises(DataValidationError) as exc:
        OHLCSeries([_c(0, h=9), _c(1, c=99)], "X", "M5")
    assert len(exc.value.issues) >= 2 and "candle 0" in str(exc.value)


# ------------------------------------------------------------------ loaders
def test_missing_columns_named_in_error():
    with pytest.raises(ValueError, match=r"Missing required column.*high"):
        OHLCSeries.from_dataframe(pd.DataFrame({"time": [T0], "open": [1], "low": [0], "close": [1]}), "X", "M5")


def test_unparseable_time_and_price_reported_with_row_and_raw_value():
    df = pd.DataFrame({"time": [T0, "not-a-date", T0 + timedelta(minutes=10)],
                       "open": [1, 1, "abc"], "high": [2, 2, 2], "low": [0, 0, 0], "close": [1, 1, 1]})
    with pytest.raises(DataValidationError) as exc:
        OHLCSeries.from_dataframe(df, "X", "M5")
    msg = str(exc.value)
    assert "INVALID_TIME" in msg and "'not-a-date'" in msg and "INVALID_OPEN" in msg and "'abc'" in msg


def test_nat_and_empty_inputs_rejected():
    df = pd.DataFrame({"time": [T0, pd.NaT], "open": [1, 1], "high": [2, 2], "low": [0, 0], "close": [1, 1]})
    with pytest.raises(DataValidationError):
        OHLCSeries.from_dataframe(df, "X", "M5")
    with pytest.raises(DataValidationError):
        OHLCSeries.from_dataframe(df.iloc[0:0], "X", "M5")


def test_loader_does_not_modify_source_dataframe_or_csv(tmp_path):
    series = walk(30, 1)
    df = series.to_dataframe()
    before = df.copy(deep=True)
    OHLCSeries.from_dataframe(df, "T", "M5")
    pd.testing.assert_frame_equal(df, before)
    path = tmp_path / "x.csv"
    df.to_csv(path, index=False)
    raw = path.read_bytes()
    loaded = OHLCSeries.from_csv(path, "T", "M5")
    assert path.read_bytes() == raw and len(loaded) == 30
    np.testing.assert_allclose(loaded.highs, series.highs)


def test_numeric_timestamps_need_explicit_epoch_unit(tmp_path):
    df = pd.DataFrame({"time": [1700000000, 1700000300], "open": [1, 1], "high": [2, 2], "low": [0, 0], "close": [1, 1]})
    with pytest.raises(DataValidationError):
        OHLCSeries.from_dataframe(df, "X", "M5")
    s = OHLCSeries.from_dataframe(df, "X", "M5", epoch_unit="s")
    assert s[0].time == datetime(2023, 11, 14, 22, 13, 20)


# ------------------------------------------------------------------ index preservation
def test_slice_preserves_absolute_bar_indices_and_detector_output():
    series = walk(400, 2026, "V75", "M5")
    full = replay_series(series, SwingDetector("V75", "M5", {"n": 2}))
    sub = series.slice(100, 300)
    assert (sub[0].index, sub[len(sub) - 1].index) == (100, 299)
    got = replay_series(sub, SwingDetector("V75", "M5", {"n": 2}))
    want = [e for e in full if 100 <= e.payload["bar_index"] and e.payload["confirmation_bar_index"] < 300]
    # first n bars of a slice have no left context, so compare the interior
    interior = [e for e in got if e.payload["bar_index"] >= 102]
    assert [e.to_dict() for e in interior] == [e.to_dict() for e in want if e.payload["bar_index"] >= 102]


# ------------------------------------------------------------------ fault injection end to end
def test_detector_fault_injection_malformed_duplicate_and_late_candles():
    cs = list(walk(40, 5))
    det = SwingDetector("T", "M5", {"n": 2})
    for c in cs[:20]:
        det.step(c)
    with pytest.raises(DetectorInputError):      # malformed
        det.step(Candle(cs[20].time, 1, float("nan"), 0, 1))
    assert det.step(cs[19])[1] == []             # duplicate: idempotent
    det.step(cs[21])                             # a missing candle is bridged, not fatal
    with pytest.raises(DetectorInputError, match="late"):   # the late candle must be backfilled upstream
        det.step(cs[20])
    assert det.state.last_processed_index == 21 and det.state.gaps_unverified == 1


def test_strict_gap_policy_still_available_and_leaves_state_untouched():
    cs = list(walk(40, 5))
    det = SwingDetector("T", "M5", {"n": 2, "gap_policy": "error"})
    for c in cs[:20]:
        det.step(c)
    before = det.snapshot()
    with pytest.raises(GapError):
        det.step(cs[21])
    assert det.snapshot() == before
