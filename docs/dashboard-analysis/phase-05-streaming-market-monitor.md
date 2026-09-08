# Phase 5 — Streaming market monitor

[Master plan](implementation-plan.md) · Previous: [Replay and comparison](phase-04-replay-comparison-code-trace.md)

Status: **accepted** — all P5-A1 through P5-A7 satisfied; 175 tests pass. See [acceptance validation](validation/phase-05.md).

## Objectives

### P5-O1: Add a persistent, observable market-data adapter

Implement `src/data/live_stream.py` behind an injectable stream interface. Run it independently of browser sessions and the finite backtest worker. Configure symbol/feed through validated settings; credentials remain in the server environment. Deduplicate subscriptions across tabs. Display feed identity, connection state, last received event, last finalized market timestamp, stale duration, and recovery state.

The SDK/API details and feed access must be verified against official documentation at implementation time. Entitlement failure is explicit; do not silently substitute a different feed. Monitoring displays bars and simulated signals. It does not submit broker orders.

### P5-O2: Define finalization, correction, and gap policies

Persist normalized bars keyed by symbol, feed, timeframe, bar start, and revision where supplied. Distinguish receipt time from market timestamp. Define a bounded reorder/finalization policy and configuration before coding strategy updates.

On disconnect, mark state recovering/stale, reconnect with bounded backoff, and backfill from the last durable finalized boundary with overlap. Merge overlap idempotently. Do not claim healthy current state until required gaps are resolved. Use a supported trading calendar to distinguish holidays, early closes, and actual missing bars.

For corrections to already-consumed bars, preserve the original event record and rebuild affected simulated state from a known checkpoint under a new revision. Label revised outputs; do not emit duplicate original alerts or silently rewrite history. For corrections still inside the reorder window, replace the provisional version before finalization. An unrecoverable gap produces an explicitly degraded session and suppresses new actionable simulated signals until policy permits recovery.

### P5-O3: Maintain incremental ORB state using shared rules

Introduce state operations such as `on_bar`, `snapshot`, and `end_session`. Track forming/frozen opening range, position state, trade count, and session boundaries. Reuse Phase 4 decision functions and Phase 1 execution assumptions. Do not send every unfinished session through the batch processor, which prunes incomplete opening ranges.

Persist state/checkpoints and consumed event identity atomically enough to prevent duplicate decisions after restart. On recovery, reconstruct from durable bars when checkpoint validity cannot be established. An incomplete session's state must never carry into the next session by accident.

### P5-O4: Integrate freshness and recovery into the UI

Add a separate market-monitor view with bounded recent bars, incremental range and simulated position, signal history, and connection controls. Keep historical runs and streaming state visually distinct. Pause rendering independently of data ingestion; disconnecting the browser should not stop the collector. Stopping the collector must actually release the subscription and persist its stopped state.

## Implementation sequence

1. Write finalization, gap, correction, calendar, and signal-revision policies; define fake-clock/fake-stream fixtures.
2. Implement persistent ingestion and storage without strategy updates; prove recovery/deduplication.
3. Add incremental state and compare to historical decisions using the same finalized event sequence.
4. Add freshness/recovery UI and bounded buffering.
5. Run deterministic failure tests and a recorded soak test; perform a separate opt-in provider smoke test if credentials/feed access are available.

## Tests, including edge and corner cases

| ID | Scenario | Required assertion |
|---|---|---|
| P5-T01 | Valid subscription; invalid credentials; unavailable feed; malformed symbol | Valid stream starts once; errors are explicit and redacted; no silent feed fallback. |
| P5-T02 | Two browser tabs, repeated connect, browser reload/close | One intended upstream subscription; collection survives UI lifecycle. |
| P5-T03 | Duplicate bars, out-of-order delivery inside/outside reorder window | Finalized sequence obeys policy; no duplicate state transition/alert; late data is handled explicitly. |
| P5-T04 | Network disconnect, reconnect overlap, partial/failed backfill, long outage | Idempotent recovery; missing ranges tracked; healthy indicator appears only after required recovery. |
| P5-T05 | Corrected provisional bar; corrected consumed OR bar; corrected entry/exit bar | Provisional data replaces cleanly; consumed corrections create labeled revised state; original alert history is retained. |
| P5-T06 | OR forming/freeze, no breakout, dual touch, trade limit, end of session | Incremental outcomes match historical processing for identical finalized inputs. |
| P5-T07 | Start collector mid-session with/without successful backfill | Reconstruct valid range/state before signals; unresolved history yields degraded state, not guessed range. |
| P5-T08 | Holiday, early close, DST change, midnight restart, missing final bar | Calendar-aware boundaries and exit policy; no false stale alert solely from closure; no position/state leaks to next session. |
| P5-T09 | Kill process between bar persistence and state commit; stale/corrupt checkpoint | Recovery consumes each logical event once or deterministically rebuilds; state/alert identities prevent duplicates. |
| P5-T10 | Empty stream, frozen market timestamps despite heartbeats, future timestamps, local clock jump | Freshness uses correct clocks; heartbeats alone do not imply fresh bars; invalid timestamps are quarantined. |
| P5-T11 | Burst at 10× expected selected-symbol bar rate; slow UI; storage failure | Buffers stay bounded; data is durably spooled or explicit backpressure/degradation occurs; no silent drop. |
| P5-T12 | Same deterministic dataset through batch and stream, multiple chunk sizes/restarts | Decisions, range state, fills, and final equity agree under the same revision/finalization policy. |
| P5-T13 | Pause display, stop collector, reconnect after stop | Display pause keeps ingestion running; stop releases subscription; restart resumes using recovery policy. |
| P5-T14 | Adapter spy for external calls and secret-bearing errors | Only market-data operations occur; no broker order calls; errors/logs/browser payloads contain no credentials. |

## Acceptance criteria

- **P5-A1:** Stream identity/access errors are visible; only one intended subscription exists across UI reruns (T01–T02).
- **P5-A2:** Duplicates, gaps, reconnects, late bars, and corrections follow the documented policy with no duplicated original decisions (T03–T05, T09).
- **P5-A3:** Supported session/calendar scenarios and mid-session startup are deterministic; degraded histories do not produce apparently valid current signals (T06–T08).
- **P5-A4:** Batch/stream parity passes for the same finalized revisioned input, including restart and different arrival chunks (T12). Comparing different correction histories is explicitly outside this equality claim.
- **P5-A5:** For the fake-clock fixture, stale state appears by the configured threshold plus one UI polling interval. Heartbeats do not reset bar freshness (T10).
- **P5-A6:** During a 60-minute synthetic soak at 10× expected rate, configured buffers stay within their limits, persisted accepted bars reconcile with input counts, and no unreported loss occurs. After warmup, process memory shows no sustained growth attributable to retained consumed events (T11).
- **P5-A7:** Pause/stop behavior is documented and verified, and market monitoring never calls an order API or leaks credentials (T13–T14).

## Evidence and handoff

Save `validation/phase-05.md` with policies, parity results, recovery traces, freshness thresholds, soak measurements, and supported calendar/feed combinations. Document provider smoke-test results separately with feed and date; if access is unavailable, explicitly mark real-provider verification outstanding rather than claiming production connectivity from fake-stream tests.

The final runbook must explain startup, shutdown, stale/recovering states, correction revisions, checkpoint recovery, and the distinction between simulated signals and broker positions.
