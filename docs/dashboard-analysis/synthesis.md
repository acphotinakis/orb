# Dashboard Discovery Synthesis

**Synthesis Date:** 2026-09-08 (revised per `synthesis-review.md`)
**Sources Analyzed:** 10 files in `docs/dashboard-analysis/` (9 entries; final entry holds 2 log files)
**Target Implementation:** 5-phase local Streamlit ORB research dashboard
**Status:** Proposed synthesis — no implementation phase completed; decisions D1–D6 resolved for planning per review, phases not marked complete

## 1. Synthesis Overview

**Discovery question:** What should an interactive live dashboard for this ORB repo be, and what must be fixed before it can be trustworthy?

**Sources Analyzed:**
1. `live-dashboard-assessment.md` — Architecture assessment + 7 correctness issues + proposed Streamlit/Plotly/worker design (repo at commit `3d6cc28`, 2026-09-08)
2. `implementation-plan.md` — Master 5-phase delivery plan with shared contracts and completion rules
3. `phase-01-correctness-foundation.md` — P1 objectives/tests/acceptance (P1-O1..O4, P1-T01..T13, P1-A1..A6)
4. `phase-02-results-explorer.md` — P2 read-only explorer (P2-O1..O4, T01..T12, A1..A6)
5. `phase-03-run-control.md` — P3 submission/worker/progress (P3-O1..O4, T01..T14, A1..A6)
6. `phase-04-replay-comparison-code-trace.md` — P4 replay/compare/trace (P4-O1..O4, T01..T12, A1..A7)
7. `phase-05-streaming-market-monitor.md` — P5 streaming monitor (P5-O1..O4, T01..T14, A1..A7)
8. `verification-summary.md` — Baseline: 21 passed, 12 failed, 1 warning (importlib mode)
9. `test-results.txt` + `test-results-importlib.txt` — 2 files counted as 1 entry: raw evidence (collection collision + full failure list)

**Target Implementation Phases:**
- P1 Correctness foundation → P2 Results explorer → P3 Run control → P4 Replay/comparison/trace → P5 Streaming monitor
- Release boundary: historical dashboard releasable after P3; P4 extends it; P5 adds monitoring + simulated signals only (no broker orders).

## 2. Executive Summary

**Key Insight:** The repo already supplies most of the calculation layer (config, fetch/cache, ORB strategy, backtest engine, metrics, paths) but supplies none of the interactive layer (no web app, worker, progress events, or streaming adapter). Worse, the calculation layer is not yet trustworthy for interactive use: processed cache reuses incompatible flags, advertised strategy controls don't affect execution, run identities overwrite, accounting doesn't reconcile ($97.75 net P&L vs $100,105.11 ending equity from $100k), and time semantics conflate bar-open with decision time.

**Recommendation:** Build a local single-user Streamlit + Plotly dashboard on a separate worker process, but sequence strictly P1 → P2 → P3 → P4 → P5. Do not expose strategy controls, live session state, or streaming signals until P1 accounting/cache/config contracts pass. Present "live" as three distinct modes (running-job progress, historical replay, market monitoring) with replay explicitly labeled as replay.

**Confidence Level:** High for P1–P3 direction (grounded in code inspection + failing tests). Medium for P4–P5 effort (depends on P1 root-cause findings and unverified Alpaca stream/feed entitlement).

**Major Decisions — Resolved for planning (per `synthesis-review.md`; do not mark any phase complete):**
- D1: Restore static PNG exports when `generate_plots=True`; skip when false (P1-O1). Interactive Plotly charts use tables and do not depend on PNGs.
- D2: Use immutable, versioned processed datasets keyed by every content-affecting input (raw-content fingerprint, feed, symbol, timeframe, bounds, preprocessing version, OR duration, exit time). Normalized-bars-plus-per-run-flags may be a later optimization.
- D3: Adopt a one-minute dashboard preset; migrate unused YAML `parameters` fields into explicit config/run-option fields with documented precedence. This does not authorize changing unrelated strategy defaults such as target R.
- D4: Extract shared domain decision functions preserving accepted engine behavior under parity tests (P4-O1). In P1, reject unsupported controls; shared-rule extraction belongs to P4 unless a correctness fix requires it earlier.
- D5: Keep stated exclusions: no broker orders, browser editing, multi-user hosting, or EMA integration in P1–P5.
- D6: One worker process with a durable queue (master plan + P3). Keep existing domain modules; add `src/dashboard/` and `src/services/` — do not move into frontend/backend directories.

