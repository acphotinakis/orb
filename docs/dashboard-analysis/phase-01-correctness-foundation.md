# Phase 1 — Correctness foundation

[Master plan](implementation-plan.md) · Next: [Results explorer](phase-02-results-explorer.md)

Status: proposed. Dependency: existing assessment and baseline test logs.

## Objectives

### P1-O1: Restore a trustworthy offline test baseline

Resolve duplicate `test_metrics.py` collection through a documented pytest import mode or unique module names. Repair the default-config expectation, validator call signatures, incomplete metric/config fixtures, and drawdown expectation identified in the verification summary. Determine intended contracts before editing assertions. Repair the synthetic OHLC fixture that currently loses 374 bars. Replace the CLI test's permissive “0 or 1” assertion with an offline success case and explicit failure cases using an injected source or seeded cache.

Restore the existing static plot export contract: `generate_plots=True` produces the documented PNG artifacts and `False` skips generation. Dashboard rendering will still use underlying data.

### P1-O2: Establish one effective configuration and request contract

Add a public `RunRequest` model and shared validation for CLI and future UI. Include dates, refresh, plot generation, run label, and log level in addition to `AppConfig`. Resolve the unused top-level YAML `parameters` section by migrating its intended values into this contract and documenting precedence. Choose and document canonical defaults; preserve explicit user configuration. Use one-minute bars as the dashboard's initial execution preset.

Require matching strategy/data symbols, real clock values, ordered valid dates, finite numeric inputs, real booleans, and supported timeframe/window combinations. Reject unsupported ATR, intrabar confirmation, and enabled filters with field-specific errors until implemented. Reject unknown override fields so typos cannot silently do nothing. Separate user labels from filesystem identities.

### P1-O3: Make caching and provenance correct

Either version processed data by all transformation inputs or cache strategy-independent normalized bars and derive flags per run. In both approaches include source data fingerprint, feed, timeframe, date bounds, and processing version in validity. Propagate raw refresh to dependent processing. New runs receive unique IDs; manifests include immutable dataset references, effective settings, source revision/fingerprint, dirty status, artifact checksums, and schema version.

Atomic file replacement protects readers; per-key synchronization protects shared cache writers. Do not claim multiple files are transactional because individual renames are atomic: publish the validated manifest/completion marker last. Paths derived from user input must remain inside configured storage, including through symlinks.

### P1-O4: Reconcile simulation accounting and time semantics

Reproduce the observed net-P&L/final-equity mismatch. Inspect fallback end-of-session liquidation, fees, and the final equity sample. Enforce `final_capital = initial_capital + sum(net_trade_pnl)` and agreement with final equity after flattening. Use independent, hand-calculated examples.

Record bar-start and decision-availability semantics without claiming a close was known at bar open. Keep conservative dual-touch behavior and temporal causality. Document the supported session calendar/timeframe assumptions; unsupported sessions or resolutions must fail clearly rather than silently produce misleading timing.

## Implementation steps and likely files

1. Reproduce and categorize baseline failures; establish isolated fixtures in `tests/conftest.py` and pytest collection configuration.
2. Add `src/services/run_models.py`; update `src/common/config.py` and `src/main.py` to share the request contract.
3. Update `src/common/paths.py`, `src/data/processor.py`, and `src/pipeline.py` for cache validity, refresh propagation, and immutable run artifacts.
4. Correct engine/report accounting and restore plot export calls in `src/pipeline.py`.
5. Add manifest and artifact serialization helpers; normalize unavailable/non-finite metrics at strict JSON boundaries without concealing their meaning.
6. Run the complete offline suite and record the new baseline.

## Tests, including edge and corner cases

| ID | Scenario | Required assertion |
|---|---|---|
| P1-T01 | Full suite collected in a fresh process | Both metrics test modules execute; prior failures are resolved, not skipped or weakened. |
| P1-T02 | CLI seeded-cache success and malformed request failure | Success exits 0 and writes valid artifacts without network; failure is nonzero with a useful field error. |
| P1-T03 | Config precedence and ignored YAML migration | Explicit overrides win; effective run dates/options are preserved in the manifest; unknown fields are rejected. |
| P1-T04 | Zero/negative/NaN/infinite numbers; strings used as booleans; invalid enum/time/date; reversed dates | Invalid input fails before storage creation or data fetch. Include `24:00:00`, impossible dates, whitespace-only symbols, and mismatched ticker/symbol. |
| P1-T05 | Same raw data, 15-minute then 30-minute range; changed exit time; IEX then SIP | Correct flags/data are rebuilt or separately keyed; no incompatible cache hit. |
| P1-T06 | Identical config with revised raw data; explicit refresh; corrupt or missing processed cache | Source changes invalidate results; refresh reaches processor; corruption is surfaced or rebuilt explicitly. |
| P1-T07 | Two writes to one cache key; process failure during each publication step | Reader sees a valid old or new dataset, never partial bytes; no false completion marker. |
| P1-T08 | Same label/config submitted twice; path traversal and symlink escape | Distinct run IDs preserve both runs; unsafe paths never escape storage. |
| P1-T09 | Hand-calculated long/short, stop/target, same-bar dual touch, EOD fallback, fees, zero shares | Net trade P&L, final cash/equity, and exit reason match independent expected results; no double-counted fee or fill. |
| P1-T10 | No trades, empty data, single trade, all wins/losses, flat equity | Defined empty/error behavior; no divide-by-zero crash; undefined ratios are explicit and strict JSON parses. |
| P1-T11 | Bar at OR boundary, missing OR bar, missing final bar, DST transition, coarse timeframe | No future bar enters OR; availability is after close; unsupported timing is rejected or handled by a documented policy. |
| P1-T12 | Plot generation enabled/disabled; valid synthetic fixture | True creates expected plots; False does not; valid fixture loses no bars unexpectedly. |
| P1-T13 | Same immutable inputs rerun; source edited between runs; shared dataset later refreshed | Results reproduce within declared tolerance; fingerprints change for edited code; old chart dataset remains unchanged. |

## Acceptance criteria

- **P1-A1:** The complete offline suite collects and passes, including repaired baseline tests and deterministic CLI coverage (T01–T02, T12).
- **P1-A2:** Every supported control has a documented effect and invalid/unsupported requests fail before side effects (T03–T04).
- **P1-A3:** All cache invalidation, refresh, corruption, and publication scenarios preserve correct data identity (T05–T08, T13).
- **P1-A4:** Final flattened equity and capital reconcile with net P&L within $0.01 for currency fixtures; underlying calculations use a stricter documented floating-point tolerance (T09–T10).
- **P1-A5:** Temporal tests pass and timestamp semantics are documented; no claim of broader calendar/timeframe support exceeds tested behavior (T11).
- **P1-A6:** Each completed run has a validated manifest and immutable artifacts sufficient to reproduce its results (T08, T13).

## Evidence and handoff

Save the selected contracts, test commands/results, and accounting root-cause explanation in `validation/phase-01.md`. Deliver a clean synthetic completed run for Phase 2, plus zero-trade and intentionally corrupt-artifact fixtures. Do not use actual credentials or live downloads to generate required fixtures.
