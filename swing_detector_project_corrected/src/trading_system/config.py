"""Configuration loading, validation and parameter lookup.

Instrument parameters from Build Order v3.4 Section 8 live in YAML. Nothing in the
detector source hard-codes a symbol, timeframe or swing ``n``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "instruments.yaml"

CANDLE_TIME_CONVENTIONS = ("open", "close")
GAP_POLICIES = ("bridge", "reset", "error")
TIE_RULES = ("strict", "mt5")

_TIMEFRAME_RE = re.compile(r"^([MHD])(\d+)$")
_UNIT_SECONDS = {"M": 60, "H": 3600, "D": 86400}
_INSTRUMENT_KEYS = (
    "htf", "htf_swing_n", "ltf", "ltf_swing_n",
    "eqh_eql_tolerance_points", "eqh_eql_tolerance_atr_mult",
    # Price units per broker "point". Build Order Section 8 states EQH/EQL tolerances in
    # points while ATR and prices are in price units, so a conversion is required.
    "point_size",
)


class ConfigError(ValueError):
    """Raised for any invalid or inconsistent configuration."""


def timeframe_seconds(timeframe: str) -> int:
    """Duration of a timeframe label such as ``M5`` or ``H1`` in seconds."""
    if not isinstance(timeframe, str):
        raise ConfigError(f"Timeframe must be a string like 'M5' or 'H1', got {timeframe!r}")
    match = _TIMEFRAME_RE.match(timeframe)
    if not match or int(match.group(2)) < 1:
        raise ConfigError(
            f"Invalid timeframe {timeframe!r}: expected <M|H|D><positive integer>, e.g. 'M5', 'H1'"
        )
    return _UNIT_SECONDS[match.group(1)] * int(match.group(2))


def _check_swing_n(value: Any, where: str) -> int:
    # bool is a subclass of int; True/False are never a valid lookback.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: swing n must be an integer >= 1, got {value!r}")
    if value < 1:
        raise ConfigError(f"{where}: swing n must be >= 1, got {value}")
    return value


def validate_config(config: Any) -> Dict[str, Any]:
    """Validate a parsed configuration and return it. Raises ConfigError with a clear message."""
    if not isinstance(config, dict) or not isinstance(config.get("instruments"), dict) \
            or not config["instruments"]:
        raise ConfigError("Config must contain a non-empty 'instruments' mapping")

    for symbol, inst in config["instruments"].items():
        if not isinstance(inst, dict):
            raise ConfigError(f"Instrument {symbol!r}: expected a mapping of parameters")
        missing = [k for k in _INSTRUMENT_KEYS if k not in inst]
        if missing:
            raise ConfigError(f"Instrument {symbol!r}: missing keys {missing}")
        unknown = sorted(set(inst) - set(_INSTRUMENT_KEYS))
        if unknown:
            raise ConfigError(f"Instrument {symbol!r}: unknown keys {unknown}")
        htf_s = timeframe_seconds(inst["htf"])
        ltf_s = timeframe_seconds(inst["ltf"])
        if htf_s <= ltf_s:
            raise ConfigError(
                f"Instrument {symbol!r}: HTF ({inst['htf']}) must be longer than LTF ({inst['ltf']})"
            )
        _check_swing_n(inst["htf_swing_n"], f"Instrument {symbol!r} htf_swing_n")
        _check_swing_n(inst["ltf_swing_n"], f"Instrument {symbol!r} ltf_swing_n")
        # EQH/EQL tolerance parameters (Build Order Section 8). Finite, non-negative numbers.
        for key in ("eqh_eql_tolerance_points", "eqh_eql_tolerance_atr_mult"):
            val = inst[key]
            if (isinstance(val, bool) or not isinstance(val, (int, float))
                    or not math.isfinite(val) or val < 0):
                raise ConfigError(f"Instrument {symbol!r} {key} must be a finite non-negative number, got {val!r}")
        ps = inst["point_size"]
        if isinstance(ps, bool) or not isinstance(ps, (int, float)) or not math.isfinite(ps) or ps <= 0:
            raise ConfigError(f"Instrument {symbol!r} point_size must be a finite positive number, got {ps!r}")

    convention = config.get("data", {}).get("candle_time", "open")
    if convention not in CANDLE_TIME_CONVENTIONS:
        raise ConfigError(f"data.candle_time must be one of {CANDLE_TIME_CONVENTIONS}, got {convention!r}")
    gap_policy = config.get("detectors", {}).get("swing", {}).get("gap_policy", "bridge")
    if gap_policy not in GAP_POLICIES:
        raise ConfigError(f"detectors.swing.gap_policy must be one of {GAP_POLICIES}, got {gap_policy!r}")
    tie_rule = config.get("detectors", {}).get("swing", {}).get("tie_rule", "strict")
    if tie_rule not in TIE_RULES:
        raise ConfigError(f"detectors.swing.tie_rule must be one of {TIE_RULES}, got {tie_rule!r}")
    return config


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file {path} is not valid YAML: {exc}") from exc
    return validate_config(data)


def get_instrument_config(config: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    instruments = config.get("instruments", {})
    if symbol not in instruments:
        raise ConfigError(
            f"Symbol '{symbol}' not in configuration. Available: {list(instruments.keys())}"
        )
    return instruments[symbol]


def get_swing_params(config: Dict[str, Any], symbol: str, timeframe: str) -> Dict[str, Any]:
    """Swing detector parameters for one (symbol, timeframe).

    ``n`` is chosen by the timeframe's role for that symbol (HTF or LTF). A timeframe that is
    neither is rejected rather than guessed.
    """
    inst = get_instrument_config(config, symbol)
    if timeframe == inst["htf"]:
        n = inst["htf_swing_n"]
    elif timeframe == inst["ltf"]:
        n = inst["ltf_swing_n"]
    else:
        raise ConfigError(
            f"Timeframe '{timeframe}' is neither HTF ({inst['htf']}) nor LTF ({inst['ltf']}) "
            f"for {symbol}. Add it to the configuration to use it."
        )
    return {
        "n": _check_swing_n(n, f"{symbol}/{timeframe}"),
        "candle_time": config.get("data", {}).get("candle_time", "open"),
        "gap_policy": config.get("detectors", {}).get("swing", {}).get("gap_policy", "bridge"),
        "tie_rule": config.get("detectors", {}).get("swing", {}).get("tie_rule", "strict"),
    }


def get_eqh_eql_params(config: Dict[str, Any], symbol: str, timeframe: str) -> Dict[str, Any]:
    """EQH/EQL detector parameters for one (symbol, timeframe).

    Tolerance (points and ATR multiple) comes from Build Order v3.4 Section 8 (per instrument);
    ``point_size`` converts points to price units. ATR period and min_touches come from
    detectors.eqh_eql (Section 9). ``candle_time`` is the same data convention the swing
    detector uses, so both compute the same confirmation time.
    """
    # Ensure the symbol/timeframe pair is known (raises if not).
    swing = get_swing_params(config, symbol, timeframe)
    inst = get_instrument_config(config, symbol)
    eqh_cfg = config.get("detectors", {}).get("eqh_eql", {}) or {}
    unknown = sorted(set(eqh_cfg) - {"atr_period", "min_touches", "max_swings_kept"})
    if unknown:
        raise ConfigError(f"detectors.eqh_eql: unknown keys {unknown}")
    ints = {}
    for key, default in (("atr_period", 14), ("min_touches", 2), ("max_swings_kept", 500)):
        v = eqh_cfg.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ConfigError(f"detectors.eqh_eql.{key} must be an integer, got {v!r}")
        ints[key] = v
    return {
        "tolerance_points": float(inst["eqh_eql_tolerance_points"]),
        "tolerance_atr_mult": float(inst["eqh_eql_tolerance_atr_mult"]),
        "point_size": float(inst["point_size"]),
        "candle_time": swing["candle_time"],
        **ints,
    }


def list_symbols(config: Dict[str, Any]) -> List[str]:
    return list(config.get("instruments", {}).keys())


def list_timeframes(config: Dict[str, Any], symbol: str) -> List[str]:
    inst = get_instrument_config(config, symbol)
    return [inst["htf"], inst["ltf"]]


def config_hash(config: Dict[str, Any]) -> str:
    """Stable hash of the configuration for run manifests."""
    canonical = json.dumps(copy.deepcopy(config), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def dataset_hash(data_bytes: bytes) -> str:
    return hashlib.sha256(data_bytes).hexdigest()[:16]