## 3. Consolidated Findings

### Validated Hypotheses

Count: 1 validated (H1), 1 partially validated (H4), 4 invalidated (H2, H3, H5, H6). Partial validation is not counted as full validation.

| ID | Original Hypothesis | Status | Evidence | Implications |
|----|---------------------|--------|----------|--------------|
| H1 | Existing pipeline can be reused as dashboard compute backend | Validated | `ORBPipeline.run`, `BacktestEngine.run`, metrics/reporter, `PathManager` mapped in assessment | Keep CLI; add `RunRequest` + service/worker around existing pipeline, don't rewrite engine |
| H2 | Interactive controls are safe to expose directly | Invalidated | Cache identity omits OR/feed/exit (F1); intrabar/ATR/filters unimplemented (F2); config split (F3) | P1 must gate every control with documented effect + validation; reject unsupported with field errors |
| H3 | Runs are reproducible/comparable today | Invalidated | Default `baseline_v1` overwrites; no manifest/fingerprint; shared processed bars mutable (F4); 12 baseline failures (F8–F9) | UUID run IDs, immutable manifests, dataset fingerprints required before comparison UI |
| H4 | Reported metrics/equity can be shown as headlines | Partially validated | Formulas exist but P&L/equity mismatch ($97.75 vs +$105.11), drawdown 25.0 vs 10.0 expected, fixture drops 374 bars | Accounting oracle + fixture repair in P1; normalize non-finite metrics at JSON boundary in P1 |
| H5 | "Live" = one streaming mode | Invalidated | Assessment distinguishes job progress vs replay vs monitor; batch processor prunes incomplete OR sessions | Three separate views with distinct contracts; never run unfinished sessions through batch processor |
| H6 | Static plots + EMA scripts are dashboard-ready | Invalidated | Plot calls commented out; EMA scripts have separate contracts not wired to `ORBPipeline` | Decide plot contract in P1; EMA requires canonical choice + strategy interface (deferred past P5) |

### Research Questions Answered

| ID | Question | Answer | Confidence | Sources |
|----|----------|--------|------------|---------|
| Q1 | What stack for local dashboard? | Streamlit (fragments for ~1s polling) + Plotly (candlestick/equity), SQLite registry + per-run files, single worker process | High | Assessment + P2/P3 specs |
| Q2 | What is baseline test health? | 21 passed / 12 failed / 1 warning (importlib); default collection fails on duplicate `test_metrics.py` | High (observed) | verification-summary + both logs |
| Q3 | What are the 12 failures? | 1 config (target_r 1.25 vs 2.0), 4 validator `timeframe` arg, 4 `slippage_paid` fixtures, 1 drawdown ($25 vs $10), 1 `AppConfig` 4 missing fields, 1 missing `equity_curve.png` | High | importlib log + summary |
| Q4 | Can CLI test prove pipeline? | No — excluded (network) + asserts exit 0-or-1 | High | Assessment §Verification |
| Q5 | How to handle source-code interaction? | Call engine via worker with recorded revision/fingerprint; read-only allowlisted source pane; edit in normal editor → new run; no hot-swap, no browser editor in scope | High | Assessment + P4-O4 |
| Q6 | When to add streaming? | After P1–P4; needs `live_stream.py`, finalization/correction/gap policies, incremental `on_bar/snapshot/end_session` sharing P4 rule functions | Med | Assessment + P5 spec |
| Q7 | What perf budgets? | P2: warm selection + session render <2s (100k equity rows/10k trades fixture); P3: status <500ms, progress <2s, CPU-cancel ack <2s; P4: 100k-event seek <500ms; P5: 60-min 10× soak bounded | Low (proposed, unmeasured) | Phase specs |

