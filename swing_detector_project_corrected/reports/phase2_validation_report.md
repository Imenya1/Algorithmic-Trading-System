# Phase 2 Swing Detector - Validation Report

Generated 2026-11-19T01:19:27+00:00 | code 0.2.0-phase2-swing-audited | config hash `9cc29c3ceb782dc7`

**Automated suite:** 206 passed in 21.15s (exit code 0).

**All data below is SYNTHETIC (seeded random walks). It demonstrates behaviour and tooling, not market accuracy.**

## Test report

| Validation | Required result | Actual result | Status |
|---|---|---|---|
| No-lookahead | 100% pass | 25/25 tests passed | PASS |
| Batch equals streaming | Exact match | 13/13 tests passed | PASS |
| Determinism | Identical output | 1/1 tests passed | PASS |
| Fault injection | Tests pass | 15/15 tests passed | PASS |
| Normal-window precision | >= 90% | No reference-labelled sample supplied (evaluator ready: reference_eval.py) | PENDING |
| Normal-window recall | >= 90% | No reference-labelled sample supplied (evaluator ready: reference_eval.py) | PENDING |
| High-volatility precision | >= 80% | No reference-labelled sample supplied (evaluator ready: reference_eval.py) | PENDING |
| High-volatility recall | >= 80% | No reference-labelled sample supplied (evaluator ready: reference_eval.py) | PENDING |
| Yearly stability | Stable | No multi-year real data supplied (metrics per year implemented) | PENDING |
| Throughput | Within 20% of saved baseline | V75/H1: 158,513 c/s; V75/M5: 163,342 c/s; V100/M15: 166,630 c/s; V100/M3: 165,948 c/s; V50/H1: 157,128 c/s; V50/M15: 159,653 c/s | PASS |
| Configuration-only onboarding | Successful | 2/2 tests passed | PASS |
| Visual sign-off | Recorded | Review set and charts generated; no developer sign-off recorded | PENDING |

## Throughput (replay candles/second, median of 5, synthetic series of 20,000 candles)

| Symbol/TF | Median c/s | Min | Max | Verdict vs saved baseline |
|---|---:|---:|---:|---|
| V75/H1 | 158,513 | 138,913 | 166,175 | PASS |
| V75/M5 | 163,342 | 160,945 | 165,070 | PASS |
| V100/M15 | 166,630 | 164,325 | 168,351 | PASS |
| V100/M3 | 165,948 | 162,377 | 168,229 | PASS |
| V50/H1 | 157,128 | 96,134 | 168,155 | PASS |
| V50/M15 | 159,653 | 157,390 | 160,265 | PASS |

## Regression snapshots

- V75/H1: MATCH
- V75/M5: MATCH
- V100/M15: MATCH
- V100/M3: MATCH
- V50/H1: MATCH
- V50/M15: MATCH

Characterisation metrics per symbol/timeframe/year: `reports/characterisation_metrics.csv`. Suppression, supersession, largest-bar share and EQH/EQL frequency are not computed (undefined or out of scope; reasons in the CSV schema notes in `metrics.py`).

## Not available without external input

- Real V75/V100/V50 OHLC history and its train/validation/holdout split.
- The ~200-bar hand-labelled reference sample per symbol/timeframe.
- Developer visual sign-off (Section 5).
- The Research Specification (tie handling, minimum leg, suppression/supersession definitions).
