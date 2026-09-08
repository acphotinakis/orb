# Synthesis review and implementation decisions

The synthesis preserves the main architectural findings and phase order. Review status: documentation reviewed; no implementation or user approval is claimed here.

## Decisions

| ID | Implementation direction | Basis |
|---|---|---|
| D1 | Restore static PNG exports when `generate_plots=True`; skip them when false. | Already specified in Phase 1 objective P1-O1. Interactive charts use tables and do not depend on PNG generation. |
| D2 | Use immutable, versioned processed datasets keyed by every content-affecting input for the first implementation. | Engineering choice within the existing plan; smallest change to the current processor contract. Include raw-content fingerprint, feed, symbol, timeframe, bounds, preprocessing version, OR duration, and exit time. Deriving flags from normalized data can be a later optimization. |
| D3 | Adopt a one-minute dashboard preset; migrate the unused YAML `parameters` fields into explicit configuration/run-option fields. | Phase 1 already specifies the preset and migration. Preserve explicit configured values through documented precedence; this does not authorize changing unrelated strategy defaults such as target R. |
| D4 | Extract shared domain decision functions, preserving accepted engine behavior under parity tests. | Already specified by P4-O1. In Phase 1 reject currently unsupported controls; shared-rule extraction belongs to Phase 4 unless a specific correctness fix requires it earlier. |
| D5 | Exclude broker orders, browser editing, multi-user hosting, and EMA integration. | Already explicit in the master plan's scope. |
| D6 | One worker process with a durable queue. | Already explicit in the master plan and Phase 3. |

D1, D3's preset, D4, D5, and D6 do not need a new approval gate merely because the synthesis labels them pending. D2 is a routine implementation choice; the direction above resolves it for planning. YAML field mapping and compatibility details still need to be written and tested in P1-2.

Keep existing domain modules and add `src/dashboard/` and `src/services/`. Do not move the project into frontend/backend directories for this implementation.

## Corrections before using the synthesis as a ticket source

1. **Source count:** There are nine source entries but ten individual files, because the final entry contains two logs.
2. **Hypothesis count:** The hypothesis table contains one validated hypothesis (H1), one partial (H4), and four invalidated hypotheses (H2, H3, H5, H6). Partial validation must not be counted as full validation.
3. **Ticket count:** The JSON contains 19 tickets, but the phase recommendation tables contain 22 rows: 6 + 4 + 4 + 4 + 4. Choose one canonical inventory. Prefer retaining the explicit 22 work items so race testing, the source pane, and streaming soak/provider verification remain visible.
4. **Missing acceptance coverage in JSON:** P2-A5, P3-A6, P4-A4/A6/A7, and P5-A7 have no explicit ticket mapping. P1-5 also needs plot-contract coverage through P1-A1/T12, not accounting acceptance alone. Assign each acceptance criterion to a ticket even if the inventory remains at 19.
5. **Dependency wording:** Replace “Do NOT build until P1-A1..A6 green” with “Do not start dependent phase implementation until P1-A1..A6 are green.” P1 itself is the work required to achieve that gate.
6. **Chart dependency:** P2 Plotly charts depend on validated datasets and metrics, not the PNG export decision. Remove D1 as a direct P2-3 blocker; retain the overall Phase 1 gate.
7. **Baseline repair dependencies:** P1-1's final green-suite acceptance depends on accounting/plot work and other P1 contract fixes. Reproduce and repair isolated fixtures first, but do not require P1-1 completion before allowing the other P1 tickets to begin.
8. **Accounting certainty:** The mismatch and late liquidation path are observed; causation remains unproven. Preserve U1 until a focused test isolates the cause.

The synthesis's proposed approval command is not needed to review local documents or make routine implementation decisions. Do not record `approved_by` or an approval date without an actual approval event.

## Recommended P1 execution order

1. Reproduce baseline failures and add an independent accounting regression reproducing the discrepancy.
2. Resolve intended config/metric contracts and repair malformed fixtures without weakening assertions.
3. Implement RunRequest validation and explicit time/session semantics.
4. Implement processed-cache identity, dependent refresh, and immutable run manifests together.
5. Correct accounting and restore plotting, using the focused regression and export tests.
6. Run the full offline suite and complete `validation/phase-01.md` with actual evidence.

Later-phase acceptance criteria remain those in the detailed phase specifications. These decisions resolve planning ambiguity; they do not mark any phase complete.