### Key Insights by Theme

**User / Product:**
- First screen: left config, top job status, center equity/drawdown, bottom selectable trade table; trade selection opens session chart + decision details. Slider = draft only; Run button submits.
- Empty state required: inspected repo had no experiment/data dirs; fresh checkout must guide to synthetic demo, not blank charts.
- Browsing must never trigger simulation or Alpaca downloads (P2-T08); full-run metrics vs filtered-table stats must be explicitly labeled.
- Accessibility: keyboard-operable selectors, visible focus, text summaries, non-color-only distinctions.

**Technical:**
- Cache: key by source fingerprint + feed + timeframe + dates + preprocessing version + OR duration + exit time; propagate `refresh_cache` → processor `force_refresh`; atomic writes + per-key sync; manifest last.
- Config: single `RunRequest` (AppConfig + dates/refresh/plots/label/log); reject unknown overrides; validate symbols match, clocks real, dates ordered, numbers finite, timeframe/window supported; 1-min initial preset.
- Execution: one active worker; states queued/running/cancelling/cancelled/succeeded/failed; token→run-ID idempotency; persist refs before launch; per-run logging (shared logger `setup_logging` interferes today); cancel at stage/session/bar-loop bounds + finite data timeouts.
- Events: versioned `run_started/stage_started/session_completed/trade_opened/trade_closed/run_completed/run_failed` with seq numbers; coarse by default, detailed trace opt-in bounded.
- Replay causality: slice by availability time + seq; hide future candles/entries/final range/metrics; freeze forming range at decision boundary; backward step = rebuild, not mutate.
- Compare: diff config/fingerprints, delta metrics, aligned equity (common timestamps default); label incompatible capital/calendar/symbol/feed/data; never zero-fill missing sessions silently.
- Streaming: persistent collector independent of browser/worker; dedup subscriptions; persist bars by (symbol/feed/timeframe/bar-start/revision); reorder window + backfill overlap idempotent; corrections → new revision + rebuilt state, original alerts retained; stale = f(threshold + poll); heartbeats ≠ freshness.
- Security: run IDs server-generated; path components validated (no traversal/symlink escape); allowlisted artifacts only; text rendered as text; secrets never in export/browser/errors; no shell-built worker commands; no order API calls in monitor.

**Business / Process:**
- Batch explorer ≪ streaming monitor in effort; single schedule for both hides main uncertainty — estimate only after P1 baseline reproduced.
- Each phase needs `docs/dashboard-analysis/validation/phase-NN.md` (commit/fingerprint, env/versions, commands, outcomes, screenshots, limitations) — does not exist yet.
- Financial assertions need independent hand-calculated expected values + declared tolerances (display rounding vs full-precision calc); never derive expected from implementation under test.
- Offline deterministic fixtures by default; external-data integration opt-in only.

## 4. Remaining Unknowns

| ID | Unknown | Impact | Recommendation | Priority |
|----|---------|--------|----------------|----------|
| U1 | Accounting mismatch root cause (EOD liquidation after last equity sample suspected, not proven) | Headline metrics untrustworthy | Reproduce + hand-calc oracle in P1-O4/T09 | High |
| U2 | Intended contracts for 12 failing tests (e.g., drawdown formula vs fixture, target_r 1.25 vs 2.0, `slippage_paid` requirement) | Can't fix without changing wrong side | Determine intended contract before editing assertions | High |
| U3 | Supported calendar/timeframe matrix (DST, early close, coarse TF, missing final bar) | Live state could mislead | Document + enforce; reject unsupported clearly (P1-T11, P5-T08) | High |
| U4 | Feed entitlement + SDK behavior at implementation time | P5 design may mismatch reality | Verify against official Alpaca docs at P5 start; explicit entitlement errors | Med |
| U5 | Perf budgets achievability on real machines | Could fail A-gates | Record machine + fixture size; change budgets only via documented measured decision | Med |
| U6 | Legacy run dirs without manifests — import or reject? | UX + provenance risk | Explicit validated import flow or mark unsupported; never guess (P2-O2) | Med |
| U7 | Trace/checkpoint storage cost + schema evolution | Replay perf/correctness | Bounded detailed-trace mode; checkpoint policy benchmarked (P4-T12/A7) | Med |

