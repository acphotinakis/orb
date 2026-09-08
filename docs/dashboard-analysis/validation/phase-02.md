# Phase 2 validation — Results explorer

Date: 2026-09-08. Scope: P2-O1..O4 (read-only browsing). No execution,
submission, or streaming work. Entry gate was Phase 1 acceptance (87 green).

## Setup (clean environment, no secrets/network after install)

```
.orb_venv/bin/pip install -r requirements-dashboard.txt  # streamlit==1.63.0, plotly==7.0.0 (pinned after validating)
.orb_venv/bin/python -m src.dashboard.demo --root demo_runs   # ~seconds, real pipeline on synthetic bars
.orb_venv/bin/streamlit run src/dashboard/app.py              # enter demo_runs in the sidebar
```

Base `requirements.txt` is unpinned and untouched; dashboard pins live in
`requirements-dashboard.txt`. The install downgraded `websockets` 17.1 → 16.1.1
(Streamlit dependency); the full suite still passes (third-party warning only).

## What was built

- `src/services/artifact_store.py` (+read-only): `discover_runs`
  (complete/corrupt/legacy with per-dir reasons, never raising),
  `read_run_artifacts` (checksum-validated CSV/JSON/YAML readers, exact
  run-ID match, no fallback values), `read_session_bars` (selected-session
  OHLCV via the manifest dataset ref), `read_download_bytes` (5-file
  allowlist, bytes preserved), `to_et_display` (ET with numeric offset),
  `display_value` (`∞`/`n/a`, never zero-for-unavailable),
  `is_synthetic_run` (label badge).
- `src/dashboard/charts.py` (Plotly, Streamlit-free): equity+drawdown,
  session candlestick with frozen-range and entry/exit/stop/target markers,
  R-multiple histogram; empty inputs yield annotated figures, never crashes;
  `downsample_for_display` (≤5000 pts, extrema preserved).
- `src/dashboard/demo.py`: `generate_demo` seeds 3-day mixed (TP/STOP/EOD)
  + 2-day zero-trade datasets and runs the real pipeline offline;
  `python -m src.dashboard.demo --root demo_runs`.
- `src/dashboard/app.py`: run selector, synthetic badge, metadata with
  dataset/source fingerprints, full-run metric cards with units, equity
  chart with text summary, filterable trade table (500-row bound, explicit
  filtered-scope caption), session explorer with explicit trade selector and
  text-only detail pane, R distribution, allowlisted downloads, empty state
  with the exact demo command, unsupported-runs expander. No `st.markdown`
  or `unsafe_allow_html` anywhere (user content renders as text).

## Boundary evidence (P2-T08)

With `BacktestEngine.run`, `DataFetcher.fetch_and_cache`, and
`ORBPipeline.run` all rigged to raise, every browse operation (discovery,
read, session bars, all three chart builders, downloads) completes without
touching them. `app.py` imports neither engine nor fetchers.

## Test results

`tests/dashboard/test_results_explorer.py`: 21 tests covering T01 (empty
root, offline demo), T02 (metric/row/series parity incl. chart inputs), T03
(trade→session sync, run switch reset), T04 (zero-trade + empty figures +
`∞`/`n/a` rendering), T05/T06 (truncated CSV, checksum tamper → corrupt with
healthy runs usable, legacy, disappearing run, absent dataset), T07
(ET-0500/ET-0400 labels, session linkage), T08 (read-only, above), T09
(traversal, download allowlist + byte equality, evil-label text round-trip,
no-executable-HTML source check), T10 (100k-row/10k-trade bounds with
extrema preserved + full-resolution bytes), T12 + UI (AppTest: filtered-scope
caption with full-run cards intact, run switch 3→0 trades, empty state).

Full suite: **108 passed, 0 failed** (87 prior + 21 new), default mode.
T11 (keyboard/narrow-layout walkthrough) is manual: all controls are native
Streamlit widgets (keyboard-operable, visible focus) and every chart ships a
text summary; a narrow-viewport pass remains a manual runbook step, as do
screenshot captures (AppTest state assertions stand in for normal/empty/
error renders in this record).

## Timings (dev machine, demo fixtures)

Warm run selection (read+validate) 0.003s; session read+figure 0.036s;
equity figure 0.008s; discovery 0.001s — all far inside the 2s P2-A5 budget.
Budgets for the 100k/10k fixture were verified structurally (bounded traces,
checksum-equal downloads); replace with measured browser timings before any
budget change.

## Acceptance mapping

P2-A1 (offline demo) ✓, P2-A2 (parity/units/timezones) ✓, P2-A3 (corrupt/
partial/legacy/disappearing) ✓, P2-A4 (read-only + storage containment) ✓,
P2-A5 (bounded rendering, exports intact) ✓, P2-A6 (keyboard-capable widgets
+ text summaries; manual narrow pass outstanding, non-blocking) ✓.

No credentials used; no network access; no simulation or market-data calls
from any browsing path. Phase 3 may begin (entry gate: this record).
