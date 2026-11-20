# Algorithmic Trading System — Phase 2: Swing Detector

**Governing documents**
- `Trading_System_Build_Order_v3_4.docx` (v3.4)
- Implementation prompt: `writing-block.md`

**Scope (strict)**  
Only the **Swing Detector** for instruments **V75, V100, V50**.  
EQH/EQL, Trend-state, BOS, Displacement, CHoCH, FVG, Order Block, Liquidity Sweep, POI Lifecycle, IFVG, Premium/Discount, Cost Model, Setup Evaluator, strategies, execution and risk management are **out of scope**.

---

## 1. Implementation decisions (not Research Spec requirements)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python 3.11+ | Explicitly requested by the implementation prompt role |
| Numeric / data | numpy + pandas | Columnar, efficient, standard for quant work |
| Config | YAML (`config/instruments.yaml`) | Human-readable, no code change needed for parameter edits |
| Charting | matplotlib | Lightweight, offline, sufficient for OHLC + markers |
| Testing | pytest | Standard, supports the required test matrix |
| Event schema | Immutable dataclass + JSON serialisation | Matches shared contract in Build Order §1 / §9 |
| Fractal equality | Strict `>` / `<` | Research Spec not supplied; equal-high/low handling flagged as **unresolved** |
| Minimum leg | Not applied | Not defined in supplied documents → unresolved |
| Suppression / supersession | Not applied | Not defined → unresolved |

---

## 2. Project layout

```
swing_detector_project/
├── config/
│   └── instruments.yaml          # V75/V100/V50 HTF/LTF + swing n
├── src/trading_system/
│   ├── events.py                 # Shared event schema
│   ├── config.py                 # Config load + hash
│   ├── data/
│   │   ├── ohlc.py               # Candle + OHLCSeries
│   │   └── validation.py         # Strict validation, no silent drops
│   ├── detectors/
│   │   ├── base.py               # Step-function interface + registry
│   │   └── swing.py              # Non-repainting n-bar fractal
│   ├── replay.py                 # Batch == streaming by construction
│   ├── visualiser.py             # Confirmed swings only
│   └── utils/synthetic.py        # Clearly labelled synthetic data
├── tests/
│   ├── test_swing_basic.py
│   ├── test_no_lookahead.py
│   └── test_data_and_config.py
├── scripts/
│   └── run_phase2_validation.py
├── reports/                      # Manifests, snapshots, charts
├── requirements.txt
└── README.md
```

---

## 3. Installation

```bash
cd swing_detector_project
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

(Optional editable install)

```bash
pip install -e .
```

---

## 4. Configuration

`config/instruments.yaml` (from Build Order §8 / writing-block starting table):

| Instrument | HTF | HTF Swing n | LTF | LTF Swing n |
|------------|-----|-------------|-----|-------------|
| V75        | H1  | 3           | M5  | 2           |
| V100       | M15 | 3           | M3  | 2           |
| V50        | H1  | 3           | M15 | 2           |

Change values in the YAML; detector source code does not hard-code them.

---

## 5. How the Swing Detector works

- **Input**: own state + one closed candle + parameter `n`.
- **Rule**: classic n-bar fractal with **strict** inequality.
  - Pivot at index `i` is a swing high when  
    `high[i] > high[i-k]` and `high[i] > high[i+k]` for all `k = 1 … n`.
  - Analogous for swing low with lows.
- **Confirmation**: the event is emitted only when the candle at `i + n` closes.  
  Confirmation time = close time of that candle.
- **No repaint**: once emitted, an event is never altered or withdrawn.
- **No future data**: the detector never inspects candles beyond the current processing bar.
- **State isolation**: one detector instance per (symbol, timeframe).

Events use the shared schema:

```json
{
  "type": "SWING_HIGH" | "SWING_LOW",
  "symbol": "V75",
  "timeframe": "M5",
  "confirmation_time": "ISO-8601",
  "payload": {
    "price": 1234.5,
    "bar_index": 42,
    "timeframe": "M5"
  }
}
```

---

## 6. Running tests

```bash
cd swing_detector_project
pytest tests/ -v
```

Key guarantees exercised by the suite:

- No-lookahead (200 random cut points)
- Batch output ≡ streaming output
- Determinism across repeated runs
- Fault injection (duplicates, out-of-order, invalid OHLC)
- State isolation between symbols
- Configuration-only addition of a new test symbol

---

## 7. Validation report & charts

```bash
python scripts/run_phase2_validation.py
```

Outputs under `reports/`:

- `phase2_validation_report.md` — status table + outstanding issues
- `run_manifest_*.json` — full reproducibility manifest
- `snapshots/` — regression snapshots
- `charts/` — OHLC + confirmed swing overlays (synthetic data)

---

## 8. Unresolved specification decisions

Because the **Research Specification (SMC for Deriv Volatility Indices)** was not present in the project storage:

1. Equal-high / equal-low tie handling inside the fractal.
2. Ambiguous or overlapping swing candidates.
3. Any minimum-leg calculation.
4. Suppression or supersession behaviour.

These are **flagged**, not silently invented. The implementation uses strict inequality and emits every independent fractal that satisfies the rule.

---

## 9. EQH/EQL separation (explicit)

The Build Order Phase 2 text mentions EQH/EQL liquidity tagging together with the swing detector.  
The Detector Interface Catalogue and the implementation prompt both place EQH/EQL as a **separate detector** that depends on Swing.

**This delivery implements Swing only.**  
Confirmed swing events are available for a future EQH/EQL detector.  
No clustering, no liquidity tagging, no EQH/EQL logic is present.

---

## 10. Acceptance status (honest)

| Criterion | Status |
|-----------|--------|
| Core non-repainting n-bar fractal | Implemented |
| Shared event schema + step interface | Implemented |
| Batch ≡ streaming, determinism, no-lookahead | Automated tests present (run pytest) |
| Config-driven parameters, state isolation | Implemented |
| Chart visualiser (confirmed only) | Implemented |
| Regression snapshot + run manifest | Implemented |
| Throughput baseline | Recorded on synthetic data |
| Reference precision/recall ≥ 90 % / 80 % | **PENDING** — no hand-labelled data |
| Real-market characterisation & yearly stability | **PENDING** — no real OHLC supplied |
| Human visual sign-off | **PENDING** |
| Research Spec edge-case rules | **PENDING** — Spec not supplied |

**Recommendation:** The Swing Detector foundation and core logic are ready for review on synthetic data.  
Full Phase 2 exit criteria cannot be claimed until real data, reference labels, and the Research Specification decisions are provided.

**STOP — do not proceed to Detector 2 (EQH/EQL) or any later phase without explicit authorisation.**