## 5. Recommendations by Phase

### P1: Correctness foundation (first, blocks dependent phases)

| # | Ticket | Title | Type | Priority | Acceptance | Based On |
|---|--------|-------|------|----------|------------|----------|
| 1 | P1-1 | Fix pytest collection + repair 12 baseline failures per intended contracts | Task | High | P1-A1 (final green depends on P1-2..P1-6 fixes) | F8, F9, U2 |
| 2 | P1-2 | Introduce `RunRequest` + shared validation; migrate YAML `parameters`, 1-min preset, CLI flag fixes (`--paper/--no-paper`, log level) | Story | High | P1-A2 | F3 (D3 resolved: 1-min preset + migration) |
| 3 | P1-3 | Processed-cache identity (all inputs) + dependent refresh + atomic publication + path safety | Story | High | P1-A3 | F1 (D2 resolved: versioned immutable datasets) |
| 4 | P1-4 | UUID run IDs + immutable manifests (config/options/fingerprint/dataset/checksums/schema) | Story | High | P1-A6 | F4 |
| 5 | P1-5 | Reconcile accounting (`final_capital = initial + Σ net_pnl` ±$0.01) + restore plot exports | Story | High | P1-A4 + P1-A1/T12 (plot part) | F6, F7, U1 (D1 resolved: restore PNGs) |
| 6 | P1-6 | Document bar-start vs availability time + calendar policy | Task | High | P1-A5 | F7, U3 |

**Scope:** Include all above. Defer any UI/worker/streaming. Do not start dependent phase implementation until P1-A1..A6 are green.
**Dependencies:** Reproduce baseline failures and add the accounting regression first, but do not require P1-1 completion before P1-2..P1-6 begin — P1-1's final green suite depends on those fixes.
**Risks:** Fixing tests by weakening assertions (forbidden — must resolve intended contracts); claiming atomicity across files (publish manifest last instead).

**Recommended P1 execution order (per review):**
1. Reproduce baseline failures and add an independent accounting regression reproducing the discrepancy.
2. Resolve intended config/metric contracts and repair malformed fixtures without weakening assertions.
3. Implement RunRequest validation and explicit time/session semantics.
4. Implement processed-cache identity, dependent refresh, and immutable run manifests together.
5. Correct accounting and restore plotting, using the focused regression and export tests.
6. Run the full offline suite and complete `validation/phase-01.md` with actual evidence.

### P2: Results explorer

| # | Ticket | Title | Type | Priority | Acceptance | Based On |
|---|--------|-------|------|----------|------------|----------|
| 1 | P2-1 | Streamlit app + pinned `requirements-dashboard.txt` + synthetic demo command + empty state | Story | High | P2-A1 | F10, F11 |
| 2 | P2-2 | Manifest-based run selector + metrics/config/data-fingerprint display | Story | High | P2-A2 (part) | F4 |
| 3 | P2-3 | Plotly equity/drawdown/distributions + trade table + session candlestick with range/stop/target markers | Story | High | P2-A2 (part) + P2-A5 (perf) | F4 datasets/metrics (not PNG decision) |
| 4 | P2-4 | Downloads (allowlisted), corrupt/legacy handling, timezone ET labels, XSS/path-safety, a11y | Story | Med | P2-A3, A4, A6 | F11 + P2-T05..T09 |

**Defer:** Any submission/execution side effects; replay causality (P4).

### P3: Run control and progress (first releasable historical loop)

Canonical inventory retains 4 items so race testing stays visible.

