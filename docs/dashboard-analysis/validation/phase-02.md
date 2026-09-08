# Phase 2 validation — Results explorer

Date: 2026-09-08. Scope: P2-O1..O4 (read-only browsing). No execution,
submission, or streaming work. Entry gate was Phase 1 acceptance (87 green).

## Setup (clean environment, no secrets/network after install)

```
.orb_venv/bin/pip install -r requirements-dashboard.txt  # streamlit==1.63.0, plotly==7.0.0 (pinned after validating)
.orb_venv/bin/python -m src.dashboard.demo --root demo_runs   # ~seconds, real pipeline on synthetic bars
.orb_venv/bin/streamlit run src/dashboard/app.py  # enter demo_runs in the sidebar
```

Launch fix: the app now adds the repository root resolved from its own file
to `sys.path` before importing project modules. The earlier browser verification
required `PYTHONPATH=.`; that workaround is no longer necessary.

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

## P2-A6 browser verification (real Chromium, Playwright 1.62.0)

Harness is verification-only (NOT added to `requirements-dashboard.txt`).
Evidence: `validation/phase-02-screenshots/` (`normal-wide.png`,
`empty-state.png`, `narrow-390px.png`, `keyboard-focus.png`,
`walkthrough.json`).

- **Normal state (1440px):** 8 metric cards, synthetic badge, dataset/source
  fingerprints, equity text summary all render; screenshot archived.
- **Keyboard-only walkthrough:** 14 consecutive Tabs from the page body land
  on INPUT, BUTTON, and link (A) elements only — every control (storage
  root, run selector, filters, session/trade selectors, downloads) is a
  native keyboard-focusable widget; no custom HTML/JS components exist in
  the app (also asserted in-suite: no `unsafe_allow_html`, no `st.markdown`).
  Streamlit's default theme provides the visible focus ring; the focus
  screenshot shows the active control highlighted. Full flow (root → run →
  filter → session → trade → download) is reachable without a pointer;
  selectbox values change via arrows+Enter (AppTest asserts the resulting
  state changes in-suite).
- **Narrow layout (390px):** page stacks vertically with **0px horizontal
  overflow** (measured `scrollWidth - innerWidth`); metric cards, tables,
  and charts remain reachable by vertical scroll; screenshot archived
  (full-page).
- **Empty state:** invalid root renders the guidance text with the exact
  demo command; screenshot archived.

## Acceptance mapping (final)

P2-A1 (offline demo) ✓, P2-A2 (parity/units/timezones) ✓, P2-A3 (corrupt/
partial/legacy/disappearing) ✓, P2-A4 (read-only + storage containment) ✓,
P2-A5 (bounded rendering, exports intact) ✓, P2-A6 (keyboard walkthrough +
narrow layout + screenshots, this section) ✓.

No credentials used; no network access; no simulation or market-data calls
from any browsing path.

**Phase 2 is fully accepted.** Phase 3 may begin (entry gate: this record).

## Timings (dev machine, demo fixtures)

Warm run selection (read+validate) 0.003s; session read+figure 0.036s;
equity figure 0.008s; discovery 0.001s — all far inside the 2s P2-A5 budget.
Budgets for the 100k/10k fixture were verified structurally (bounded traces,
checksum-equal downloads); replace with measured browser timings before any
budget change.

**Phase 2 is fully accepted.** Phase 3 may begin (entry gate: this record).
