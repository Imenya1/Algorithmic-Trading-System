#!/usr/bin/env python3
"""Phase 2 validation runner for the Swing Detector.

EXECUTES the automated test suite and the throughput benchmark, then writes manifests,
regression snapshots, characterisation metrics, charts and a report. Every status in the
report is derived from what was actually run: PASS only if the tests ran and passed;
PENDING where the required evidence (real data, reference labels, human sign-off) does
not exist. All data used here is SYNTHETIC and says so everywhere it appears.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_system import __version__  # noqa: E402
from trading_system.config import config_hash, get_swing_params, list_symbols, list_timeframes, load_config, timeframe_seconds  # noqa: E402
from trading_system.metrics import characterise  # noqa: E402
from trading_system.replay import (RunManifest, check_throughput_regression, compare_regression_snapshot,  # noqa: E402
                                   create_swing_detector, measure_throughput, replay_series,
                                   save_regression_snapshot)
from trading_system.review_set import build_review_set, render_review_set  # noqa: E402
from trading_system.utils.synthetic import generate_random_walk  # noqa: E402
from trading_system.visualiser import plot_swings  # noqa: E402

SEED = 20261118
N_BARS_BENCH = 20_000
TOLERANCE = 0.20  # Open Decision 5 default


def run_pytest(junit: Path):
    proc = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q", "-p", "no:cacheprovider",
                           f"--junitxml={junit}"], cwd=ROOT, capture_output=True, text=True)
    cases = []
    for tc in ET.parse(junit).getroot().iter("testcase"):
        outcome = "failed" if (tc.find("failure") is not None or tc.find("error") is not None) else \
                  "skipped" if tc.find("skipped") is not None else "passed"
        cases.append((tc.get("classname", "").split(".")[-1], tc.get("name", ""), outcome))
    return proc.returncode, proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "", cases


def group_status(cases, file, name_filter=None):
    sel = [c for c in cases if c[0] == file and (name_filter is None or name_filter(c[1]))]
    if not sel:
        return "FAIL", "no tests found"
    passed = sum(c[2] == "passed" for c in sel)
    return ("PASS" if passed == len(sel) else "FAIL"), f"{passed}/{len(sel)} tests passed"


def main() -> int:
    reports = ROOT / "reports"
    for sub in ("snapshots", "charts", "manifests", "benchmarks", "review_set"):
        (reports / sub).mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    cfg_h = config_hash(cfg)

    print("Running pytest ...")
    rc, last_line, cases = run_pytest(reports / "pytest_junit.xml")
    print(f"  {last_line}")

    # ---- per (symbol, timeframe): replay, snapshot, benchmark, metrics, chart, manifest -------
    baseline_path = reports / "benchmarks" / "throughput_baseline.json"
    previous = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    new_baseline, bench_rows, metric_rows, snapshot_notes = {}, [], [], []
    manifests = []
    for symbol in list_symbols(cfg):
        for tf in list_timeframes(cfg, symbol):
            key = f"{symbol}|{tf}"
            minutes = timeframe_seconds(tf) // 60
            seed = SEED + sum(map(ord, key))
            series = generate_random_walk(N_BARS_BENCH, seed=seed, symbol=symbol, timeframe=tf, timeframe_minutes=minutes)
            ds_hash = hashlib.sha256(series.to_dataframe().to_csv(index=False).encode()).hexdigest()[:16]
            det = create_swing_detector(cfg, symbol, tf)
            events = replay_series(series, det)

            snap = reports / "snapshots" / f"swing_{symbol}_{tf}.json"
            if snap.exists():
                diffs = compare_regression_snapshot(events, snap)
                snapshot_notes.append((key, "MATCH" if not diffs else f"CHANGED ({len(diffs)} differences)"))
            else:
                snapshot_notes.append((key, "SAVED (first run)"))
            snap_hash = save_regression_snapshot(events, snap) if not snap.exists() else \
                hashlib.sha256(snap.read_bytes()).hexdigest()[:16]

            bench = measure_throughput(series, det, repeats=5)
            verdict = check_throughput_regression(bench, previous.get(key), TOLERANCE)
            new_baseline[key] = previous.get(key) or bench  # a baseline is only replaced deliberately
            bench_rows.append((key, bench, verdict))

            metric_rows += characterise(series, events)
            plot_swings(series, events, save_path=reports / "charts" / f"{symbol}_{tf}_SYNTHETIC.png",
                        title=f"{symbol} . {tf} . n={get_swing_params(cfg, symbol, tf)['n']} . SYNTHETIC DATA",
                        max_bars=160, dpi=130)

            man = RunManifest(code_version=__version__, dataset_hash=ds_hash, configuration_hash=cfg_h,
                              random_seed=seed, date_range=(series[0].time.isoformat(), series[len(series) - 1].time.isoformat()),
                              symbol=symbol, timeframe=tf, detector_parameters=get_swing_params(cfg, symbol, tf),
                              performance_benchmark=bench, regression_snapshot_ref=snap.name,
                              data_origin="SYNTHETIC random walk - not market data",
                              outstanding_validation_issues=[
                                  "No real market data supplied", "No reference-labelled sample supplied",
                                  "Developer visual sign-off not recorded"])
            man.save(reports / "manifests" / f"run_manifest_{symbol}_{tf}.json")
            manifests.append(man)

            if key == "V75|M5":  # demonstrate the fixed review set tooling on one stream
                rs = build_review_set(series, series[int(len(series) * 0.8)].time, seed=SEED, window_bars=120)
                (reports / "review_set" / "review_set_V75_M5.json").write_text(json.dumps(rs, indent=2))
                render_review_set(series, events, rs, reports / "review_set" / "V75_M5")

    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(new_baseline, indent=2))
    with open(reports / "characterisation_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(metric_rows[0]))
        w.writeheader()
        w.writerows(metric_rows)

    # ---- status table: derived from executed results only -------------------------------------
    fails = [v for _, _, v in bench_rows if v["status"] == "FAIL"]
    first_run = all(v["status"] == "BASELINE_RECORDED" for _, _, v in bench_rows)
    rows = []

    def add(name, required, status_actual):
        rows.append((name, required, status_actual[1], status_actual[0]))

    add("No-lookahead", "100% pass", group_status(cases, "test_no_lookahead"))
    add("Batch equals streaming", "Exact match", group_status(cases, "test_replay_consistency",
                                                              lambda n: n.startswith(("test_batch", "test_chunked", "test_snapshot"))))
    add("Determinism", "Identical output", group_status(cases, "test_replay_consistency", lambda n: "determinism" in n))
    add("Fault injection", "Tests pass", group_status(cases, "test_data_validation"))
    for name, req in (("Normal-window precision", ">= 90%"), ("Normal-window recall", ">= 90%"),
                      ("High-volatility precision", ">= 80%"), ("High-volatility recall", ">= 80%")):
        add(name, req, ("PENDING", "No reference-labelled sample supplied (evaluator ready: reference_eval.py)"))
    add("Yearly stability", "Stable", ("PENDING", "No multi-year real data supplied (metrics per year implemented)"))
    thr_actual = "; ".join(f"{k.replace('|', '/')}: {b['median_candles_per_sec']:,.0f} c/s" for k, b, _ in bench_rows)
    if fails:
        add("Throughput", f"Within {TOLERANCE:.0%} of saved baseline", ("FAIL", thr_actual))
    elif first_run:
        add("Throughput", "Within budget (Section 4 / Open Decision 5)", ("BASELINE RECORDED", thr_actual + " (no previous baseline; absolute budget not defined)"))
    else:
        add("Throughput", f"Within {TOLERANCE:.0%} of saved baseline", ("PASS", thr_actual))
    add("Configuration-only onboarding", "Successful", group_status(cases, "test_config", lambda n: "scalability" in n or "hardcoded" in n))
    add("Visual sign-off", "Recorded", ("PENDING", "Review set and charts generated; no developer sign-off recorded"))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    md = [f"# Phase 2 Swing Detector - Validation Report", "",
          f"Generated {now} | code {__version__} | config hash `{cfg_h}`", "",
          f"**Automated suite:** {last_line} (exit code {rc}).", "",
          "**All data below is SYNTHETIC (seeded random walks). It demonstrates behaviour and tooling, not market accuracy.**", "",
          "## Test report", "", "| Validation | Required result | Actual result | Status |", "|---|---|---|---|"]
    md += [f"| {n} | {r} | {a} | {s} |" for n, r, a, s in rows]
    md += ["", "## Throughput (replay candles/second, median of 5, synthetic series of "
           f"{N_BARS_BENCH:,} candles)", "", "| Symbol/TF | Median c/s | Min | Max | Verdict vs saved baseline |", "|---|---:|---:|---:|---|"]
    md += [f"| {k.replace('|', '/')} | {b['median_candles_per_sec']:,.0f} | {b['min_candles_per_sec']:,.0f} | {b['max_candles_per_sec']:,.0f} | {v['status']} |" for k, b, v in bench_rows]
    md += ["", "## Regression snapshots", ""] + [f"- {k.replace('|', '/')}: {s}" for k, s in snapshot_notes]
    md += ["", "Characterisation metrics per symbol/timeframe/year: `reports/characterisation_metrics.csv`. "
           "Suppression, supersession, largest-bar share and EQH/EQL frequency are not computed (undefined or out of scope; reasons in the CSV schema notes in `metrics.py`).", "",
           "## Not available without external input", "",
           "- Real V75/V100/V50 OHLC history and its train/validation/holdout split.",
           "- The ~200-bar hand-labelled reference sample per symbol/timeframe.",
           "- Developer visual sign-off (Section 5).",
           "- The Research Specification (tie handling, minimum leg, suppression/supersession definitions).", ""]
    (reports / "phase2_validation_report.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(f"{s:<18} {n}: {a}" for n, r, a, s in rows))
    print(f"Report: {reports / 'phase2_validation_report.md'}")
    return 0 if rc == 0 and not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
