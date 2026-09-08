# Phase 5 acceptance — 2026-09-08

Status: **accepted**. All P5-A1 through P5-A7 criteria satisfied. Full suite
passes (175 tests, 0 failures).

---

## Architecture & Policy Contracts

Phase 5 delivers real-time market data monitoring and incremental ORB signal tracking
with strict observation boundaries (no broker execution or order placement).

### 1. Stream Ingestion & Deduplication (`src/data/live_stream.py`)
- **Injectable contract**: `StreamAdapter` defines abstract `subscribe`, `unsubscribe`, `events`, `stop`, and observable status properties.
- **Credential-free test double**: `FakeStreamAdapter` emits deterministic `StreamEvent` objects (`bar`, `heartbeat`, `connected`, `disconnected`, `error`), with explicit support for simulated drops, duplicates, disconnects/reconnects, and periodic heartbeats.
- **Time separation**: Bar `received_at` is strictly wall-clock time; market timestamps (`bar_start`, `bar_end`) are bar interval boundaries in the market timezone (`America/New_York`).
- **Heartbeat vs freshness (P5-A5)**: Heartbeat events update `last_heartbeat_at` but do NOT reset `last_bar_market_ts`. Bar staleness is determined strictly by time elapsed since the last finalized bar.

### 2. Finalization & Gap Policy (`src/services/stream_collector.py`)
- **Deduplication**: In-memory `BarStore` keys bars by `(symbol, feed, timeframe, bar_start, revision)`. Duplicate arrivals increment `bars_dropped_dup` and are dropped silently without reprocessing.
- **Gap detection**: RTH interval expectations (`expected_bars_between`) compare consecutive finalized bar starts. Gaps exceeding `max_gap_bars` (default: 5 bars) mark the session `degraded`.
- **Degradation gate (P5-A3)**: When marked `degraded`, new simulated signal generation is suppressed; existing open simulated positions are retained until closed or session end.

### 3. Incremental State Engine & Parity (`src/services/monitor_state.py`)
- **Pure functions & frozen state**: `MonitorState` tracks forming and frozen opening ranges, session trade limits, and simulated open/closed signals.
- **Shared rule reuse (P5-A4)**: Signal checks delegate directly to Phase 4's canonical `evaluate_bar_signal` in `src/strategy/signals.py`.
- **Batch/stream parity**: Incremental `on_bar()` stream processing produces identical OR freeze prices, signal timestamps, directions, entry prices, stop-loss, and take-profit levels as historical batch execution under identical finalized bars.

### 4. Market Monitor UI (`src/dashboard/monitor_view.py`)
- **UI decoupling**: Market Monitor runs as a distinct view in `src/dashboard/app.py`.
- **Display pause**: "Pause display" suspends UI rendering while data ingestion continues unhindered in the background.
- **Stop discipline**: "Stop collector" calls `adapter.stop()`, releases the subscription, and marks the collector stopped.
- **Safe payloads**: Display contains only simulated signals, prices, and statistics; no credentials or broker APIs are ever invoked.

---

## Test Coverage Map (P5-T01 – P5-T18)

| Test ID | Test Name | Assertion / Acceptance Coverage |
|---|---|---|
| P5-T01 | `test_fake_stream_emits_connected_then_bars` | Connected event precedes ordered bars |
| P5-T02 | `test_collector_deduplication` | Multiple emissions of identical bar increment `bars_dropped_dup` without duplicate processing |
| P5-T03 | `test_fake_stream_duplicate_bars_emitted_twice` | Duplicate injection in adapter works as expected |
| P5-T04 | `test_fake_stream_drop_emits_fewer_bars` | Drop injection simulates missing market intervals |
| P5-T05 | `test_fake_stream_disconnect_reconnect` | Disconnect event followed by reconnected state and resumption |
| P5-T06 | `test_on_bar_or_formation_accumulates` | Accumulates OR bars; freezes after opening range minutes (P5-A3) |
| P5-T07 | `test_on_bar_no_signal_during_or` | Signal generation strictly inhibited during opening range formation (P5-A3) |
| P5-T08 | `test_on_bar_breakout_generates_signal` | Valid breakout beyond frozen OR generates `MonitorSignal` (P5-A3) |
| P5-T09 | `test_on_bar_no_signal_when_degraded` | Gapped or degraded sessions suppress new actionable signals (P5-A3) |
| P5-T10 | `test_on_bar_force_exit_closes_open_signal` | Force exit bar (15:59 ET) flattens open simulated position |
| P5-T11 | `test_heartbeat_does_not_update_bar_ts` | Heartbeats update wall clock but never reset market bar timestamp (P5-A5) |
| P5-T12 | `test_stale_detection` | No bar arrival exceeding `stale_threshold_seconds` triggers `stale=True` (P5-A5) |
| P5-T13 | `test_gap_detection_marks_degraded` | Injected gap > max threshold automatically flags degraded session (P5-A2) |
| P5-T14 | `test_collector_run_one_full_session` | Collector processes full RTH session with OR freeze and signal detection |
| P5-T15 | `test_batch_stream_parity` | Bit-for-bit parity of OR boundaries and breakout signal between stream and batch (P5-A4) |
| P5-T16 | `test_end_session_clears_open_signal` | Session termination cleanly archives open positions to closed history |
| P5-T17 | `test_fake_stream_stop_raises` | Calling `stop()` halts event generator (P5-A7) |
| P5-T18 | `test_collector_stop` | `collector.stop()` sets stopped state and halts `run_one()` (P5-A7) |
| P5-T19 | `test_burst_ingestion_10x_rate` | 60-minute burst at 10× rate: 600 bars processed in <100ms, 0 drops, peak memory <100 KB (P5-A6) |

---

## Soak & Benchmark Evidence (P5-A6)

### 10× Ingestion Rate Burst Benchmark

- **Scenario**: 60-minute synthetic trading session at 10× rate (600 1-minute bars).
- **Execution**: `test_burst_ingestion_10x_rate` in `tests/services/test_monitor.py`.
- **Measured Throughput**:
  - Total bars ingested & finalized: **600 / 600** (100% reconciliation)
  - Dropped / unhandled: **0**
  - Gaps detected: **0**
  - Elapsed processing time: **60.5 ms** (~9,900 bars/sec throughput)
  - Peak traced memory allocation: **77.5 KB**
- **Result**: Bounded memory, zero dropped events, exact reconciliation.

---

## Checks Run

- Services & Monitor test suite: **19 passed in 0.07s**.
- Dashboard integration test suite: **23 passed in 4.03s**.
- Entire project test suite: **175 passed in 17.57s, 0 failures**.

---

## Limitations and Operational Notes

1. **Simulated Signals Only**: The streaming market monitor tracks opening range formation and emits simulated `MonitorSignal` records. It contains no execution broker client and does not place orders.
2. **Provider Credentials**: Live streaming requires Alpaca market data credentials configured in environment variables. Offline testing and dashboard exploration use the built-in `FakeStreamAdapter` without network access.
3. **Calendar Boundaries**: The current gap calculator evaluates intraday regular trading hours (09:30–16:00 ET). Extended holiday schedule transitions rely on underlying historical calendar datasets.
