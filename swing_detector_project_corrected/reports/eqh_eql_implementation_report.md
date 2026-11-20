# EQH/EQL Detector — Implementation Report (continued)

**Date:** 2026-11-19  
**Scope:** Detector 2 only (Equal Highs / Equal Lows / EQ_CLUSTER)  
**Governing documents used:**
- Trading System Build Order v3.4 (Table 19 instrument parameters, Table 20 detector catalogue)
- Existing Swing Detector implementation

---

## 1. Rules Implemented from the Documents

| Rule | Source | Implementation |
|------|--------|----------------|
| Tolerance = max(fixed points, atr_mult × ATR(14)) | Table 19 | Config-driven per instrument |
| V75: max(716, 0.10 ATR) | Table 19 | `eqh_eql_tolerance_points: 716`, `atr_mult: 0.10` |
| V100: max(12, 0.10 ATR) | Table 19 | `12` / `0.10` |
| V50: max(115, 0.12 ATR) | Table 19 | `115` / `0.12` |
| ATR period = 14 | Table 20 | Wilder smoothing |
| Min touches = 2 | Table 20 (“confirmed at the second swing”) | `min_touches: 2` |
| Confirmation time = confirmation of the newest (second+) swing | Table 20 | Event `confirmation_time` |
| Output event `EQ_CLUSTER` with side, level, inner_edge, touch_count, age | Table 20 | Payload fields present |
| Inputs: swing events + ATR(14) + tolerance | Table 20 | `step` for ATR, `on_swing_events` for swings |
| Causal, no look-ahead | Build Order §1 / §9 | Enforced |
| Config-driven, no hard-coded instrument values | Build Order §4 / §8 | YAML + `get_eqh_eql_params` |

## 2. Unresolved Items (explicitly isolated)

These are **not** present in the Build Order or any Research Specification available in the project:

| Item | Current behaviour | Decision needed |
|------|-------------------|-----------------|
| Multi-touch clustering algorithm beyond pairwise check of newest vs priors | Newest + all prior same-side swings within tolerance | Exact grouping rule |
| Minimum spacing between constituent swings | None applied | Bars or time distance? |
| Definition of `level` | Mean of constituent prices | First / mean / median / other? |
| Definition of `inner_edge` | min(prices) for highs, max for lows | Confirm or replace |
| Definition of `age` | bar_index(last) − bar_index(first) | Time delta? bars? |
| Role of `spread` in equality test | Not used | Include or ignore? |
| ATR method | Wilder (standard) | SMA / other? |
| Behaviour while ATR is warming up | No clusters emitted | Acceptable? |

No fabricated clusters are emitted when rules are not met.

## 3. Changed / Created Files

**Modified**
- `config/instruments.yaml` — instrument-specific EQH/EQL tolerances + detector section
- `src/trading_system/config.py` — validation + `get_eqh_eql_params`
- `src/trading_system/events.py` — `EQ_CLUSTER` constant
- `src/trading_system/detectors/eqh_eql.py` — full detection logic
- `src/trading_system/detectors/__init__.py` — exports
- `src/trading_system/visualiser.py` — horizontal EQ_CLUSTER level lines
- `tests/test_eqh_eql.py` — comprehensive suite (25 tests)
- `tests/test_config.py` — scalability test updated for new required keys

**Created / updated report**
- `reports/eqh_eql_implementation_report.md`

## 4. Test Results

```
pytest tests/test_eqh_eql.py  →  25 passed
pytest tests/                 → 232 passed (full suite, zero regressions)
```

Covered categories:
- EQH detection (within / outside / exact boundary)
- EQL detection
- V75 / V100 / V50 tolerance configs
- ATR warm-up (no clusters until ATR defined)
- Min touch count = 2
- Causal confirmation time
- Out-of-order / wrong-symbol rejection
- Batch ≡ streaming
- Determinism
- Snapshot / restore
- State isolation
- Invalid / non-swing events ignored
- Multi-touch (3+)

## 5. Visualization

`visualiser.py` now draws confirmed `EQ_CLUSTER` levels as dashed horizontal lines (orange for highs, blue for lows). Levels appear only when the cluster’s confirmation time is at or before the end of the displayed window / `as_of`. No look-ahead.

## 6. Instructions to Run

```bash
cd /home/workdir/artifacts/project/swing_detector_project_corrected
export PYTHONPATH=src
pip install -r requirements.txt   # if needed

pytest tests/test_eqh_eql.py -v
pytest tests/ -q                  # full regression
```

## 7. Readiness Assessment

| Criterion | Status |
|-----------|--------|
| Rules from Build Order Table 19/20 implemented | Yes |
| Tolerances configuration-driven | Yes |
| Causal, snapshot/restore, isolation, determinism | Yes |
| No invented trading rules hidden | Yes |
| Unresolved items explicitly listed | Yes |
| Full test suite green | Yes |
| Ready for Liquidity Sweep or later detectors | **No** — stop per Completion Gate |

**STOP.** Awaiting explicit approval before any subsequent detector.
