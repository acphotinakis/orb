# Phase 1 validation — P1-O1: trustworthy offline test baseline

Date: 2026-09-08. Scope: P1-O1 only (baseline reproduction + focused accounting
regression). P1-2..P1-6 (RunRequest, cache identity, manifests, calendar policy)
are not implemented here.

## Baseline reproduced (before fixes)

- Default mode: collection interrupted — `tests/unit/test_metrics.py` and
  `tests/evaluation/test_metrics.py` collide (`import file mismatch`).
- Importlib mode: 21 passed, 12 failed (config target_r; validator `timeframe`
  x4; metrics `slippage_paid` x4; drawdown; report `AppConfig`; pipeline plots).

## Intended-contract decisions (no weakened assertions)

| Failure | Decision | Rationale |
|---|---|---|
| `test_metrics.py` collision | Added `tests/**/__init__.py` package markers | Qualified names (`tests.unit` vs `tests.evaluation`); default-mode collection now works |
| `target_r == 2.0` vs YAML `1.25` | Test now asserts `1.25` | Checked-in YAML is the deliberate default; review forbids changing target R (D3) |
| Validator `timeframe` TypeError x4 | Tests pass `timeframe="1Min"` | Implementation (gap audit needs resolution) is the newer contract |
| Metrics `slippage_paid`/`commission_paid` KeyError x4+1 | Fixtures carry zero columns; report test builds full `AppConfig` | Engine ledger (`BacktestEngine.run`, `models.Trade`) always writes these columns |
| Drawdown `10.0 / 10000.0 / 2` | Test now `25.0 / 30000.0 / 4` | Running-peak definition (peak 120k idx2 → trough 90k idx5; dd>0 idx3–6), verified empirically |
| `profit_factor`/`payoff_ratio` float `inf` | Tests assert `"inf"`; `payoff_ratio` implementation aligned to `profit_factor` convention | `"inf"` strings are the deliberate strict-JSON convention (also sortino/calmar); payoff previously returned `0.0` for no-loss runs, inconsistent with `profit_factor` two lines above |
| Mixed `median_r 1.5` / `expectancy_r 0.5` | Now `0.5` / `0.75` | numpy median of [2,-1,4,-2]; documented `E_R = W*avg_win-(1-W)*avg_loss`; old comment's own arithmetic said 0.75 |
| Scratch `profit_factor 2.0` / `median 2.0` / `expectancy 1.0` | Now `3.0` / `1.0` / `0.5` | 300/100; median of [-2,0,2,4]; documented formula `0.5*3.0-0.5*2.0` (old comment weights summed to 0.75, not 1.0) |
| `equity_curve.png` missing | Restored all 4 commented plot blocks (D1) | Plotter APIs and `PathManager` dirs unchanged; verbatim restore |
| Reporter `:.2f` crash on `"inf"` payoff | `_fmt_ratio` renders `"inf"` as-is | Found via order-dependent CLI failure (all-win run); `profit_factor` line already used `{}` safely |
| CLI `0-or-1` assertion | `main(argv=None)` + injected-source success (exit 0, artifact asserts) + missing-config and bad-timeframe failures (exit 1) | No network; isolated cwd; in-process invocation |

Comparison rule applied throughout (P1-A4): full-precision internals with tight
tolerance (`pytest.approx`), currency reconciliation within $0.01.

## U1 accounting — cause established by regression

`tests/unit/test_accounting_reconciliation.py` (hand-calculated, fixed 100-share
fixture: entry fill 501.51, EOD exit fill 501.49, gross -2.00, slippage 2.00,
commission 0.70, net -4.70):

- Pre-fix failure: last in-loop equity `99999.00` vs final capital `99995.30`
  (gap $3.70) — isolates `BacktestEngine` end-of-session fallback
  (`engine.py`): the fallback close updates capital AFTER the final per-bar
  equity record.
- Minimal fix: append an explicit flattened equity record after the fallback
  close. Existing engine/execution tests still pass.
- U1 status: cause confirmed by the above isolation; synthesis text unchanged
  (kept open per instruction until this test established it).

Pipeline-level confirmation (throwaway probe, 3-day synthetic run mirroring the
e2e fixture): `total_pnl_dollars 97.75` (same figure as the original observation)
vs ending equity `100097.75` → reconcile gap `0.0` (was `+7.36` at `100105.11`).
11 PNGs written including `equity_curve.png` and 3 trade charts;
`metrics.json` strict-parses.

Validator note: clean synthetic input validates 782/782 with 0 removals; the
previously observed 374-bar drop did not reproduce on the current fixture.
Malformed-bar removal is covered by dedicated validator unit tests
(duplicates, inverted OHLC, strict-mode gate).

## Final result

Command: `MPLCONFIGDIR=/private/tmp/orb-dashboard-mpl .orb_venv/bin/python -m pytest tests -q`
Result: **37 passed, 0 failed, 1 warning** (websockets legacy deprecation, third-party).
Default collection mode (no `--import-mode` flag) — the original collision is gone.

Files changed: `src/backtest/engine.py`, `src/evaluation/metrics.py`,
`src/evaluation/reporter.py`, `src/main.py`, `src/pipeline.py`,
`tests/unit/test_config.py`, `tests/unit/test_validator.py`,
`tests/evaluation/test_metrics.py`, `tests/integration/test_pipeline_e2e.py`;
added `tests/**/__init__.py` (x5), `tests/unit/test_accounting_reconciliation.py`.

No credentials used; no network access; no live orders. P1-2…P1-6 (RunRequest, cache identity, manifests, calendar policy) remains open.

---

# P1-2 record — RunRequest and shared validation (appended 2026-09-08)

Scope: P1-O2 only. New module `src/services/run_models.py` is the single entry
point (`build_run_request`) for CLI / dashboard / worker.

