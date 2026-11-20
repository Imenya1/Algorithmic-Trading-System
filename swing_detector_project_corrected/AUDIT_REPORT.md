# Independent Audit - Swing Detector (Phase 2)

Original implementation (Grok) preserved unchanged in `ORIGINAL_grok_backup/`. Governing document: Build Order v3.4.
All results below were produced by executing code in this session. Data used is synthetic; no real V75/V100/V50 history, hand-labelled sample or Research Specification was available.

## 1. Original architecture
| Stage | File |
|---|---|
| Config | `config.py`, `config/instruments.yaml` |
| OHLC load/validate | `data/ohlc.py`, `data/validation.py` |
| Replay/stream, manifest, snapshot, benchmark | `replay.py` |
| Detector interface + swing | `detectors/base.py`, `detectors/swing.py` |
| Event schema | `events.py` |
| Chart | `visualiser.py` |
| Validation runner | `scripts/run_phase2_validation.py` |
| Tests | 13 tests (all passed on the original) |

## 2. Assessment of the original
Core fractal arithmetic was correct (0 mismatches vs a brute-force oracle, n=1..4, 20 series) and batch/streaming shared one `step`. The surrounding system was not: the chart drew wrong markers, confirmation time was mis-stamped, bad input could create swings or vanish silently, and tests/validation script overstated assurance.

## 3. Findings
Severity: C1 Critical, C2 Major, C3 Quality. "Evidence" = probe run on the original.

| ID | Sev | Where | Problem and evidence | Correction | Verified by |
|---|---|---|---|---|---|
| F1 | C1 | `swing.py step` | `confirmation_time = candle.time` = bar OPEN time, but spec says CLOSE of the confirming candle. M5 example: event stamped 10:35, candle closes 10:40. Event time precedes the information. | Close time derived from timeframe duration; `data.candle_time` config states data convention. | `test_known_swings_exact...`, `test_event_is_published_on_exactly...`, `timing_check` |
| F2 | C1 | `swing.py` | NaN high in a neighbour compared False, so no pivot was blocked: false SWING_HIGH emitted at the NaN bar. | Every candle checked (finite, relations, closed) before use; rejected without state change. | `test_nan_neighbour_cannot_create_a_false_swing`, `test_conflicting_duplicate_...` |
| F3 | C1 | `swing.py` | Fractal window silently spanned a 10-hour gap and emitted a swing. | Superseded by the agreed gap design (Addendum): gaps are bridged and flagged by default; `reset` and strict `error` remain as options. | gap tests |
| F4 | C2 | `visualiser.py`, `ohlc.py` | `OHLCSeries` renumbered indices on slice; chart used absolute event indices. 500 bars/180 window: 48 of 48 drawn markers sat on candles whose price did not match the swing. | Indices preserved through slices; markers computed by `compute_markers` and price-checked against the candle. | `test_default_window_markers_sit_on_the_correct_candles`, `..._for_every_window` |
| F5 | C2 | `visualiser.py` | Drew every event regardless of confirmation; no historical window; no confirmation marker; global matplotlib rcParams mutation. | `as_of`/window-end filter, hidden-count in title, confirmation ticks, `window=` selection, `rc_context`. | `test_unconfirmed_swings_...`, `test_as_of_...` |
| F6 | C2 | `ohlc.py` | slice re-indexing also made `bar_index` differ between a slice and the full run (2 of 50 events matched). | Same fix as F4. | `test_slice_preserves_absolute_bar_indices...` |
| F7 | C2 | `swing.py` | Out-of-order and conflicting-duplicate candles silently ignored (no error, no counter). | Exact repeat: idempotent and counted. Conflict/out-of-order/unclosed: raise. | `test_conflicting_duplicate_out_of_order...` |
| F8 | C2 | `validation.py` | `validate_series` never passed an expected interval: a missing candle reported "OK". NaT accepted. Empty set only a warning. Non-numeric gave raw `ValueError`. No insufficient-history, tz-mix or missing-column list. | Rewritten: gap detection from timeframe, NaT/type/tz/empty/min-history checks, row-level errors with raw value, `DataValidationError`. | `test_data_validation.py` (14 tests) |
| F9 | C2 | `config.py` | No validation (n=0, "3", True, htf==ltf accepted; missing keys gave KeyError); no config-driven detector factory (script/tests hard-coded n and symbol); `pass` under `defaults`. | `validate_config`, `create_swing_detector(config,...)`, `DetectorBank`; YAML cleaned. | `test_config.py` (13 invalid-config cases, static no-hardcode check) |
| F10 | C2 | `tests/test_no_lookahead.py` | Weak: one series, n=2, only "prefix events appear in full run". Cannot see omissions, wrong-but-consistent timing, or repaint. | Four properties (truncation equality, future-perturbation, oracle timing, full-history validity) on 5 configurations, plus mutation analysis. Re-implemented original test misses 3 of 4 mutants that the new suite catches. | `test_no_lookahead.py` |
| F11 | C2 | `run_phase2_validation.py`, `test_data_and_config.py` | Report printed "PASS (run pytest to confirm)" without running; "Configuration-only onboarding: PASS" tested a constructor argument, not configuration. | Runner executes pytest and derives every status; onboarding test uses a YAML-only new symbol. | runner output |
| F12 | C3 | `swing.py` | `confirmed_events` list grew without bound (1,313 after 5,000 bars); no snapshot/restore. | Bounded state (2n+1 bars + counters); JSON snapshot/restore, refusing mismatched symbol/timeframe/n. | `test_state_is_bounded...`, `test_snapshot_restore_midstream...` |
| F13 | C3 | `replay.py` | Throughput timed `list(series)`, one 400-candle run, no regression check; snapshots could be saved but never compared; manifest `visual_review_status` was a free string; `utcnow()` deprecated; docstring cited nonexistent "Section 10". | Timed region isolated, warm-up, median; `check_throughput_regression` (20% rule); `compare_regression_snapshot`; structured sign-off with validation, no auto sign-off. | `test_regression_metrics.py` |
| F14 | C3 | `run_phase2_validation.py` | Metrics: "confirmation lag" hard-coded to n; legs mixed same-type neighbours; not per year. | `metrics.py`: measured lag, opposite-type legs, per symbol/timeframe/year; undefined metrics reported as not computed with reasons. | `test_metrics_...` |
| F15 | C3 | missing | No fixed review set (Section 5), no CSV loader/CLI, no replay symbol/timeframe guard, no reference-label scorer. | Added `review_set.py`, `OHLCSeries.from_csv`, `scripts/swing_cli.py`, replay guard, `reference_eval.py`. | tests |

