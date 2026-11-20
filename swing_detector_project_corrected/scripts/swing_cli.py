#!/usr/bin/env python3
"""Command-line interface: validate OHLC data, replay it through the Swing Detector,
draw charts and build the fixed visual review set.

  python scripts/swing_cli.py validate --csv data.csv --symbol V75 --timeframe M5
  python scripts/swing_cli.py replay   --csv data.csv --symbol V75 --timeframe M5 --out events.json
  python scripts/swing_cli.py replay-eq / chart-eq   same as replay / chart, with the EQH/EQL detector (Detector 2) run on the swings
  python scripts/swing_cli.py chart    --csv data.csv --symbol V75 --timeframe M5 --out chart.png [--window 100 220]
  python scripts/swing_cli.py review-set --csv data.csv --symbol V75 --timeframe M5 --review-until 2025-06-30T23:59:59 --seed 1 --out-dir review/

CSV header: time,open,high,low,close[,volume]. The source file is only ever read.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_system.config import load_config, get_swing_params  # noqa: E402
from trading_system.data.ohlc import OHLCSeries  # noqa: E402
from trading_system.data.validation import DataValidationError, validate_series  # noqa: E402
from trading_system.replay import (create_eqh_eql_detector, create_swing_detector,  # noqa: E402
                                    replay_series, replay_swing_eqh_eql)
from trading_system.review_set import build_review_set, render_review_set  # noqa: E402
from trading_system.visualiser import plot_swings  # noqa: E402


def _load(args) -> OHLCSeries:
    try:
        return OHLCSeries.from_csv(Path(args.csv), args.symbol, args.timeframe, epoch_unit=args.epoch_unit)
    except DataValidationError as exc:
        print(f"DATA INVALID: {exc}", file=sys.stderr)
        for issue in exc.issues[:50]:
            print(f"  row {issue.index}: [{issue.code}] {issue.message}", file=sys.stderr)
        raise SystemExit(2)


def _events(args, series):
    cfg = load_config(Path(args.config) if args.config else None)
    detector = create_swing_detector(cfg, args.symbol, args.timeframe)
    try:
        return replay_series(series, detector), get_swing_params(cfg, args.symbol, args.timeframe)
    except ValueError as exc:  # DetectorInputError (invalid/late/conflicting candle) or GapError in strict mode
        print(f"REPLAY STOPPED: {exc}", file=sys.stderr)
        raise SystemExit(3)


def _events_with_eq(args, series):
    """Swing events plus EQ_CLUSTER events from the single Swing -> EQH/EQL pipeline."""
    cfg = load_config(Path(args.config) if args.config else None)
    try:
        swings, clusters = replay_swing_eqh_eql(series, create_swing_detector(cfg, args.symbol, args.timeframe),
                                                create_eqh_eql_detector(cfg, args.symbol, args.timeframe))
    except ValueError as exc:
        print(f"REPLAY STOPPED: {exc}", file=sys.stderr)
        raise SystemExit(3)
    return swings, clusters


def cmd_replay_eq(args) -> int:
    series = _load(args)
    swings, clusters = _events_with_eq(args, series)
    merged = sorted(swings + clusters, key=lambda e: e.confirmation_time)  # stable: swings first on ties
    Path(args.out).write_text(json.dumps([e.to_dict() for e in merged], indent=2), encoding="utf-8")
    print(f"{args.symbol}/{args.timeframe}: {len(series)} candles -> {len(swings)} swings, "
          f"{len(clusters)} EQ_CLUSTER events -> {args.out}")
    return 0


def cmd_chart_eq(args) -> int:
    series = _load(args)
    swings, clusters = _events_with_eq(args, series)
    window = tuple(args.window) if args.window else None
    path = plot_swings(series, swings + clusters, save_path=Path(args.out), window=window, max_bars=args.max_bars)
    print(f"Chart written: {path}")
    return 0


def cmd_validate(args) -> int:
    series = _load(args)
    params = get_swing_params(load_config(Path(args.config) if args.config else None), args.symbol, args.timeframe)
    report = validate_series(series, allow_gaps=not args.strict_gaps, min_bars=2 * params["n"] + 1)
    print(report.summary())
    for issue in report.issues[:200]:
        print(f"  candle {issue.index}: {issue.severity.upper()} [{issue.code}] {issue.message}")
    return 0 if report.valid else 1


def cmd_replay(args) -> int:
    series = _load(args)
    events, params = _events(args, series)
    Path(args.out).write_text(json.dumps([e.to_dict() for e in events], indent=2), encoding="utf-8")
    print(f"{args.symbol}/{args.timeframe}: n={params['n']}, {len(series)} candles -> {len(events)} confirmed swings -> {args.out}")
    return 0


def cmd_chart(args) -> int:
    series = _load(args)
    events, _ = _events(args, series)
    window = tuple(args.window) if args.window else None
    path = plot_swings(series, events, save_path=Path(args.out), window=window, max_bars=args.max_bars)
    print(f"Chart written: {path}")
    return 0


def cmd_review_set(args) -> int:
    series = _load(args)
    events, _ = _events(args, series)
    rs = build_review_set(series, datetime.fromisoformat(args.review_until), seed=args.seed,
                          cut_points=args.cut_points or ())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review_set.json").write_text(json.dumps(rs, indent=2), encoding="utf-8")
    paths = render_review_set(series, events, rs, out)
    print(f"Review set v{rs['version']} (seed {rs['seed']}): {len(paths)} windows rendered to {out}")
    print("Visual sign-off is NOT recorded by this command; a human must record it in the run manifest.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("validate", cmd_validate), ("replay", cmd_replay), ("chart", cmd_chart),
                     ("review-set", cmd_review_set), ("replay-eq", cmd_replay_eq), ("chart-eq", cmd_chart_eq)):
        sp = sub.add_parser(name)
        sp.add_argument("--csv", required=True)
        sp.add_argument("--symbol", required=True)
        sp.add_argument("--timeframe", required=True)
        sp.add_argument("--config", help="path to instruments.yaml (default: config/instruments.yaml)")
        sp.add_argument("--epoch-unit", choices=["s", "ms"], help="required if the time column is numeric epoch")
        sp.set_defaults(fn=fn)
        if name == "validate":
            sp.add_argument("--strict-gaps", action="store_true", help="report gaps as errors (default: warnings; the detector bridges them)")
        if name in ("replay", "replay-eq"):
            sp.add_argument("--out", required=True)
        if name in ("chart", "chart-eq"):
            sp.add_argument("--out", required=True)
            sp.add_argument("--window", nargs=2, type=int, metavar=("FIRST_BAR", "LAST_BAR"))
            sp.add_argument("--max-bars", type=int, default=180)
        if name == "review-set":
            sp.add_argument("--review-until", required=True, help="ISO time: end of the validation segment")
            sp.add_argument("--seed", type=int, required=True)
            sp.add_argument("--cut-points", nargs="*", type=int)
            sp.add_argument("--out-dir", required=True)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
