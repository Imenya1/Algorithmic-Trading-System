# EQH/EQL Detector (Detector 2) - Audit and Correction Report

Audited: Grok's implementation (pre-audit copy kept in `../BACKUP_before_audit/`).
Governing source: `Trading_System_Build_Order_v3.4` (Section 8 parameters, Section 9 catalogue).
The Research Specification "SMC for Deriv Volatility Indices" is **not in the project**, so nothing was checked against it.
Note: Grok's code and report cite "Table 19 / Table 20". The Build Order has no tables with those numbers; the content is in Sections 8 and 9. References were corrected.

## A. Executive summary
**Incorrect / incomplete.** Grok's baseline test suite passed (232/232, reproduced), but the detector was unusable on the supplied data:
the tolerance was compared in the wrong units, so 99.9% of V50/M15 swings were reported as "equal"; the detector was never wired into the replay pipeline;
ATR look-ahead was possible; duplicate swings produced false clusters; and several state-integrity holes existed. All were corrected except the items in section G.

## B. Defects found (each reproduced against Grok's original code with `/tmp/defects.py`-style probes)

| # | Defect | Location (original) | Evidence | Spec/architecture violated | Correction |
|---|---|---|---|---|---|
| D1 | **Units.** Tolerance floor is given in *points* (716/12/115) but was compared to price differences and ATR in *price units* | `eqh_eql.py` `current_tolerance` | V50/M15: 2241 swings -> 2239 EQ events (99.9%); tolerance 115 on a ~95 price | Section 8 ("Sweep/EQH-EQL tolerance (points)") | New required per-instrument `point_size`; floor = points x point_size. See G1 |
| D2 | **Duplicate swing forms a cluster with itself** (touch_count 2 from one swing) | `on_swing_events` | Same event delivered twice -> 1 false cluster | Section 9 (confirmed at *second* swing); no duplicate/contradictory detections | Idempotent dedupe by (type, bar_index); conflicting duplicate rejected |
| D3 | **ATR look-ahead / wrong-ATR.** Any swing was judged with whatever ATR the detector currently held; feeding candles first then swings used the *final* ATR | `on_swing_events` vs `step` | Old swing evaluated with ATR 9484 instead of ~100 | Section 1 past-only detection; confirmation timing | Swing confirmation time must equal close of last processed candle (enforced); new single-path `step_with_swings`; pipeline helpers in `replay.py` |
| D4 | **Non-atomic batch.** A bad event mid-batch left earlier events applied and their clusters lost | `on_swing_events` | KeyError with 2 swings retained, 0 clusters returned | State integrity / determinism | Validate whole batch first, then apply |
| D5 | **Corrupt entry kept.** Swing appended to state *before* its payload was read | `on_swing_events` | Missing `price` -> KeyError, entry retained | Same | Payload validated (finite price, int bar_index) before any mutation |
| D6 | **NaN candle accepted**, ATR became NaN, tolerance silently fell back to the fixed floor | `step` | ATR=nan, tolerance=716 | Data discipline | Candle validated with the same `candle_issues` the swing detector uses |
| D7 | Same-timestamp candle with different prices silently ignored | `step` | "silently ignored" | Matches swing detector rule | Conflicting duplicate raises; exact repeat idempotent |
| D8 | `restore({symbol, timeframe})` accepted and wiped state | `restore` | atr=None, time=None | Snapshot/restore correctness | Complete-key and version check; corrupt snapshot rejected, state untouched |
| D9 | Restore into a detector with different tolerance parameters accepted | `restore` | accepted | Config consumed consistently | Parameter equality enforced |
| D10 | **Not integrated.** `DetectorBank`, `replay_series`, the CLI and charts only ran swings; EQ was a class that nothing executed | `replay.py`, `swing_cli.py` | grep: no caller | Phase integration requirement | `create_eqh_eql_detector`, `stream_/replay_swing_eqh_eql`, CLI `replay-eq` and `chart-eq` |
| D11 | **Chart look-ahead.** `axhline` drew each level across the *whole* chart, including bars before the cluster was confirmed; legend showed only EQH | `visualiser.py` | code + image | "Visualization does not introduce future information" | Levels drawn from the confirmation candle rightwards; EQH/EQL separate legend entries; mismatching price/symbol raises |
| D12 | Config accepted `atr_period: 14.5` (silently truncated), unknown keys; non-finite tolerances | `config.py` | code | No silent fallbacks | Strict validation |
| D13 | Tests did not prove behaviour: timing never checked, duplicates/units/atomicity/real swing detector never exercised; "determinism" and "batch == streaming" fed the same hand-made list | `tests/test_eqh_eql.py` | passed while D1-D9 present | Audit rule: do not trust test names | 70 new EQ tests + 7 chart/CLI tests; independent oracle; mutation check (14/14 mutants killed) |