Not defects: the swing rule itself, payload fields (price, bar_index, timeframe), Section 8 table in YAML (matches exactly, no extra instruments), batch and streaming sharing `step`.

## 4. Spec items that remain unresolved (not invented)
1. Tie handling (strict inequality retained, documented). 2. Minimum leg, suppression, supersession definitions. 3. Largest-bar share definition. 4. Candle timestamp convention (open assumed). 5. Gap policy: resolved by the Addendum below. 6. Absolute throughput budget (only the 20% regression rule exists). 7. Train/validation/holdout proportions. 8. Parameter sweep: not implemented, because minimum leg and "stable region" are undefined and no real data exists.

## 5. EQH/EQL scope
No EQH/EQL code existed in the original; none added. Confirmed swing events carry bar index, price, confirmation time, so a future EQH/EQL consumer can use them. Extra payload fields (`pivot_time`, `confirmation_bar_index`) are additive to the contract.

## 6. Test report (executed)
Full suite (after the gap addendum): **206 passed** (`pytest`, ~17 s).

| Validation | Required | Actual | Status |
|---|---|---|---|
| No-lookahead | 100% pass | 25/25 tests (300 cuts x 5 configs, 100 perturbation cuts x 5, oracle, validity, 4 mutants detected) | Pass (synthetic data) |
| Batch equals streaming | Exact match | 13/13; whole event dicts, order, chunked feed, snapshot/restore mid-stream | Pass (synthetic data) |
| Determinism | Identical output | 10 runs, one hash | Pass |
| Fault injection | Tests pass | 14/14 data tests + detector rejection tests | Pass |
| Normal precision/recall | >= 90% | no reference labels | **Pending** |
| High-vol precision/recall | >= 80% | no reference labels | **Pending** |
| Yearly stability | Stable | per-year metrics implemented; no multi-year real data | **Pending** |
| Throughput | Within budget | ~130k-190k candles/s per stream (20,000 synthetic candles, CPython 3.13, shared VM with about +/-15% run-to-run noise). Baseline re-recorded after the gap change (see `reports/benchmarks/BASELINE_NOTE.md`); no absolute budget defined | Baseline recorded; regression rule needs same-machine baseline |
| Config-only onboarding | Successful | YAML-only new symbol replays; no instrument literal in src | Pass |
| Visual sign-off | Recorded | review set and charts generated; no human sign-off | **Pending** |

I inspected one rendered chart (V75/M5 synthetic, window 100-220): markers on pivot candles, confirmation ticks 2 bars later, one unconfirmed swing hidden. That is not a Section 5 sign-off.

