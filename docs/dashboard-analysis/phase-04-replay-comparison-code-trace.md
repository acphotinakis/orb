# Phase 4 — Replay, comparison, and code trace

[Master plan](implementation-plan.md) · Previous: [Run control](phase-03-run-control.md) · Next: [Streaming monitor](phase-05-streaming-market-monitor.md)

Status: **accepted** — all P4-A1 through P4-A7 satisfied; 156 tests pass; Phase 5 entry gate met. See [acceptance validation](validation/phase-04.md).

## Objectives

### P4-O1: Capture decisions from the real engine

Consolidate entry rules currently duplicated between the engine and signal generator behind shared domain functions without changing accepted behavior. Add optional versioned decision traces containing event sequence, bar start/availability, rule identifier, accepted/rejected outcome, relevant thresholds, opening-range state, and position changes. Capture explanations during computation; do not infer explanations afterward from a second UI implementation.

Detailed trace mode is explicit and bounded. Normal runs retain coarse progress; trace runs retain enough events for deterministic reconstruction. Define behavior when a trace is unavailable, truncated, or from an unsupported schema.

### P4-O2: Replay only information available at the cursor

Provide play, pause, step, seek, reset, and playback speed. Reconstruct state from events plus optional checkpoints; stepping backward must rebuild state rather than mutate an already advanced position. Availability time plus sequence breaks ties deterministically.

Hide future candles, entries/exits, final trade outcomes, full-session high/low, final range, and end-of-run metrics while in replay mode. During range formation show only observed range values; freeze them at the tested decision boundary. Clearly separate completed-results mode from replay. Playback speed changes presentation cadence only.

### P4-O3: Compare runs with explicit compatibility

Display config diffs, source/data fingerprints, metric deltas, and equity comparison. Label unequal date ranges, symbols, feeds, initial capital, and data versions. Default to comparable common timestamps for overlay comparisons, with an explicit alternative full-period display. Never fill missing trade sessions as if they were zero-return observations without a stated policy.

Offer absolute equity and normalized return views with explicit baselines. Metric differences remain linked to each run's original full-period metrics unless recalculation over a common period is explicitly implemented and labeled.

### P4-O4: Connect observed behavior to recorded code

Show rule-to-module/function references and recorded source text from an allowlisted run snapshot. Current worktree code must not masquerade as historical code. If source snapshots are unavailable, show fingerprint and an unavailable explanation. Source viewing is read-only; editing happens in the user's normal editor and starts a new run.

## Implementation sequence

1. Extract shared decision functions under parity tests.
2. Define trace/checkpoint schema, source map, and serializer; emit actual engine decisions.
3. Implement replay reducer and cursor filtering independently of UI scheduling.
4. Add replay controls and prevent future information in every visible component.
5. Add compatibility-aware comparisons, config diffs, and source pane.

Likely files: `src/strategy/signals.py`, `src/backtest/engine.py`, `src/services/events.py`, `src/services/replay.py`, and dashboard replay/comparison views.

## Tests, including edge and corner cases

| ID | Scenario | Required assertion |
|---|---|---|
| P4-T01 | Engine before/after shared-rule extraction | Trades, fills, reasons, and equity match the accepted Phase 1 fixtures. |
| P4-T02 | Perturb all bars after cursor, including future OR extremes | State and every visible replay value at/before cursor are unchanged. |
| P4-T03 | Cursor before first bar, during OR, exactly at freeze, first entry, last exit, after end | Only available events are visible; range freezes correctly; empty/end controls behave predictably. |
| P4-T04 | Step N times vs play/seek to same event; backward seek; repeated reset | Same domain state at same cursor; no duplicated trades or accumulated P&L. |
| P4-T05 | Equal timestamps, late availability, skipped session, missing bar | Sequence/availability ordering is deterministic and missing intervals are visible. |
| P4-T06 | Change speed while playing; pause during refresh; rerender repeatedly | Cursor advances once per scheduled action; pause stops progression; speed never changes decisions. |
| P4-T07 | Truncated trace, unknown schema, missing checkpoint, no trace run | Explicit unavailable/recovery behavior; replay never invents missing decisions. |
| P4-T08 | Complete replay with normal, no-trade, stop/target dual-touch, fallback-EOD fixtures | Final trade ledger, equity, and position state match completed run artifacts. |
| P4-T09 | Compare identical runs; different capital, calendars, symbol/feed/data/source | Identical deltas are zero; differences are labeled; normalization and alignment follow documented policy. |
| P4-T10 | No overlapping timestamps; one-point overlap; missing starting equity | No misleading overlay or division by zero; user receives an explanation or explicit alternate view. |
| P4-T11 | Change current source; historical snapshot missing; unsafe source path | Historical source remains correct or explicitly unavailable; files outside allowlist cannot be read. |
| P4-T12 | Large trace with checkpoints; random forward/backward seeks | Reconstruction equals full sequential reduction; storage and seek work stay bounded by documented checkpoint policy. |

## Acceptance criteria

- **P4-A1:** Rule extraction preserves accepted engine outputs; trace explanations originate from executed rules (T01).
- **P4-A2:** Causality tests cover all replay-visible components, including metric cards, tables, tooltips, and source input details; no future state leaks (T02–T03, T05).
- **P4-A3:** Play/step/seek/reset produce identical state for the same cursor, and complete replay matches artifacts (T04, T06, T08).
- **P4-A4:** Unsupported/incomplete traces produce explicit limitations rather than reconstructed fiction (T07).
- **P4-A5:** Comparison labels input incompatibilities and does not confuse common-window charts with full-period metrics (T09–T10).
- **P4-A6:** Code views identify the recorded revision/fingerprint and obey source allowlists (T11).
- **P4-A7:** On the recorded machine, seeking within a 100,000-event trace completes within 500 ms using the documented checkpoint strategy; replay controls remain responsive (T12).

## Evidence and handoff

Save `validation/phase-04.md` with causality test results, trace schema, comparison/alignment rules, screenshots at OR formation/freeze/exit, and seek benchmarks. Shared rule functions and deterministic replay fixtures become the reference for Phase 5 parity tests.
