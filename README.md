# Algorithmic Trading System

**Project:** Algorithmic Trading System — Deriv Synthetic Indices  
**Governing Build Order:** `Trading_System_Build_Order_v3_4.docx`  
**Project Authority:** Project Director  
**Development Status:** Architecture Review and Controlled Development Preparation  
**Implementation Authorization:** Requires explicit Project Director approval

---

## 1. Project Overview

This project aims to develop a modular, deterministic, high-performance algorithmic trading system for selected Deriv synthetic indices.

The system will identify and evaluate market structure, liquidity events, price delivery patterns, and strategy conditions using a component-based architecture.

The development process prioritizes:

- Correctness over speed of delivery.
- Deterministic and reproducible analysis.
- No-lookahead detection.
- Incremental processing.
- Efficient memory and CPU utilization.
- Independent component validation.
- Clear component dependencies.
- Evidence-based strategy evaluation.
- Controlled implementation and integration.

The system must be developed according to the governing Build Order and the applicable Research Specification.

No undocumented trading rules or architectural assumptions may be introduced without review and approval.

---

## 2. Project Authority and Governance

The Project Director has final authority over all project decisions.

The Project Director exclusively approves:

- System architecture.
- Component specifications.
- Component dependencies.
- Trading and market-structure definitions.
- Technology-stack decisions.
- Build Order amendments.
- Development priorities.
- Acceptance criteria.
- Changes to existing components.
- Integration of completed components.
- Progression between development phases.

### Mandatory rule

**NO IMPLEMENTATION OR PROJECT MODIFICATION WITHOUT EXPLICIT PROJECT DIRECTOR APPROVAL.**

AI agents and developers may research, inspect, analyse, challenge, recommend, and prepare proposed solutions.

They must not independently authorize implementation or change approved requirements.

When requirements are ambiguous or contradictory, work must stop at the affected decision and be referred to the Project Director.

---

## 3. Approved Market Scope

The current project scope covers three Deriv synthetic indices.

| Instrument | Higher Timeframe | Lower Timeframe |
|---|---|---|
| V75 | H1 | M5 |
| V100 | M15 | M3 |
| V50 | H1 | M15 |

### Approved strategy combinations

**V75**
1. Sweep + CHoCH + FVG
2. OB + FVG
3. IFVG
4. BOS Continuation

**V100**
1. Sweep + CHoCH + FVG
2. OB + FVG
3. IFVG

**V50**
1. EQH/EQL Sweep
2. OB + FVG
3. BOS Continuation

Boom, Crash, and other instruments or strategy combinations are outside the approved v3.4 scope unless explicitly authorized by the Project Director.

---

## 4. Governing Documentation

The following documents govern project development:

1. `Trading_System_Build_Order_v3_4.docx`
2. The authoritative Research Specification for SMC/ICT definitions, instrument parameters, strategy rules, and market behaviour.
3. Approved amendments and supplementary specifications.
4. Approved component specifications and decision records.

The governing documents must be preserved.

AI agents must not silently replace, reinterpret, or amend their requirements.

Where documents conflict, the conflict must be documented and referred to the Project Director.

---

## 5. Development Architecture

The system follows a modular, component-first development approach.

Each component must have:

- A clearly defined responsibility.
- Explicit input and output contracts.
- Declared dependencies.
- Defined state-management requirements.
- Deterministic behaviour.
- Unit and integration tests where appropriate.
- Performance considerations.
- Documented acceptance criteria.

Components must be independently developed and validated before integration.

The component inventory is a development planning reference, not automatic authorization to implement every listed component.

### Core engineering principles

1. Past-only detection.
2. No future-candle information in detection decisions.
3. Event confirmation must occur before downstream consumption.
4. Batch and streaming execution must produce equivalent results.
5. Incremental processing must use the same underlying step logic.
6. State must be maintained independently per symbol and timeframe.
7. Replay must be deterministic.
8. Snapshot and restore must preserve reproducibility.
9. Component caching must support selective invalidation.
10. Performance optimization must be based on measurement.

---

## 6. Development Phases

The project follows the governing Build Order's staged development process.

| Phase | Description |
|---|---|
| Phase 0 | Foundations and architecture |
| Phase 1 | Data foundation and ingestion |
| Phase 2 | Swing detection and tooling |
| Phase 3 | Market structure |
| Phase 4 | Points of Interest and liquidity |
| Phase 5 | Strategy logic |
| Phase 6 | Simulated execution and paper trading |
| Phase 7 | Live operations |

Progression between phases requires satisfaction of the applicable acceptance criteria and Project Director authorization.

Passing unit tests alone does not automatically authorize phase completion.

---

## 7. Core Technical Requirements

### Event contract

The shared event contract is:

`type, symbol, timeframe, confirmation_time, payload`

Any proposed changes must be reviewed and approved.

### Deterministic processing

The system must support:

- Deterministic historical replay.
- Incremental streaming.
- Batch/streaming equivalence.
- State snapshot and restoration.
- Reproducible experiment metadata.

### No-lookahead protection

A component must never consume an event before its confirmation time.

Future candles must not influence historical detection decisions.

### Data integrity

The system must:

- Preserve original historical datasets.
- Validate OHLC integrity.
- Detect duplicates and missing candles.
- Normalize timestamps consistently.
- Preserve dataset hashes.
- Avoid silently fabricating or filling missing market data.

