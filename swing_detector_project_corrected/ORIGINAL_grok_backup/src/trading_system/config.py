"""Configuration management.

Instrument parameters from Build Order v3.4 Section 8 are loaded from
YAML and remain fully configurable without changing detector source code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import json
import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "instruments.yaml"


class ConfigError(Exception):
    pass


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "instruments" not in data:
        raise ConfigError("Config must contain an 'instruments' mapping")
    return data


def get_instrument_config(config: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    instruments = config.get("instruments", {})
    if symbol not in instruments:
        raise ConfigError(
            f"Symbol '{symbol}' not in configuration. "
            f"Available: {list(instruments.keys())}"
        )
    return instruments[symbol]


def get_swing_params(config: Dict[str, Any], symbol: str, timeframe: str) -> Dict[str, Any]:
    """
    Return the swing detector parameters for a given symbol + timeframe.

    HTF uses htf_swing_n, LTF uses ltf_swing_n.
    """
    inst = get_instrument_config(config, symbol)
    htf = inst["htf"]
    ltf = inst["ltf"]
    if timeframe == htf:
        n = inst["htf_swing_n"]
    elif timeframe == ltf:
        n = inst["ltf_swing_n"]
    else:
        # Allow arbitrary timeframe for the scalability / architecture test
        # but require an explicit 'n' override if not HTF/LTF
        raise ConfigError(
            f"Timeframe '{timeframe}' is neither HTF ({htf}) nor LTF ({ltf}) "
            f"for {symbol}. For architecture tests supply an explicit n via "
            f"params override."
        )
    return {"n": int(n)}


def list_symbols(config: Dict[str, Any]) -> List[str]:
    return list(config.get("instruments", {}).keys())


def list_timeframes(config: Dict[str, Any], symbol: str) -> List[str]:
    inst = get_instrument_config(config, symbol)
    return [inst["htf"], inst["ltf"]]


def config_hash(config: Dict[str, Any]) -> str:
    """Stable hash of the configuration for run manifests."""
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def dataset_hash(data_bytes: bytes) -> str:
    return hashlib.sha256(data_bytes).hexdigest()[:16]