## 7. Change log
Modified: `config/instruments.yaml`, `config.py`, `events.py`, `detectors/base.py`, `detectors/swing.py`, `detectors/__init__.py`, `data/ohlc.py`, `data/validation.py`, `data/__init__.py`, `replay.py`, `visualiser.py`, `__init__.py` (version), `scripts/run_phase2_validation.py`, `README.md`, `requirements.txt` unchanged.
Created: `metrics.py`, `review_set.py`, `reference_eval.py`, `scripts/swing_cli.py`, `pyproject.toml`, `AUDIT_REPORT.md`, tests `helpers.py mutants.py checks.py test_swing_algorithm.py test_no_lookahead.py test_replay_consistency.py test_data_validation.py test_config.py test_visualiser.py test_regression_metrics.py test_reference_eval.py`, regenerated `reports/`.
Removed: `tests/test_swing_basic.py`, `tests/test_data_and_config.py`, old `tests/test_no_lookahead.py` (superseded), old reports/caches. `utils/synthetic.py` unchanged.

## 8. Readiness: **Partially validated** (core implementation Ready for technical review)
No critical or major defect known remains. Phase 2 acceptance is not claimed: reference-label accuracy, yearly stability on real data, parameter sweep, real-versus-baseline comparison (not built; randomised baseline generator pre-exists), and visual sign-off all need external data or a human. Real data must also settle the candle-time convention (F1).

Stopped here. Awaiting your authorization before any next detector.

## 9. Addendum - gap handling (agreed with the project owner, implemented and tested)
Rationale: gaps can be genuine (platform maintenance, no ticks) and affect lower timeframes only; connection handling and backfill belong to the ingestion layer (Phase 1), not the detector.

| Rule | Implementation |
|---|---|
| Never stop for a gap, any size | `gap_policy: bridge` (default). `reset` and strict `error` remain optional. |
| Nothing invented | The next candle is the next bar in sequence; no candle is fabricated. |
| Swings spanning a gap are flagged | Payload adds `spans_gap`, `gap_missing_bars`, `gap_status` only for those swings; other payloads unchanged. |
| Confirmed vs unverified | `Candle.gap_confirmed` is the upstream marker (backfill attempted and failed) -> `confirmed`; otherwise `unverified`. Mixed gaps in a window -> `unverified`. |
| Counted, listed | State counters (bridged, confirmed, unverified, missing bars) and a bounded gap log (1,000 records); in snapshots; logged as warnings. |
| Each timeframe independent | One detector per (symbol, timeframe); tested with an M5 outage and an intact H1 for the same symbol. |
| Late candles | Still rejected ("backfill must complete before delivery"); confirmed events are never revised. |
| Spacing shorter than the timeframe | Rejected as invalid, not treated as a gap. |
| Validation | Gaps are warnings by default; `--strict-gaps` / `allow_gaps=False` makes them errors. |
| Chart | Orange diamond on swings whose window spans a gap. |

New tests (`tests/test_gaps.py`, 14): oracle equality on existing bars, exact flag/missing counts vs an independent derivation, confirmed/unverified status, 100-candle hole, batch = streaming and snapshot/restore across gaps, truncation + validity on randomly gappy data, per-timeframe independence, bounded log, late candle rejected, reset behaviour.

Real data (your six CSVs, 18 Aug - 16 Nov 2026, shipped config, no workaround): all six run. V75 M5: 1 gap (1 missing candle), 2 flagged swings. V100 M3: 2 gaps (3 missing), 1 flagged swing. V75 H1, V100 M15, V50 H1, V50 M15: no gaps. Timestamps verified as bar OPEN time (every complete higher-timeframe bar equals the aggregate of its lower-timeframe bars). All gaps are `unverified` because the CSVs carry no upstream marker.

## 10. Owner approval record (19 Nov 2026)
- **Approval:** the project owner, Ime Edet Nya, approved the Swing Detector on 19 Nov 2026.
- **Visual sign-off:** recorded in `reports/manifests_real/run_manifest_<symbol>_<tf>.json` for all six real streams. Reviewer Ime Edet Nya, decision "accepted", no defects logged. Windows: the last-150-bar charts delivered in `final_results.zip`, plus the owner's own local run of the same CSVs (default last-180-bar charts). The owner did not list further bar ranges.
- **Waived by owner:** "Metrics stable across yearly slices". Recorded as WAIVED, not passed. The supplied data covers about 90 days of one year, so it could not have been assessed.
- **Still not performed (not waived):** reference-labelled precision/recall, defined train/validation/holdout split, parameter sweep, real-versus-baseline comparison, and the open specification points in section 4.
- **Readiness:** owner-approved; evidence status unchanged from "Partially validated" for the items above.
