# Phase 3 validation — Run control and progress

Date: 2026-09-08. Scope: P3-O1..O4 (submission, single worker, progress,
cancel, recovery) + minimal Run Control UI. No replay/streaming work.

## Lifecycle (implemented in `src/services/registry.py`)

```
submit(token) → queued ──claim──▶ running ──cancel──▶ cancelling ──ack──▶ cancelled
                  │                   │  │                │  ├─error─▶ failed
                  │                   │  │                └─lost race after publish → cancelled
                  │                   │  └─finish──▶ succeeded (manifest proof required)
                  │                   └─error/dead-worker──▶ failed
                  └─cancel──▶ cancelled (worker never launches)
```

All transitions are single-statement compare-and-swap transactions; exactly
one terminal outcome commits (P3-T06 proven by race tests). Terminal states
are immutable passthroughs.

## What was built

- **Hooks (CLI-safe):** `BacktestEngine.run(progress, cancel_requested)` —
  session boundary events, 64-bar cancel polling, `cancelled` result flag,
  telemetry failures never break the engine. `ORBPipeline.run(on_event,
  cancel_requested)` — `run_started`/`stage_started`/`run_completed` events,
  per-stage cancel boundaries raising `CancelledRun` (new, in
  `exceptions.py`), engine events enriched with stage. `None` hooks preserve
  CLI behavior bit-for-bit (existing e2e/CLI tests untouched and green).
- **Registry (`registry.py`):** SQLite WAL store at `<root>/runs/registry.sqlite`;
  tokenUNIQUE idempotency (retries return the run, reruns mint tokens);
  claim-only-if-free single-worker discipline; owner-checked adopt/release;
  per-run monotonic event journal (unparseable tails skipped, never crash);
  cursor reads; redaction of credential-like content on every error/event
  write; reconcile (with zombie reaping) marks dead-pid+stale-heartbeat runs
  failed without rerunning anything.
- **Worker (`worker.py`, `python -m src.services.worker <root> <run_id>`):**
  rebuilds immutable inputs from the registry, streams events, polls cancel,
  publishes only on the uninterrupted path, terminalizes every outcome, and
  hands the freed slot to the next queued run (best-effort; crashes defer to
  the next supervisor call). Refuses non-running runs (no silent reruns).
- **Service (`run_service.py`):** submit/get/list/events/bounded log tail/
  cancel + `ensure_supervisor` (reconcile → claim → structured subprocess
  spawn, never shell-built). Safe on every UI interaction.
- **UI (`app.py` Run control):** validated submit form (shared RunRequest
  contract, retry-safe token, blank-means-default), 1s display-only poll
  fragment, cancel button, reconnect-by-run-ID field. Form edits never touch
  active runs (workers snapshot inputs at launch).

## Bugs found by tests (not test artifacts)

1. **No completion handoff:** queued runs after an active one never dispatched
   (test stuck in `queued`). Workers now hand the slot off on exit.
2. **Supervisor-PID lease:** claim recorded the supervisor pid, so dead
   workers never reconciled. Claim leaves pid NULL; the supervisor adopts the
   real child pid post-spawn (ownership-checked).
3. **Zombie liveness:** SIGKILLed children answer `kill(pid, 0)` until
   reaped. `reconcile` reaps before checking.
4. **Reporter vs `"inf"` payoff** (P1 follow-through): console format crashed
   on all-win runs; `_fmt_ratio` renders the JSON-safe convention.

## Evidence (18 run-control + 1 UI submit-flow tests)

T01 snapshot/idempotency/rerun · T02 retry-same-token + claim discipline ·
T03 registry claim unit + FIFO handoff in integration · T04 restart-new-
instance state + event replay/dedup/corrupt-tail · T05 queued-cancel (never
launches), stalled-fetch running-cancel (deterministic), terminal passthrough ·
T06 CAS race matrix · T07 SIGKILL reconcile without rerun, queued-next
dispatch · T08 live-restart no-duplicate, stale-lease dead-pid + owner checks ·
T09 redaction (errors, events, labels-as-data) + bounded log tail · T10
storage-as-file failure (explicit failed, never success) · T11 covered in
T09/T05 · T12 registry→manifest revision flow · T13 no-hook parity ·
T14 slow-fetch responsiveness (status <0.5s during 3s block, then cancelled).

Full suite: **127 passed, 0 failed**, default mode.

## Measured responsiveness (local fixture, P3-A4)

Status reads <0.02s (budget 0.5s) · submit+dispatch <2s · first persisted
progress <2s (deadline-polled) · CPU-cancel ack **0.49s** (budget 2s).

## Limitations (explicit)

- Cancellation during a blocked provider call waits for that call to return
  or time out, followed by the next cancellation check. No measured upper
  bound for blocked-network acknowledgement has been established. The CPU
  cancellation evidence and passing test counts do not establish that bound;
  provider timeout/retry behavior requires separate controlled validation.
- No background supervisor thread: dispatch happens on submit/cancel/UI
  poll/worker handoff. A crashed worker with an empty queue waits for the
  next supervisor call (documented; matches local single-user scope).
- One active worker; queue order is FIFO by submission time.

No credentials used; no network access. Phase 4 may begin (entry gate:
this record + green suite).