| # | Ticket | Title | Type | Priority | Acceptance | Based On |
|---|--------|-------|------|----------|------------|----------|
| 1 | P3-1 | Registry transactions, token idempotency, single-worker supervisor, crash/restart reconciliation | Story | High | P3-A1, A2 | F5 (D6: single worker) |
| 2 | P3-2 | Pipeline/engine progress + cancel callbacks; ~1s polling; bounded logs | Story | High | P3-A4, A5 | F5 |
| 3 | P3-3 | Submit/status/cancel UI with reconnect-by-ID + secret redaction | Story | High | P3-A3 (part) | F5 |
| 4 | P3-4 | Failure-injection + cancel/completion race tests + CLI regression gate | Task | High | P3-A3 (race part) + P3-A6 | P3-T05..T10, T13 |

### P4: Replay, comparison, code trace

Canonical inventory retains 4 items so the source pane stays visible.

| # | Ticket | Title | Type | Priority | Acceptance | Based On |
|---|--------|-------|------|----------|------------|----------|
| 1 | P4-1 | Extract shared decision functions under parity tests; versioned decision-trace schema | Story | High | P4-A1 | F2 (D4: shared functions, P4 timing) |
| 2 | P4-2 | Replay reducer + controls (play/pause/step/seek/speed) with causality enforcement + incomplete-trace handling + seek perf | Story | High | P4-A2, A3, A4, A7 | H5 |
| 3 | P4-3 | Compatibility-aware comparison + normalized/absolute equity views | Story | Med | P4-A5 | F4 |
| 4 | P4-4 | Read-only source pane from allowlisted snapshot + fingerprint | Story | Med | P4-A6 | Q5 |

### P5: Streaming market monitor

Canonical inventory retains 4 items so soak/provider verification stays visible.

| # | Ticket | Title | Type | Priority | Acceptance | Based On |
|---|--------|-------|------|----------|------------|----------|
| 1 | P5-1 | `live_stream.py` behind injectable interface + persistent collector + feed/connection/freshness UI | Story | Med | P5-A1, A5 | F10, U4 (D5/D6 scope) |
| 2 | P5-2 | Finalization/correction/gap/calendar policies + backfill/dedup | Story | Med | P5-A2, A3 | H5, U3 |
| 3 | P5-3 | Incremental `on_bar/snapshot/end_session` reusing P4 rules + batch/stream parity tests | Story | Med | P5-A4 | F2 (D4 shared rules) |
| 4 | P5-4 | Pause/stop behavior + no-order/secret audit + 60-min soak + opt-in provider smoke test | Task | Med | P5-A6, A7 | U4, U5 |

**Explicitly NOT in these phases:** broker order submission/reconciliation, browser code editor, multi-user hosting, EMA canonicalization (choose canonical impl + strategy interface first, later).

## 6. Decision Log (resolved for planning per `synthesis-review.md`)

| Decision | Options considered | Direction | Rationale |
|----------|-------------------|-----------|-----------|
| D1 Plot contract | Restore PNGs / Revise contract | Restore PNGs when `generate_plots=True` | Already specified in P1-O1; test expects PNGs |
| D2 Cache design | Version-all-inputs / Normalized-bars + per-run flags | Immutable versioned processed datasets keyed by all content-affecting inputs | Smallest change to current processor contract; normalized derivation is a later optimization |
| D3 Defaults | 1-min preset + migrate YAML `parameters` | Adopt 1-min research preset + explicit migration with documented precedence | Already specified in P1; does not change unrelated defaults (e.g., target R) |
| D4 Signal consolidation | Engine path / Generator / Shared fn | Shared domain functions under parity tests, in P4 timing | Already specified in P4-O1; P1 rejects unsupported controls |
| D5 Scope | Include/exclude orders/editor/multi-user/EMA | Exclude from P1–P5 | Already explicit in master plan scope |
| D6 Concurrency | Single worker / pool | Single worker + durable queue | Already explicit in master plan + P3; keep `src/dashboard/` + `src/services/`, no frontend/backend move |

### 6a. Decision Status