## C. Files modified
`src/trading_system/detectors/eqh_eql.py` (rewritten), `config.py`, `replay.py`, `visualiser.py`, `scripts/swing_cli.py`, `config/instruments.yaml`, `tests/test_eqh_eql.py` (rewritten), `tests/test_config.py` (new required key), new `tests/test_visualiser_eq.py`, this report.
**Not touched:** `swing.py`, `events.py`, `base.py`, data validation, other tests. No other detector was started.

## D. Specification compliance
Correct now: tolerance formula and per-instrument values (V75 716/0.10, V100 12/0.10, V50 115/0.12), ATR(14), min touches 2, confirmation = second swing's confirmation, EQ_CLUSTER with side/level/inner_edge/touch_count/age, swing inputs only (no re-detection), per-(symbol,timeframe) state, config-driven.
Additive payload fields (not in Section 9): `tolerance_used`, `tolerance_used_points`, `constituent_bar_indices/prices`, `confirmation_bar_index`.

## E. Tests
| Scope | Result |
|---|---|
| Full suite, before changes (Grok) | 232 passed |
| Full suite, after changes | **284 passed, 0 failed** |
| `tests/test_eqh_eql.py` | 70 passed (config/units/tolerance/boundary, swing-input discipline, causality incl. truncation and oracle comparison, batch==stream, determinism, snapshot/restore mid-stream with JSON round trip, isolation across 4 instrument/timeframe pairs, candle reliability, real-swing-detector integration, supplied data) |
| `tests/test_visualiser_eq.py` | 7 passed |
| Swing/regression files (no_lookahead, swing_algorithm, replay_consistency, gaps, regression_metrics) | 161 passed |
| Mutation check on the EQ tests | 14/14 injected faults detected |
| Not run | `run_all.ps1` (PowerShell; Linux sandbox) |

## F. Visualization
EQH (orange) and EQL (blue) levels come only from detector events and start at the confirmation candle. I viewed the V50/M15 chart. V75/M5 and V100/M3 charts were rendered (in `reports/charts/`) but I did not inspect them. **Developer visual sign-off (Build Order Section 5) is a human step and is NOT recorded.**

## G. Unresolved items needing the owner
1. **`point_size` is provisional.** Build Order Section 8 gives tolerance in points but no point size (Open Decision 8; V75 tick size explicitly unresolved). I set V75 0.01, V100 0.01, V50 0.0001, inferred from proportions of Section 8's spread/zone-width/Stops Level values to ATR in the supplied data. **Confirm from the broker terminal.** Impact is modest here because the ATR term dominates (floor never bound in the supplied data); changing point_size x10 raises EQ counts ~25-30%, /10 changes nothing.
2. **No lookback / expiry rule is defined, and it dominates results.** A new swing pairs with *any* prior same-side swing among the last `max_swings_kept` (500) within tolerance. With corrected units, 39-75% of swings still produce an EQ_CLUSTER depending on symbol/timeframe (H1: 39-46%; M3/M5/M15: 68-75%; e.g. V75/M5 4985 of 6783), median partner age ~1,650 bars, 75% of partners older than 500 bars. EQ events vs `max_swings_kept`: 20 -> 1948, 50 -> 2720, 100 -> 3354, 500 -> 4985 (V75/M5). `max_swings_kept=500` is therefore an undocumented detection parameter. I did not invent a maximum age or an "unswept" requirement. Needs a decision (likely belongs with the Phase 4 POI lifecycle manager or the Research Specification).
3. Multi-touch grouping (newest-anchored, non-transitive; one event per new swing, no cluster id), `level` (mean), `inner_edge`, `age` definitions, spread's role in equality (Section 9 lists spread as an input; no rule given), ATR smoothing (Wilder) and warm-up behaviour are implementation choices, documented in the module docstring.
4. The Research Specification was not available for cross-checking.

## H. Final verdict
**APPROVED WITH LIMITATIONS.** The implementation is now correct against what the Build Order defines, causal, atomic, snapshot-safe, integrated, and tested against an independent oracle. It is not approved as a *useful* detector until G1 (broker point size) and especially G2 (lookback/expiry) are decided, and the visual sign-off is recorded by a human.

Stopped here. Detector 3 (Trend State & BOS) has not been started.
