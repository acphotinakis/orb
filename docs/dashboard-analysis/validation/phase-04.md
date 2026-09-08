# Phase 4 foundation validation — 2026-09-08

Status: in progress, not Phase 4 acceptance. Existing uncommitted Phase 4
extraction, trace, comparison, and reducer work was present at entry and retained.

## Shared rule parity

`evaluate_bar_signal` is shared by engine and scanner. The golden fixture
`tests/fixtures/phase03_engine_parity.json` was generated using the engine from
commit `69fc4bebd055995f79e26778da5c40c23bbe99c3`, before extraction. Tests
compare every serialized trade, equity row, daily row, and final capital with
tracing both disabled and enabled. Fixtures cover no trade, long/short target,
conservative dual-touch stop, and fallback EOD. Boundary/explicit-buffer tests
also remain in place.

## Trace and replay contract

Opt-in pipeline tracing emits schema v1 JSONL with a header, event count,
truncation flag and rule map. Collection is capped at 200,000 events by default.
Loading rejects unsupported versions, missing/out-of-order sequences, invalid
or decreasing availability times, and incomplete file counts. A capped trace
is an explicitly marked prefix, not a complete run.

Actual engine execution now records every observed bar, including formation,
held-position and invalid-range sessions. Frozen OR values are emitted only
at the observed boundary, with bar start and availability. Entry decisions
record buffer, direction mode and target multiple. Availability is bar start
plus configured duration; equal-time events retain execution sequence.

The reducer exposes only applied bar observations and freeze events. It does
not read full-session dataframe values or derived columns for display.
Backward seek reconstructs position/P&L. A formation test reruns the engine
with future OR extremes changed and checks identical visible bars and state;
a tie test verifies that the freeze event must actually be applied.

## Checks run

- Unit, anti-leakage, replay and comparison suite: **62 passed**.
- Replay suite after event-count validation: **15 passed**.
- Existing synthetic 100,000-event checkpoint probe: mean seek **5 ms** on
  this workspace machine. This is not P4-A7 acceptance: it lacks a realistic
  bar-observation-heavy trace and measures a mean rather than worst-case.
- Initial whole-suite run: **138 passed, 9 failed**. One source-excerpt test
  incorrectly counted a trailing blank line and is corrected. Eight worker/UI
  failures attempted provider access: default config has refresh_cache true
  while those tests seed offline cache. No full-suite pass is claimed.

## Remaining Phase 4 work

Playback controls and scheduling, replay-only UI data wiring (charts, tables,
tooltips, metrics and source inputs), recorded source snapshots, comparison UI,
full trade/equity trace reconstruction, execution rejection/held-position rule
explanations, and realistic checkpoint benchmarks remain pending. The current
source-excerpt helper reads worktree files and must not be used as a historical
source view. The reducer still scans observations in the applied prefix;
checkpointed market-state/index reconstruction is required for bounded seeks.
No browser acceptance evidence has been recorded for Phase 4.

## Provider-timeout limitation

Cancellation cannot interrupt a blocked synchronous provider call. No measured
upper bound for that condition is established here. Passing test counts and
CPU-bound cancellation measurements do not establish network cancellation
bounds; provider timeout/retry behavior needs separate controlled validation.
