"""Configuration: Section 8 values, validation, behaviour change, config-only onboarding,
and a static check that detector source hard-codes no instrument parameters."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest
import yaml

from helpers import walk
from trading_system.config import (ConfigError, config_hash, get_swing_params, list_symbols,
                                   list_timeframes, load_config, timeframe_seconds, validate_config)
from trading_system.replay import DetectorBank, create_swing_detector, replay_series

SRC = Path(__file__).resolve().parents[1] / "src" / "trading_system"

# Build Order v3.4 Section 8 (the specification, restated independently of the YAML)
SECTION_8 = {
    "V75": {"H1": 3, "M5": 2},
    "V100": {"M15": 3, "M3": 2},
    "V50": {"H1": 3, "M15": 2},
}


def test_shipped_config_matches_section_8_exactly_and_has_no_extra_instruments():
    cfg = load_config()
    assert set(list_symbols(cfg)) == set(SECTION_8)
    for symbol, tfs in SECTION_8.items():
        assert set(list_timeframes(cfg, symbol)) == set(tfs)
        for tf, n in tfs.items():
            assert get_swing_params(cfg, symbol, tf)["n"] == n


def test_detector_for_each_symbol_timeframe_uses_the_configured_n():
    cfg = load_config()
    for symbol, tfs in SECTION_8.items():
        for tf, n in tfs.items():
            det = create_swing_detector(cfg, symbol, tf)
            assert det.n == n
            events = replay_series(walk(300, 1, symbol, tf), det)
            assert events and {e.payload["confirmation_bar_index"] - e.payload["bar_index"] for e in events} == {n}


def test_changing_config_changes_detector_behaviour():
    cfg = load_config()
    cfg2 = copy.deepcopy(cfg)
    cfg2["instruments"]["V75"]["ltf_swing_n"] = 4
    series = walk(400, 3, "V75", "M5")
    a = replay_series(series, create_swing_detector(cfg, "V75", "M5"))
    b = replay_series(series, create_swing_detector(cfg2, "V75", "M5"))
    assert len(a) != len(b)
    assert {e.payload["confirmation_bar_index"] - e.payload["bar_index"] for e in b} == {4}
    assert config_hash(cfg) != config_hash(cfg2)


def test_yaml_value_change_flows_through_load_config(tmp_path):
    raw = yaml.safe_load((SRC.parents[1] / "config" / "instruments.yaml").read_text())
    raw["instruments"]["V100"]["htf_swing_n"] = 5
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw))
    assert get_swing_params(load_config(path), "V100", "M15")["n"] == 5


@pytest.mark.parametrize("mutate,match", [
    (lambda c: c["instruments"]["V75"].update(ltf_swing_n=0), "must be >= 1"),
    (lambda c: c["instruments"]["V75"].update(ltf_swing_n=-2), "must be >= 1"),
    (lambda c: c["instruments"]["V75"].update(ltf_swing_n="2"), "integer"),
    (lambda c: c["instruments"]["V75"].update(ltf_swing_n=2.5), "integer"),
    (lambda c: c["instruments"]["V75"].update(ltf_swing_n=True), "integer"),
    (lambda c: c["instruments"]["V75"].pop("ltf"), "missing keys"),
    (lambda c: c["instruments"]["V75"].update(extra=1), "unknown keys"),
    (lambda c: c["instruments"]["V75"].update(htf="M5"), "longer than"),
    (lambda c: c["instruments"]["V75"].update(htf="M1", ltf="H1"), "longer than"),
    (lambda c: c["instruments"]["V75"].update(ltf="5min"), "Invalid timeframe"),
    (lambda c: c["instruments"].clear(), "non-empty"),
    (lambda c: c.update(data={"candle_time": "midnight"}), "candle_time"),
    (lambda c: c.update(detectors={"swing": {"gap_policy": "ignore"}}), "gap_policy"),
])
def test_invalid_configurations_are_rejected_with_clear_errors(mutate, match):
    cfg = copy.deepcopy(load_config())
    mutate(cfg)
    with pytest.raises(ConfigError, match=match):
        validate_config(cfg)


def test_unknown_symbol_and_timeframe_rejected():
    cfg = load_config()
    with pytest.raises(ConfigError, match="not in configuration"):
        get_swing_params(cfg, "BOOM500", "M5")
    with pytest.raises(ConfigError, match="neither HTF"):
        get_swing_params(cfg, "V75", "M15")


def test_missing_and_malformed_config_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("instruments: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)


def test_timeframe_seconds():
    assert [timeframe_seconds(t) for t in ("M1", "M3", "M5", "M15", "H1", "D1")] == [60, 180, 300, 900, 3600, 86400]
    for bad in ("", "5", "M0", "m5", "M5x", None, 5):
        with pytest.raises(ConfigError):
            timeframe_seconds(bad)


def test_scalability_new_symbol_added_by_configuration_only(tmp_path):
    """Phase 2 'new symbol onboarding': a symbol that exists in no source file is added purely
    through a YAML file and replays correctly. A temporary config is used; the shipped
    v3.4 scope (V75, V100, V50) is not extended."""
    raw = yaml.safe_load((SRC.parents[1] / "config" / "instruments.yaml").read_text())
    raw["instruments"]["TEST_SYM"] = {
        "htf": "M30", "htf_swing_n": 3, "ltf": "M10", "ltf_swing_n": 2,
        "eqh_eql_tolerance_points": 100, "eqh_eql_tolerance_atr_mult": 0.10,
        "point_size": 0.01,
    }
    path = tmp_path / "with_new_symbol.yaml"
    path.write_text(yaml.safe_dump(raw))

    src_text = "\n".join(p.read_text() for p in SRC.rglob("*.py"))
    assert "TEST_SYM" not in src_text  # nothing in source knows about it

    cfg = load_config(path)
    bank = DetectorBank(cfg)
    assert ("TEST_SYM", "M10") in bank.keys() and ("TEST_SYM", "M30") in bank.keys()
    for tf, n in (("M10", 2), ("M30", 3)):
        series = walk(300, 8, "TEST_SYM", tf)
        events = replay_series(series, bank.detector("TEST_SYM", tf))
        assert events and all(e.symbol == "TEST_SYM" for e in events)
        assert {e.payload["confirmation_bar_index"] - e.payload["bar_index"] for e in events} == {n}
    # existing symbols are unaffected
    assert get_swing_params(cfg, "V75", "M5")["n"] == 2


def test_no_hardcoded_instruments_or_timeframe_roles_in_source():
    """Static check: no string literal in src (docstrings excluded) names a v3.4 instrument."""
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr)
                      and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                if node.value in ("V75", "V100", "V50"):
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
    assert offenders == []
