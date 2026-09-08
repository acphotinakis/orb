# Phase 2 — Results explorer

[Master plan](implementation-plan.md) · Previous: [Foundation](phase-01-correctness-foundation.md) · Next: [Run control](phase-03-run-control.md)

Status: proposed. Entry gate: Phase 1 accepted; stable manifest, data, metric, and plot contracts available.

## Objectives

### P2-O1: Provide an offline, useful first launch

Create `src/dashboard/app.py`, a dedicated tested dashboard dependency file, and documented launch instructions. Provide a deterministic demo-generation command using the real pipeline and valid synthetic data. Label demo runs as synthetic. A repository with no runs displays setup/demo guidance instead of blank charts or an exception.

### P2-O2: Browse completed experiments without recalculation

Implement a read-only artifact service and run selector using manifests. Display effective config, date range, data/source fingerprint, completion time, and metrics with explicit units. Legacy directories without manifests must be identified as unsupported or handled through an explicit validated import flow; never silently guess their provenance.

Render equity, drawdown, and trade distributions from existing tables. Add a searchable/filterable trade table, session selector, candlestick chart, frozen range, entry/exit markers, and stop/target levels. Selecting a trade must keep its session and detail pane synchronized. Full-session results may show the completed range; replay-specific causality arrives in Phase 4.

### P2-O3: Make presentation accurate and resilient

Use Plotly figure builders in `src/dashboard/charts.py`; retain explicit table selection as the reliable trade-to-chart interaction. Preserve timezone-aware values and format display times in ET with an offset or unambiguous timezone label. Show unavailable metrics distinctly from zero. Keep full-run metrics separate from filtered table statistics, and label filtered statistics if provided.

Load completed immutable artifacts by run identity/checksum. Bound table rendering and request only selected-session OHLCV. Downsample display-only long equity curves if needed, preserving important extrema; all calculations and downloads use the original full-resolution series.

### P2-O4: Support safe, accessible exploration and exports

Download only allowlisted manifest artifacts, preserving their content and names. Render labels/log-like text as text rather than executable HTML. Provide keyboard-operable selectors, visible focus, textual chart summaries, clear loading/empty/error states, and distinctions that do not depend only on red/green color.

## Implementation sequence

1. Add manifest discovery and artifact readers with schema/checksum validation.
2. Add offline demo generation and fresh-start instructions.
3. Build run metadata/metrics, portfolio charts, trade table, and session explorer in that order.
4. Add downloads, stale-selection recovery, and malformed-artifact handling.
5. Validate end-to-end user workflows and record local rendering measurements.

Likely files: `src/dashboard/app.py`, `charts.py`, `views/results.py`, `src/services/artifact_store.py`, `requirements-dashboard.txt`, and `tests/dashboard/`.

## Tests, including edge and corner cases

| ID | Scenario | Required assertion |
|---|---|---|
| P2-T01 | Fresh checkout, no credentials, no runs | App launches; empty state explains demo creation; generated demo opens without network. |
| P2-T02 | Known completed run | Every headline metric, trade row, timestamp, and chart series matches fixture artifacts with declared rounding/units. |
| P2-T03 | Trade filter then selection; switch run/session; selection disappears | Selection points to the correct trade/session or resets explicitly; no stale details from the prior run. |
| P2-T04 | Zero trades, one bar, flat equity, one session, all losses, unavailable ratio | Useful empty plots/tables; no crashes; unavailable is not rendered as zero or a misleading percentage. |
| P2-T05 | Missing/truncated CSV, invalid JSON, checksum mismatch, unknown schema, absent dataset | Run receives a specific unavailable/corrupt state; other runs remain usable; no fabricated fallback values. |
| P2-T06 | File removed between listing and selection; incomplete run folder | Controlled error/reload guidance; incomplete artifacts are not treated as completed results. |
| P2-T07 | Winter/summer timestamps, UTC inputs, explicit offsets, session boundary | Correct ET display and session linkage; no timezone stripping or accidental date shift. |
| P2-T08 | Repeated zoom/filter/select operations with engine/fetcher instrumented | Zero simulation calls, data-source requests, or output mutations. |
| P2-T09 | Artifact traversal, symlink escape, HTML/script-like run label, formula-like CSV text | Files remain allowlisted and text is not executed; export format/content policy is documented, including spreadsheet interpretation of text. |
| P2-T10 | Large synthetic run: 100,000 equity rows, 10,000 trades | Paginated/bounded table; selected session loads separately; full-resolution downloads match checksum; no unbounded browser payload. |
| P2-T11 | Keyboard navigation, narrow viewport, color-vision-independent reading | Core selectors/downloads work by keyboard; focus visible; textual trade direction and summaries remain understandable. |
| P2-T12 | Filtered table alongside full-run metric cards | Scope is explicit; filtering does not silently change or mislabel full-run metrics. |

## Acceptance criteria

- **P2-A1:** Documented local setup and synthetic demo work from a clean environment without secrets/network data access after dependencies are installed (T01).
- **P2-A2:** Data/metric/selection parity holds across normal and empty cases, with clear unit and timezone labels (T02–T04, T07, T12).
- **P2-A3:** Corrupt, partial, legacy, or disappearing artifacts produce actionable errors without breaking discovery of healthy runs (T05–T06).
- **P2-A4:** Browsing is read-only, and artifact lookup cannot escape the storage root (T08–T09).
- **P2-A5:** On the recorded development machine, warm run selection and selected-session rendering each complete within 2 seconds for T10; display sampling does not change exports or metrics.
- **P2-A6:** A keyboard-only walkthrough can select a run/trade and download results; narrow layouts preserve essential controls (T11).

## Evidence and handoff

Save `validation/phase-02.md` with setup commands, test results, screenshots of normal/empty/error states, fixture sizes, dependency versions, and timing methodology. Phase 3 will reuse the artifact service and extend the UI with explicit submission; do not tie read-only rendering to execution side effects.
