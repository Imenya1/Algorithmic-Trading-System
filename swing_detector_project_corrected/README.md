# Phase 2 - Swing Detector (audited and corrected)

Governing document: `Trading_System_Build_Order_v3_4.docx`. Scope: Swing Detector only (V75, V100, V50).
See `AUDIT_REPORT.md` for the independent audit, findings, change log and readiness assessment.

## Architecture
OHLC CSV -> `data/ohlc.py` loader -> `data/validation.py` -> `replay.py` (`replay_series` / `stream_candles` / `DetectorBank`)
-> `detectors/swing.py` (`SwingDetector.step`, the only swing algorithm) -> `DetectorEvent` (`events.py`)
-> `visualiser.py`, `metrics.py`, `review_set.py`, regression snapshots and run manifests.
Parameters come from `config/instruments.yaml` via `config.py`.

## Commands
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/swing_cli.py validate   --csv data.csv --symbol V75 --timeframe M5
python scripts/swing_cli.py replay     --csv data.csv --symbol V75 --timeframe M5 --out events.json
python scripts/swing_cli.py chart      --csv data.csv --symbol V75 --timeframe M5 --out chart.png --window 100 220
python scripts/swing_cli.py review-set --csv data.csv --symbol V75 --timeframe M5 --review-until 2025-06-30T23:59:59 --seed 1 --out-dir review/

pytest                                              # whole suite
pytest tests/test_no_lookahead.py                   # no-lookahead + mutation checks
pytest tests/test_replay_consistency.py             # batch == streaming, snapshots, determinism
python scripts/run_phase2_validation.py             # runs suite + benchmarks, writes reports/
```
CSV header: `time,open,high,low,close[,volume]`. Source files are never modified.

## Key rules
* Confirmation: swing at pivot p is emitted when candle p+n closes; `confirmation_time` is that candle's CLOSE time.
* Tie rule: strict inequality (equal high/low = no pivot). Unresolved in the supplied documents.
* No minimum leg, suppression or supersession (undefined). No EQH/EQL.
* `data.candle_time` (config) states whether `Candle.time` is bar open (default) or close. Unresolved assumption: confirm against real Deriv data.
* Gaps never stop the detector: bridged by default (`detectors.swing.gap_policy`), swings spanning a gap get `spans_gap`, `gap_missing_bars`, `gap_status` (`confirmed` if upstream set `Candle.gap_confirmed`, else `unverified`). Backfill/connection handling is upstream, not the detector's concern.
* Invalid, late, conflicting or too-closely-spaced candles raise `DetectorInputError`; nothing is repaired or silently dropped.

## Tie rule (`detectors.swing.tie_rule`)

How equal highs/lows are treated when deciding a swing pivot (Build Order v3.4 is silent on ties):

| Value | Rule |
|---|---|
| `strict` (default) | Pivot must be strictly beyond all n bars on both sides. Unchanged behaviour. |
| `mt5` | Strictly beyond the n bars after the pivot, equal-or-beyond the n bars before it (MetaTrader 5 Fractals convention). |

To compare against the MT5 Fractals indicator, use the separate config (n=2 and `mt5`), leaving
`config/instruments.yaml` (Section 8 values) untouched:

    python scripts/swing_cli.py chart --csv data/V75_H1.csv --symbol V75 --timeframe H1 \
        --config config/mt5_compare.yaml --out output/V75_H1_mt5_compare.png --window 2001 2180

Tests: `pytest` (a `pytest.ini` now points it at `tests/` so the backup folder is not collected).