## What was built

- `RunRequest` (frozen): effective `AppConfig` + `start_date`/`end_date`,
  `refresh_cache`, `generate_plots`, `run_label`, `log_level`.
- YAML `parameters:` migration as *defaults* (explicit section values and
  explicit caller values win): `symbol` → ticker+symbol defaults, `feed` /
  `is_paper` → data defaults, dates/refresh/plots/log_level → option defaults.
  Unknown `parameters` keys, override paths, and option keys raise
  `ConfigurationError` (typo safety). `None` options never clobber migrated
  defaults.
- Request checks beyond `validate_config`: ticker==symbol (non-blank),
  real-clock `force_exit_time` (`24:00:00` rejected), `intrabar` / `atr` /
  enabled filters rejected with field-specific errors, intraday-only
  timeframes with OR-multiple rule, ordered real calendar dates, strict
  bool/log-level/label rules, finite non-bool numerics in overrides.
- `RESEARCH_PRESET_1MIN` constant (dashboard preset; checked-in YAML default
  unchanged). `run_label` is label-only; CLI forwards `--run-id`
  transitionally until P1-O3 UUIDs split labels from filesystem identities.
- CLI now builds through `build_run_request`; `--paper`/`--no-paper` pair with
  default `None` (explicit choice only, YAML otherwise preserved);
  `log_level` forwarded into `pipeline.run` (previously dropped);
  `main(argv=None)` for in-process use.

## Evidence

- `tests/services/test_run_models.py`: 16 tests (T03 precedence/migration/
  preset/preserved options; T04 all rejection classes incl. `24:00:00`,
  `2024-02-30`, reversed dates, NaN/inf, string bools, `1Day`/`30Min` combos,
  each unsupported control with field assertion).
- Full suite: **53 passed, 0 failed** (37 P1-O1 + 16 P1-2), default mode.
- CLI smoke: `--help` lists the pair; `--timeframe BogusTF` exits 1 with a
  field-specific `ConfigurationError`.

P1-O3/O4 remaining: cache identity + immutable manifests, then time/calendar
contract. No credentials used; no network access.

---

# P1-3/P1-4 record — cache identity + immutable manifests (appended 2026-09-08)

Scope: P1-O3 only (P1-O4 time/calendar still open). Implemented together per
plan: identity determines the dataset a manifest references.

## What was built

- `src/services/artifact_store.py` (new): `fingerprint_dataframe` (canonical
  content sha256), `dataset_identity` + `identity_short_hash` (all
  content-affecting inputs: source fingerprint, feed, symbol, timeframe, date
  bounds, processing version, OR minutes, force-exit), `source_code_fingerprint`
  (git rev + dirty, best-effort, never raises), atomic parquet/text/bytes
  writers (temp sibling + `os.replace`), per-key `threading.Lock` registry,
  `ensure_safe_component` / `ensure_within_root` (symlink-resolving),
  `build_manifest` / `validate_manifest` / `publish_manifest_last` /
  `is_run_complete` (manifest.json with status `succeeded` is the sole
  completion marker; checksums verified on every validation).
- `DataProcessor`: identity-gated cache (sidecar `<file>.identity.json` must
  match exactly or it rebuilds; no identity → always rebuilds, fail-safe),
  atomic save (parquet first, sidecar second — a crash between them forces a
  rebuild, never a false hit), whole check-build-write under the per-key lock,
  `force_refresh` honored. `PROCESSING_VERSION = "1"` bumps invalidate.
- `PathManager.processed_dir_for_identity`: immutable directories
  `{symbol}/{timeframe}_{dateslug}_{hash}/` (D2). Legacy date-only property
  kept, marked deprecated. Different inputs → different directories (no
  incompatible hit possible); same inputs → shared byte-stable directory, so
  old runs keep referencing unchanged data after later refreshes.
- `ORBPipeline.run`: UUID run IDs when `run_id` is `None` (explicit IDs
  validated filesystem-safe, rejected before side effects); refresh
  propagates raw→processed; identity computed from validated bars;
  manifest published LAST after checksum enumeration of `results/`, `plots/`,
  and the config snapshot, including `accounting.reconcile_gap` evidence.
  New `run_label` param (label-only). CLI `--run-id` defaults to `None`.

## Real bug found by T13 (not a test artifact)

`fingerprint_dataframe` hashed object-dtype columns via `tobytes()`, which
hashes string-object *pointers*: fresh vs parquet-roundtripped frames with
identical content produced different digests, so identical reruns never shared
a dataset. Fixed with deterministic length-prefixed UTF-8 content encoding.
T13 (identical-rerun identity equality) now guards it.

## Evidence

- `tests/services/test_cache_identity.py` (7): OR/exit/feed changes rebuild
  or rekey (T05), revised-raw invalidation, refresh propagation, corrupt
  parquet/sidecar recovery, missing build, 8-thread same-key validity (T06/T07).
- `tests/services/test_run_manifests.py` (8): no-manifest/corrupt-manifest
  incompleteness, tamper detection, publish-refuses-bad-payload (T07),
  run-ID traversal + symlink escape rejection (T08), single-cell fingerprint
  sensitivity (T13), provenance shape, atomic-write hygiene.
- `tests/integration/test_identity_manifests.py` (6): same-label UUID
  distinctness + completion, pipeline-level traversal rejection, rerun metric
  equality with shared identity dir, old-run byte/metrics stability after an
  OR-30 run, manifest content contract incl. `reconcile_gap ≤ 0.01`.
- Full suite: **74 passed, 0 failed** (53 prior + 21 new), default mode.

P1-O4 remaining: time/calendar contract (P1-T11/P1-A5). No credentials used;
no network access.