### Performance

The system must prioritize efficient in-memory processing, minimize unnecessary historical rescanning, and avoid blocking disk/database writes on the decision path.

---

## 8. AI Engineering Team

The project uses multiple AI agents with separate responsibilities.

### ChatGPT — Architecture and Independent Review

Responsibilities:

- Review architecture and component specifications.
- Challenge assumptions and technical decisions.
- Examine dependency relationships.
- Review implementation proposals.
- Design validation strategies.
- Identify correctness and performance risks.
- Provide independent technical assessments.

ChatGPT must not assume implementation authority.

### Claude — Research and Technical Review

Responsibilities:

- Conduct independent technical research.
- Challenge project assumptions.
- Examine governing documentation.
- Investigate alternative architectures.
- Review component implementations.
- Identify specification conflicts.
- Produce technical research and review reports.

Claude must distinguish documented requirements from recommendations.

### Qwen Cloud — Proposed Primary Implementation Agent

Responsibilities, when authorized:

- Inspect the project repository.
- Implement approved component specifications.
- Create and maintain tests.
- Execute authorized test suites.
- Debug implementation defects.
- Prepare pull requests.
- Document changes and test evidence.

Qwen must not independently change approved architecture, specifications, or component responsibilities.

### Project Director

The Project Director:

- Defines priorities.
- Approves specifications.
- Resolves conflicting recommendations.
- Authorizes implementation.
- Reviews proposed changes.
- Accepts or rejects completed components.
- Controls integration and phase progression.

---

## 9. GitHub Collaboration Workflow

GitHub serves as the central project record and collaboration point.

### Repository principles

- The repository should remain private.
- The `main` branch must be protected.
- AI agents must use separate working branches.
- No automatic merging.
- No unauthorized changes to governing documents.
- Pull requests must contain clear descriptions and evidence.
- All important decisions must be recorded.

### Proposed branch naming

- `main` — approved project baseline.
- `research/...` — research and investigation.
- `review/...` — independent technical reviews.
- `feature/...` — authorized implementation.
- `fix/...` — authorized defect corrections.
- `docs/...` — authorized documentation changes.

### Pull request requirements

Every implementation pull request must include:

1. Objective.
2. Approved component specification reference.
3. Files changed.
4. Design explanation.
5. Tests added or modified.
6. Test execution results.
7. Performance impact, where relevant.
8. Known limitations.
9. Deviations from specification.
10. Outstanding decisions.

### Approval requirements

A pull request must not be merged into the protected `main` branch without the required human approval.

AI review comments are advisory, not approval authority.

---

## 10. Testing and Validation

Testing must cover more than ordinary functional correctness.

Required validation areas include:

- Unit testing.
- Integration testing.
- No-lookahead testing.
- Deterministic replay.
- Streaming-versus-batch equivalence.
- Snapshot/restore equivalence.
- Data-gap handling.
- Duplicate event prevention.
- State consistency.
- Performance benchmarking.
- Historical reference validation.

Strategy evaluation must use chronological training, validation, and holdout separation.

The final 20% holdout must remain locked until the permitted final evaluation under the governing Build Order.

No tuning on the locked holdout is permitted.

---

## 11. Security and Access Control

All agents must operate with the minimum permissions necessary.

Recommended access principles:

- ChatGPT: read-only repository access for review.
- Claude: read-only repository access initially; review contributions through controlled GitHub workflows.
- Qwen Cloud: only the permissions necessary for its approved tasks.
- Project Director: repository administration and final approval authority.

Use GitHub authentication and authorization controls.

Never commit:

- API keys.
- Access tokens.
- Passwords.
- Private credentials.
- `.env` files containing secrets.
- Sensitive account information.

Use environment variables or secure secret management.

---

## 12. Project Decision Register

Important architectural and development decisions must be recorded.

Each decision must include:

- Decision ID.
- Description.
- Relevant governing requirement.
- Available alternatives.
- Evidence.
- Recommendation.
- Consequences.
- Dependencies.
- Approval status.

Possible statuses:

- OPEN — Awaiting Project Director decision.
- APPROVED — Explicitly approved by the Project Director.
- REJECTED — Rejected by the Project Director.
- DEFERRED — Postponed by the Project Director.
- IMPLEMENTED — Approved decision has been implemented and verified.

No recommendation may be marked APPROVED without explicit Director authorization.

---

## 13. Current Development Status

The project is currently subject to an independent architectural and development-readiness challenge.

The previous structural detection research identified unresolved issues involving:

- BOS and CHoCH dependencies.
- Trend State ownership.
- Internal versus external structure.
- Displacement definition and timing.
- Structural level lifecycle.
- Dealing Range dependencies.
- Multi-timeframe synchronization.
- Event schema.
- Warm-up and gap recovery.
- Phase 3 validation.
- Swing Detector parameter discrepancies.
- Existing implementation verification.
- Technology-stack selection.
- Build Order amendments.

These matters must be resolved through evidence-backed research and Project Director decisions.

**The previous recommendations must not be treated as approved architecture.**

No new implementation is authorized by this README.

---

## 14. Final Development Rule

The objective is not merely to build a trading system that produces signals.

The objective is to build a system whose behaviour can be explained, tested, reproduced, measured, challenged, and independently verified.

Correctness, reproducibility, transparency, and controlled development take priority over implementation speed.

**The Project Director retains final authority over every development decision.**
