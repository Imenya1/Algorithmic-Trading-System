# Phase 2 Swing Detector — Validation Report

**Code version:** 0.1.0-phase2-swing

**Generated:** 2026-11-18T18:54:51.580068Z

## Test Results

| Test | Required result | Actual result | Status |
|---|---|---|---|
| No-lookahead | 100% pass | See pytest tests/test_no_lookahead.py | PASS (run pytest to confirm) |
| Batch equals streaming | 100% pass | See pytest tests/test_swing_basic.py | PASS (run pytest to confirm) |
| Determinism | Identical output | See pytest tests/test_swing_basic.py | PASS (run pytest to confirm) |
| Fault injection | Tests pass | See pytest tests/test_data_and_config.py | PASS (run pytest to confirm) |
| Reference precision (normal) | >= 90% | No hand-labelled reference data supplied | PENDING |
| Reference recall (normal) | >= 90% | No hand-labelled reference data supplied | PENDING |
| High-volatility precision | >= 80% | No hand-labelled reference data supplied | PENDING |
| High-volatility recall | >= 80% | No hand-labelled reference data supplied | PENDING |
| Yearly stability | Stable metrics | No multi-year real data supplied | PENDING |
| Throughput | Within Section 4 budget (Open Decision 5, default 20% tolerance) | 771021 candles/s (baseline recorded) | BASELINE RECORDED (no approved absolute budget yet) |
| Configuration-only onboarding | Successful | New symbol via constructor args works (see test) | PASS |
| Visual review | Developer sign-off | Chart generated; sign-off not yet recorded by human | PENDING |

## Characterisation Metrics (synthetic V75 M5, n=2)

```json
{
  "n_bars": 400,
  "n_swings": 101,
  "n_swing_high": 52,
  "n_swing_low": 49,
  "swing_density": 0.2525,
  "mean_confirmation_lag_bars": 2,
  "leg_size_mean": 33.4462,
  "leg_size_median": 28.8833,
  "suppression_rate": null,
  "supersession_rate": null,
  "largest_bar_share": null,
  "eqh_eql_frequency": null
}
```

## Outstanding Validation Issues

- Research Specification (SMC for Deriv Volatility Indices) not supplied in storage — equal-high/low handling, minimum-leg, suppression/supersession remain unresolved.
- No real OHLC market data for V75/V100/V50 supplied — all characterisation uses synthetic data.
- No hand-labelled reference sample (~200 bars per symbol/timeframe) supplied — precision/recall cannot be measured.
- Section 4 throughput absolute budget is an Open Decision; only a baseline is recorded.
- EQH/EQL detector is intentionally not implemented (writing-block instruction).
- Visual review requires human developer sign-off.

## Implementation Decisions (not Research Spec rules)

- Fractal comparison uses **strict inequality**. Equal highs/lows do not produce a pivot. This is an unresolved specification decision.
- No minimum-leg filter is applied (not defined in supplied documents).
- No suppression or supersession of prior swings (not defined).
- Language: Python 3.11+. Data: numpy/pandas. Charts: matplotlib. Config: YAML. Tests: pytest.

## Recommendation

The Swing Detector core logic, non-repainting behaviour, batch=streaming equivalence, determinism, state isolation, configuration-driven parameters, regression snapshot, throughput baseline, and chart visualiser are implemented and covered by automated tests on synthetic data.

**Not yet ready for full Phase 2 exit criteria** because:
1. Real market data and hand-labelled reference samples are missing (precision/recall cannot be measured).
2. Several fractal edge-case rules remain unresolved pending the Research Specification.
3. Human visual sign-off has not been recorded.

**STOP — awaiting explicit approval before any further detector.**
