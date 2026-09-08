# Phase 3 — Run control and progress

[Master plan](implementation-plan.md) · Previous: [Results explorer](phase-02-results-explorer.md) · Next: [Replay and comparison](phase-04-replay-comparison-code-trace.md)

Status: proposed. Entry gate: Phases 1–2 accepted.

## Objectives

### P3-O1: Submit immutable, validated work explicitly

Add a form backed by `RunRequest`; show effective settings and submit only on the Run action. Assign a request token to each deliberate submission. The service atomically maps a token to one run ID; retries return that ID, while a deliberate rerun gets a new token. Invalid requests do not enqueue work. Form edits affect the next run, not an active one.

### P3-O2: Isolate execution and persist its lifecycle

Implement `run_service.py`, `worker.py`, and durable registry storage. One supervisor owns queue dispatch and permits one active worker process across browser sessions. Launch workers with structured arguments and fixed entry points, not shell-built commands. Persist config/source/data references before launch; each worker imports a consistent source snapshot or uses a documented source-stability mechanism for its entire run, including lazy imports.

Use states `queued`, `running`, `cancelling`, `cancelled`, `succeeded`, and `failed`. Allow queued→cancelled, running→cancelling→cancelled, running→succeeded/failed, and cancelling→failed for genuine errors. Resolve completion/cancel races transactionally: whichever terminal result is committed first remains terminal; later requests report that result rather than rewriting history. Worker crash/restart reconciliation must not launch a second copy of a still-live worker.

### P3-O3: Publish progress without blocking the interface

Add versioned events at pipeline stages and engine session boundaries. Persist monotonic sequence numbers, stage, counts, timestamp, and errors. Poll the service about once per second from the UI. Distinguish indeterminate stages from measurable session progress; do not invent an overall percentage from uneven stages. Logs are bounded in the UI and remain downloadable as an allowlisted artifact.

### P3-O4: Cancel and recover predictably

Check cancellation between stages and sessions, and at bounded intervals inside long bar loops. Data requests have finite timeouts. Queue cancellation prevents worker launch; running cancellation leaves an explicit incomplete run. Failure during export cannot publish success. Handle supervisor restart, orphan workers, malformed event tails, disk-write failures, and retryable reads without silently rerunning a job.

## Implementation sequence

1. Define registry transactions, token uniqueness, ownership/lease rules, and legal state transitions.
2. Add worker execution around the shared validated service with per-run logging.
3. Instrument pipeline/engine callbacks while preserving CLI operation when no callback is supplied.
4. Add submit/status/cancel UI and reconnect-by-run-ID behavior.
5. Add failure injection and supervisor recovery tests before enabling unattended runs.

## Tests, including edge and corner cases

| ID | Scenario | Required assertion |
|---|---|---|
| P3-T01 | Submit valid form; change slider afterward | One run uses the submitted snapshot; draft edits do not change active config. |
| P3-T02 | Double-click, request retry, two tabs use same token; deliberate rerun | One run/worker for a token; explicit rerun has a new ID. |
| P3-T03 | Two sessions submit different jobs simultaneously | Registry retains both; at most one active worker; queue order follows documented policy. |
| P3-T04 | Poll, reload browser, close/reopen tab, disconnect UI | Job continues independently; run ID restores progress/results; no duplicate launch. |
| P3-T05 | Cancel while queued, fetching, in bar loop, exporting, already terminal | State follows policy; queued job never launches; incomplete work never appears as success; terminal state is stable. |
| P3-T06 | Cancel/completion transaction race repeated under controlled scheduling | Exactly one terminal outcome is committed; UI reflects it and does not oscillate. |
| P3-T07 | Kill worker before start, mid-calculation, after artifacts but before final status | Supervisor detects failure/reconciles publication; no orphan forever-running state or silent retry. |
| P3-T08 | Restart supervisor with live worker; stale lease; worker PID reused | Ownership checks prevent duplicate dispatch and avoid trusting PID alone; recovery policy is deterministic. |
| P3-T09 | Event polling repeats/out-of-order reads, truncated journal tail, sequence gap | Client deduplicates by sequence, detects gaps, and recovers from authoritative status; malformed tails do not crash UI. |
| P3-T10 | Disk full, permission denied, corrupt registry response, network timeout | Explicit bounded failure; no success without validated outputs; actionable redacted error. |
| P3-T11 | Credential-like exception content, unsafe labels/path strings, large logs | Secrets are redacted; no arbitrary command/path execution; UI log payload stays bounded. |
| P3-T12 | Edit source during run and launch another afterward | First run remains on captured code; second records changed fingerprint and uses new code. |
| P3-T13 | Normal CLI execution with callbacks omitted | Results remain equivalent; no dashboard service required by CLI. |
| P3-T14 | Slow injected data provider and long synthetic computation | UI status/cancel remain interactive; request timeout bounds cancellation during blocked I/O. |

## Acceptance criteria

- **P3-A1:** Submission is validated and idempotent per request token; all accepted jobs have durable identities and immutable inputs (T01–T03, T12).
- **P3-A2:** One active worker is enforced across tabs and supervisor recovery, with no unintended reruns (T03–T04, T07–T08).
- **P3-A3:** Lifecycle and cancellation races produce one stable terminal result; success requires valid published artifacts (T05–T10).
- **P3-A4:** Under the recorded local fixture, status reads complete within 500 ms and new persisted progress appears within 2 seconds. CPU-loop cancellation is acknowledged within 2 seconds; blocked network cancellation is bounded by the configured request timeout plus 2 seconds (T14).
- **P3-A5:** Event replay and log display tolerate repeated reads and bounded corruption; errors do not expose secrets (T09–T11).
- **P3-A6:** CLI regression and earlier-phase suites pass; browsing results still does not launch work (T13 and Phase 2 T08).

## Evidence and handoff

Save `validation/phase-03.md` with lifecycle diagram/table, race-test outcomes, recovery commands, timeout settings, and measured responsiveness. This is the first runnable historical research release: configure → submit → observe → inspect → download. Record limitations of a single local worker in the runbook.