**Status:** Resolved for planning — no new approval gate needed for D1, D3-preset, D4, D5, D6; D2 resolved as routine implementation choice above. YAML field mapping and compatibility details still require P1-2 implementation + tests. Later-phase acceptance criteria remain those in the detailed phase specs.

| ID | Decision | Status | Direction | Blocks |
|----|----------|--------|-----------|--------|
| D1 | Static plot export | Resolved | Restore | — (P1-5 implements; P2-3 depends on validated datasets, not PNGs) |
| D2 | Cache architecture | Resolved | Versioned immutable datasets | — (P1-3/P1-4 implement) |
| D3 | Defaults + YAML migration | Resolved | 1-min preset + migration | — (P1-2 implements) |
| D4 | Signal implementation | Resolved | Shared functions in P4 | — (P4-1/P5-3 implement) |
| D5 | P1–P5 exclusions | Resolved | Exclude | — |
| D6 | Worker concurrency | Resolved | Single worker | — |

## 7. Source Cross-Reference

| Source | Key Contributions | Findings Referenced |
|--------|-------------------|---------------------|
| live-dashboard-assessment.md | 7 correctness issues, architecture, service/event contract, streaming guidance | F1–F7, F10–F13, H1–H6, Q1, Q4–Q6 |
| implementation-plan.md | Phase DAG, shared contracts, completion rules, validation-record requirement | Targets, shared contracts, budgets |
| phase-01 spec | P1-O/T/A, file touchpoints (`run_models`, `config`, `paths`, `processor`, `pipeline`, engine/report) | P1 recs 1–6 |
| phase-02 spec | Offline demo, read-only browsing, Plotly/ET/units, safety/a11y, 2s budgets | P2 recs 1–4 |
| phase-03 spec | Token idempotency, worker/supervisor, states/races, 500ms/2s budgets | P3 recs 1–4 |
| phase-04 spec | Shared rules, causality, comparison policy, source pane, 500ms seek | P4 recs 1–4 |
| phase-05 spec | Collector, finalization/correction/gap, incremental state, soak/parity | P5 recs 1–4 |
| verification-summary.md | 21/12/1 baseline, 6 failure groups, P&L/equity mismatch (cause unproven, U1 preserved), 374-bar fixture loss | F8, F9, U1 |
| test-results.txt + test-results-importlib.txt (2 files) | Collection collision proof + full assertion texts | F8, F9, U2 |

## 8. Appendix: Full Findings Inventory

| Finding ID | Source(s) | Category | Finding | Confidence | Actionable |
|------------|-----------|----------|---------|------------|------------|
| F1 | Assessment §1 | Technical | Processed cache key omits feed/OR/exit; `refresh_cache` not propagated to processor | High | Yes → P1-3 |
| F2 | Assessment §2 | Technical | `_check_signal_at_bar` bypasses `SignalGenerator`; intrabar/ATR/filters non-functional | High | Yes → P1-2, P4-1 |
| F3 | Assessment §3 | Technical | YAML `parameters` unconsumed; 15Min vs 1-min mismatch; `--paper`/`--no-paper`/log-level gaps | High | Yes → P1-2 |
| F4 | Assessment §4 | Technical | `baseline_v1` overwrites; no manifest/fingerprint; shared bars mutable; artifacts incomplete in result object | High | Yes → P1-4 |
| F5 | Assessment §5 | Technical | Synchronous pipeline; no progress/cancel API; shared-logger thread interference | High | Yes → P3 |
| F6 | Assessment §6, summary, importlib log | Technical | Plot calls commented out; `generate_plots=True` no-ops; test expects `equity_curve.png` | High | Yes → P1-5 (D1) |
| F7 | Assessment §7, summary | Technical | Bar-open vs close/availability conflated; EOD liquidation after last equity sample; $97.75 vs $100,105.11 mismatch | High | Yes → P1-5/6 |
| F8 | Summary + logs | Process | Baseline 21✅/12❌/1⚠️ (importlib); default mode collection error on duplicate `test_metrics.py` | High (observed) | Yes → P1-1 |
| F9 | Importlib log (773 lines) | Technical | 6 failure groups enumerated (config/validator×4/slippage×4/drawdown/report-config/pipeline) + 374-bar fixture loss | High | Yes → P1-1 |
| F10 | Assessment + plan | Scope | No web app/worker/events/streaming; proposed paths uncreated; deps unpinned without Streamlit/Plotly | High | Yes → P2/P3/P5 |
| F11 | Assessment | UX | README/runbook stale; no experiment dirs; needs empty state + synthetic demo | Med | Yes → P2-1 |
| F12 | Assessment | Product | Three live modes; replay must self-identify; form-draft vs submit semantics | High | Yes → P2–P4 |
| F13 | Assessment | Technical | Two EMA implementations unwired to `ORBPipeline`; dropdown alone insufficient | High | Yes → deferred past P5 |

