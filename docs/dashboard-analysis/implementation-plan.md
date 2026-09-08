# Interactive dashboard implementation plan

Status: proposed; no implementation phase has been completed by this document.

This plan implements the [dashboard assessment](live-dashboard-assessment.md). The [verification summary](verification-summary.md) records the baseline: 21 selected tests passed, 12 failed, and default test collection encountered a module-name collision. Those findings are inputs to Phase 1, not accepted release exceptions.

## Scope and delivery order

Build a local, single-user ORB research dashboard using the existing Python pipeline, Streamlit, Plotly, durable run metadata, and one background worker. Deliver interactive historical research first, then current market monitoring. Broker order placement, a browser code editor, multi-user hosting, and EMA strategy integration are outside these five phases.

Dependencies: **Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5**. A historical dashboard can be released after Phase 3; replay and comparison extend it in Phase 4. Phase 5 adds market monitoring and simulated signals. Each phase must meet its acceptance gate before the dependent phase is declared complete.

| Phase | Objectives | Required tests | Acceptance gate |
|---|---|---|---|
| [1. Correctness foundation](phase-01-correctness-foundation.md) | Repair baseline contracts; make config, caching, accounting, and run artifacts reliable. | Offline regression suite; parameter-sensitive cache tests; accounting oracles; malformed config and interrupted writes. | Baseline failures resolved with meaningful assertions; immutable run identity/data references; final equity reconciles. |
| [2. Results explorer](phase-02-results-explorer.md) | Browse completed runs, metrics, interactive charts, trade details, and downloads. | Synthetic UI/service integration; empty/corrupt artifacts; timezone/units; large tables; accessibility smoke checks. | Fresh checkout can demonstrate results offline; displayed values match artifacts; navigation triggers no simulation or downloads from Alpaca. |
| [3. Run control and progress](phase-03-run-control.md) | Submit validated runs, queue work, show durable progress, cancel, and recover from failures. | Duplicate/racing submissions; worker death; browser reload; cancellation boundaries; partial publication and secret redaction. | One active worker; submission is idempotent; lifecycle remains consistent across reload/crash; UI stays responsive. |
| [4. Replay, comparison, and code trace](phase-04-replay-comparison-code-trace.md) | Replay actual engine decisions, compare experiments, and inspect recorded source provenance. | Temporal leakage perturbations; pause/seek/step determinism; unequal calendars; dirty source; missing trace versions. | Replay never exposes future state; end state matches the completed run; comparisons identify incompatible inputs. |
| [5. Streaming market monitor](phase-05-streaming-market-monitor.md) | Consume market bars, recover gaps, maintain incremental state, and show feed freshness. | Fake-stream reconnect/correction tests; calendar boundaries; delayed bars; restart recovery; historical/streaming parity. | No duplicated decisions; degraded/stale states are visible; finalized-bar decisions match historical execution under the same policy. |

## Shared implementation contracts

- A `RunRequest` includes both immutable strategy/data/execution config and run options such as date bounds, refresh, and logging. UI, CLI, and worker share validation.
- A server-generated run ID identifies a single attempt. A request token deduplicates submission retries; a deliberate rerun receives a new token and run ID.
- Each manifest records effective config, requested options, source fingerprint, immutable dataset identity, schema versions, timestamps, status, and artifact checksums. A completed run cannot silently change.
- Times are timezone-aware. Distinguish bar start, bar availability, processing time, and display timezone. Replay ordering follows information availability.
- Simulations and metric formulas remain in Python services. UI operations read results or submit explicit commands; chart interaction must not recalculate strategy logic.
- Processed cache validity covers every input affecting content. Published artifacts are read only after validation and atomic publication; incomplete runs have explicit states.
- Tests default to offline deterministic fixtures. External-data integration is opt-in and does not replace a deterministic contract test.

## Test approach and release evidence

Each phase file defines traceable objective, test, and acceptance IDs. Implement focused unit tests for domain rules, integration tests for actual service/filesystem boundaries, and UI tests for important user workflows. Use fixed seeds, fake clocks, temporary storage, injected data adapters, and injected failures where relevant. Do not use arbitrary sleeps to prove correctness.

Use independent expected values for financial assertions. Deriving expected outputs through the same implementation under test does not establish correctness. Compare stored full-precision calculations with an explicitly documented tolerance; compare displayed amounts at their declared rounding precision.

For each phase, save a completion record under `docs/dashboard-analysis/validation/phase-NN.md` when it is implemented. Record commit/source fingerprint, environment/dependency versions, exact commands, test outcomes, relevant screenshots, and unresolved limitations. This directory and these completion records are future deliverables, not existing evidence.

Performance numbers in the detailed phases are proposed local acceptance budgets. Record the machine and fixture size. Change a budget only through an explicit documented design decision supported by measurement, not by silently lowering a failing expectation.

## Completion rules

1. All phase acceptance criteria have evidence and their mapped tests pass.
2. All earlier-phase regression tests still pass; necessary test-contract changes have a recorded rationale.
3. CLI behavior remains covered, and no credentials are required for the normal suite or synthetic demo.
4. User-facing errors describe a recovery action; failed or incomplete data is never silently shown as a successful result.
5. Documentation contains setup, operation, limitations, and recovery instructions appropriate to the delivered features.

Do not estimate later-phase dates until Phase 1 establishes a dependable baseline. Sequence work within each phase using its implementation steps; the file list is guidance and may be adjusted to fit the final code structure.
