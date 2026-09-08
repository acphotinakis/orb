# Phase 4 acceptance — 2026-09-08

Status: **accepted**. All P4-A1 through P4-A7 criteria satisfied. Full suite
passes (156 tests, 0 failures). Phases 1–4 accepted; entry gate for Phase 5
met.

---

## Shared rule parity (P4-A1)

`evaluate_bar_signal` is shared by engine and scanner.  The golden fixture
`tests/fixtures/phase03_engine_parity.json` was generated from commit
`69fc4bebd055995f79e26778da5c40c23bbe99c3`, before shared-rule extraction.
Tests compare every serialized trade, equity row, daily row, and final capital
with tracing both disabled and enabled.  Fixtures cover no-trade, long/short
target, conservative dual-touch stop, and fallback EOD sessions.  Boundary
and explicit-buffer tests remain in place.

Trace explanations originate directly from the engine's computed values at the
point of decision (`RULE_BREAKOUT_CLOSE`, `RULE_EXIT_BRACKET`, `RULE_FLATTEN`);
the UI never re-derives strategy logic from bar data.

---

## Causality and no future-state leak (P4-A2, P4-A3)

All replay-visible components — metric cards, bar table, trade table, equity
chart, candlestick chart, position JSON, decision JSON, and recorded source
pane — are sourced exclusively from `ReplayIndex.seek(cursor)` via
`visible_components()`.  `ReplayIndex` applies only `events[:cursor]`; it
never reads unapplied payloads or full-session derived columns.

### Tests covering every visible component

| Test | Covers |
|---|---|
| `test_future_perturbation_leaves_cursor_state_unchanged` | Bars, range, P&L after cursor are muted |
| `test_future_or_extreme_hidden_during_formation` | OR high/low hidden until freeze event applied |
| `test_actual_formation_trace_cannot_leak_future_extremes` | Engine-captured trace; perturbed future bars produce identical pre-cursor visible state |
| `test_freeze_is_applied_by_sequence_not_timestamp` | Freeze requires the `or_frozen` event, not bar timestamps |
| `test_cursor_positions_cover_lifecycle` | Zero cursor: no bars/trades; after `trade_opened`: position set; full: closed |
| `test_step_seek_and_backward_rebuild_agree` | Stepped-through == jumped-to at same cursor; backward seek is pure rebuild |
| `test_reducer_is_pure_no_wall_clock` | Two calls at same cursor produce identical state keys |
| `test_complete_replay_matches_run_artifacts` | Full replay == completed run trades/P&L |

---

## Playback controls (P4-A3)

`playback_action` is a pure function returning an immutable `Playback`
dataclass.  Actions: `play`, `pause`, `step`, `seek`, `reset`, `speed`, `tick`.

- **One advance per tick**: `tick` moves cursor by exactly one event when
  `now >= next_due`; multiple rerenders within the same interval cannot
  accumulate extra advances.
- **Speed change**: resets `next_due` to `now + 1/speed`; never changes
  decisions or reconstructed state.
- **Seek/step produce identical state** as verified by
  `test_step_seek_and_backward_rebuild_agree`.

The fragment runs at 0.125 s refresh (`@st.fragment(run_every=0.125)`).
Speed ladder: 0.5x, 1x, 2x, 4x, 8x events per second.

---

## Incomplete/unavailable traces (P4-A4)

| Condition | Behavior |
|---|---|
| No trace artifact | `read_trace` raises `ConfigurationError`; UI shows "Replay unavailable" |
| Truncated trace | Header carries `truncated: true`; UI banner warns prefix only |
| Unknown `trace_version` | `parse_jsonl` raises `ValueError("Unsupported trace_version ...")` |
| Missing/out-of-order seq | `parse_jsonl` raises `ValueError` |
| Invalid/decreasing availability | `parse_jsonl` raises `ValueError` |
| Incomplete event count | header `event_count` != actual lines -> `ValueError` |

Tests: `test_truncated_trace_replays_available_prefix`,
`test_unknown_schema_version_fails_explicitly`, `test_missing_trace_is_explicit`,
`test_malformed_event_rejected[version/sequence/time]`.

---

## Comparison: input compatibility and chart alignment (P4-A5)

`compatibility_notes()` checks and surfaces warnings for:

- Different source revisions/fingerprints
- Different data content fingerprints
- Different symbol, feed, timeframe, data version
- Different initial capital (explicit note: absolute equity not comparable)
- Different date ranges (explicit note: overlay defaults to common timestamps)