## 9. Proposed Tickets Data

**Status:** Aligned to review — 22 canonical work items matching §5 tables; every acceptance criterion mapped
**Last Updated:** 2026-09-08 (revised per `synthesis-review.md`; no approval event claimed)

<!-- PROPOSED_TICKETS_START -->
```json
{
  "version": "1.0",
  "synthesis_source": "docs/dashboard-analysis/",
  "discovery_scope": "ORB interactive live dashboard",
  "source_file_count": 10,
  "hypothesis_counts": {"validated": 1, "partial": 1, "invalidated": 4},
  "decisions": [
    {"id": "D1", "direction": "Restore PNG exports when generate_plots=True", "status": "resolved"},
    {"id": "D2", "direction": "Immutable versioned processed datasets keyed by all content-affecting inputs", "status": "resolved"},
    {"id": "D3", "direction": "1-min dashboard preset + migrate YAML parameters with documented precedence", "status": "resolved"},
    {"id": "D4", "direction": "Shared domain decision functions under parity tests, in P4 timing", "status": "resolved"},
    {"id": "D5", "direction": "Exclude orders/browser-editor/multi-user/EMA from P1-P5", "status": "resolved"},
    {"id": "D6", "direction": "Single worker + durable queue; add src/dashboard/ + src/services/", "status": "resolved"}
  ],
  "proposed_tickets": [
    {"id": "P1-1", "title": "Repair offline baseline (collection + 12 failures per intended contracts)", "type": "Task", "target_phase": "P1", "priority": "High", "based_on_findings": ["F8", "F9"], "acceptance": ["P1-A1"], "note": "Final green depends on P1-2..P1-6; reproduce first without gating their start"},
    {"id": "P1-2", "title": "RunRequest + shared validation; migrate YAML params; 1-min preset; fix CLI flags", "type": "Story", "target_phase": "P1", "priority": "High", "based_on_findings": ["F2", "F3"], "acceptance": ["P1-A2"]},
    {"id": "P1-3", "title": "Cache identity (all inputs) + dependent refresh + atomic publication + path safety", "type": "Story", "target_phase": "P1", "priority": "High", "based_on_findings": ["F1"], "acceptance": ["P1-A3"]},
    {"id": "P1-4", "title": "UUID run IDs + immutable manifests + dataset references", "type": "Story", "target_phase": "P1", "priority": "High", "based_on_findings": ["F4"], "acceptance": ["P1-A6"]},
    {"id": "P1-5", "title": "Reconcile accounting + restore plot exports", "type": "Story", "target_phase": "P1", "priority": "High", "based_on_findings": ["F6", "F7"], "acceptance": ["P1-A4", "P1-A1-T12-plot"], "note": "Causation unproven (U1 preserved); needs focused regression"},
    {"id": "P1-6", "title": "Document bar/availability time + calendar policy", "type": "Task", "target_phase": "P1", "priority": "High", "based_on_findings": ["F7"], "acceptance": ["P1-A5"]},
    {"id": "P2-1", "title": "Streamlit app + demo command + empty state", "type": "Story", "target_phase": "P2", "priority": "High", "based_on_findings": ["F10", "F11"], "acceptance": ["P2-A1"]},
    {"id": "P2-2", "title": "Manifest-based run selector + metrics display", "type": "Story", "target_phase": "P2", "priority": "High", "based_on_findings": ["F4"], "acceptance": ["P2-A2-part"]},
    {"id": "P2-3", "title": "Plotly charts + trade table + session explorer", "type": "Story", "target_phase": "P2", "priority": "High", "based_on_findings": ["F4"], "acceptance": ["P2-A2-part", "P2-A5"], "note": "Depends on validated datasets/metrics, not PNG decision"},
    {"id": "P2-4", "title": "Downloads + corrupt/legacy + timezone + safety/a11y", "type": "Story", "target_phase": "P2", "priority": "Medium", "based_on_findings": ["F11"], "acceptance": ["P2-A3", "P2-A4", "P2-A6"]},
    {"id": "P3-1", "title": "Registry + token idempotency + single-worker supervisor", "type": "Story", "target_phase": "P3", "priority": "High", "based_on_findings": ["F5"], "acceptance": ["P3-A1", "P3-A2"]},
    {"id": "P3-2", "title": "Progress/cancel instrumentation + polling + bounded logs", "type": "Story", "target_phase": "P3", "priority": "High", "based_on_findings": ["F5"], "acceptance": ["P3-A4", "P3-A5"]},
    {"id": "P3-3", "title": "Submit/status/cancel UI with reconnect + redaction", "type": "Story", "target_phase": "P3", "priority": "High", "based_on_findings": ["F5"], "acceptance": ["P3-A3-part"]},
    {"id": "P3-4", "title": "Failure-injection + cancel/completion race tests + CLI regression gate", "type": "Task", "target_phase": "P3", "priority": "High", "based_on_findings": ["F5"], "acceptance": ["P3-A3-race", "P3-A6"]},
    {"id": "P4-1", "title": "Shared decision functions + trace schema", "type": "Story", "target_phase": "P4", "priority": "High", "based_on_findings": ["F2"], "acceptance": ["P4-A1"]},
    {"id": "P4-2", "title": "Causal replay reducer + controls + incomplete-trace handling + seek perf", "type": "Story", "target_phase": "P4", "priority": "High", "based_on_findings": ["F12"], "acceptance": ["P4-A2", "P4-A3", "P4-A4", "P4-A7"]},
    {"id": "P4-3", "title": "Compatibility-aware comparison views", "type": "Story", "target_phase": "P4", "priority": "Medium", "based_on_findings": ["F4"], "acceptance": ["P4-A5"]},
    {"id": "P4-4", "title": "Read-only source pane from allowlisted snapshot + fingerprint", "type": "Story", "target_phase": "P4", "priority": "Medium", "based_on_findings": ["Q5"], "acceptance": ["P4-A6"]},
    {"id": "P5-1", "title": "Persistent stream collector + freshness UI", "type": "Story", "target_phase": "P5", "priority": "Medium", "based_on_findings": ["F10"], "acceptance": ["P5-A1", "P5-A5"]},
    {"id": "P5-2", "title": "Finalization/correction/gap policies + recovery", "type": "Story", "target_phase": "P5", "priority": "Medium", "based_on_findings": ["F7"], "acceptance": ["P5-A2", "P5-A3"]},
    {"id": "P5-3", "title": "Incremental ORB state + batch/stream parity", "type": "Story", "target_phase": "P5", "priority": "Medium", "based_on_findings": ["F2", "F7"], "acceptance": ["P5-A4"]},
    {"id": "P5-4", "title": "Pause/stop behavior + no-order/secret audit + soak + opt-in provider smoke", "type": "Task", "target_phase": "P5", "priority": "Medium", "based_on_findings": ["U4", "U5"], "acceptance": ["P5-A6", "P5-A7"]}
  ],
  "approval_status": "pending-review-doc-only",
  "approved_by": null,
  "approved_date": null
}
```
<!-- PROPOSED_TICKETS_END -->