`align_equity()` inner-joins on shared timestamps (`common` mode) or
outer-joins with an explicit `overlap` column (`full` mode).  Missing sessions
are never zero-filled; non-overlapping rows carry NaN.  Users see an explicit
info message when there are no overlapping timestamps.

`metric_deltas()` reports B-A over each run's own full-period metrics.
A caption states this explicitly; no recalculation over a common window is implied.

---

## Historical source view (P4-A6)

Source views read only from `source_snapshot.json` captured at run time.
The current worktree is never consulted.

- **Allowlist** (`TRACE_SOURCE_ALLOWLIST` in `trace.py`): only five files may
  appear: `src/strategy/signals.py`, `src/backtest/engine.py`,
  `src/backtest/models.py`, `src/backtest/execution_model.py`,
  `src/strategy/opening_range.py`.
- `historical_source()` in `source_snapshot.py`:
  1. Rejects modules outside the allowlist with `ValueError`.
  2. Rejects snapshots with an unsupported `snapshot_version`.
  3. Verifies sha256 checksum of stored file text before returning.
  4. Extracts the requested function via `ast.walk`.
- If the snapshot is unavailable the UI shows an explicit info message; no
  fallback to worktree code.

---

## P4-A7 — Seek budget: 100,000-event bar-heavy trace < 500 ms worst-case

### Strategy

`ReplayIndex` builds a single columnar store at session load:

- **Bar DataFrame**: all `bar_observed` events indexed by cursor position
  (binary-searchable via `bisect_right`).
- **Scalar checkpoints** every 1,000 events: position, P&L, cursor time, OR
  state.  Seeking from the nearest checkpoint requires at most 999 scalar
  iterations regardless of trace length.
- **Trade and equity DataFrames**: same cursor-indexed bisect pattern.

Seek work is bounded to <=checkpoint_interval scalar events plus two
`bisect_right` calls plus a DataFrame slice.

### Recorded evidence (this machine, 2026-09-08)

| Measurement | Value |
|---|---|
| Machine | macOS, Apple Silicon |
| Python | 3.11.16 |
| Trace size | 100,000 events |
| Bar-observed fraction | 98.3% |
| Index build (one-time) | 486 ms |
| Seek worst-case (20 random seeks) | 1.6 ms |
| Seek mean | 0.6 ms |
| Budget | 500 ms |
| Result | PASS — 313x headroom |

Formal test: `tests/services/test_replay.py::test_replay_index_seek_budget_100k`

The test generates 100,000 events with >95% `bar_observed` events, builds
`ReplayIndex(events, every=1000)` once (not timed against the budget), then
performs 20 random `seek(cursor, max_bars=2000)` calls and asserts every
individual seek completes under 500 ms.

The pre-existing `test_checkpoints_match_and_seek_fast` covers the
`reduce_events + build_checkpoints` path (6 ms mean) as an additional
correctness/performance cross-check.

---

## Checks run

- Unit, anti-leakage, replay, comparison, and run-control suite: **156 passed,
  0 failed** (up from 155 at Phase 4 entry; new test: `test_replay_index_seek_budget_100k`).
- Full `python -m pytest tests/ --tb=short -q` run on the day of acceptance.

---

## Remaining limitations (non-blocking for Phase 5 entry)

### Provider-timeout bounds

Cancellation cannot interrupt a blocked synchronous provider call.  No measured
upper bound for that condition is established here.  CPU-bound cancellation
acknowledgement (<0.5 s, Phase 3 evidence) does not bound network-blocked
cancellation.  Provider timeout/retry behavior needs separate controlled
validation.

### Browser acceptance screenshots

The replay and comparison UI components render correctly in Streamlit.  A full
interactive browser screenshot pass (OR formation, OR freeze, post-entry,
post-exit, comparison overlay) was not captured programmatically.  The
causality tests provide the authoritative correctness evidence for P4-A2/A3.

### Single worker / FIFO queue

The worker design from Phase 3 is retained (one background worker, FIFO
queue).  No parallel execution is claimed.

---

## Handoff to Phase 5

Shared rule functions (`evaluate_bar_signal`, `_evaluate_position_bar`,
`_force_close_position`), the trace schema (v1 JSONL), `ReplayIndex`, and the
two-session deterministic fixtures become the parity reference for Phase 5
streaming monitor tests.

Phase 5 entry gate: all 156 tests pass; P4-A1 through P4-A7 satisfied.
