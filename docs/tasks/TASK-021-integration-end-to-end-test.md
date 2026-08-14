# TASK-021: End-to-End Pipeline Integration Test Suite

## Objective

Implement the complete integration and end-to-end (E2E) test suite in `tests/integration/test_pipeline_e2e.py` to verify that the entire data, strategy, backtesting, evaluation, persistence, and visualization pipeline executes cohesively without runtime errors.

## Scope

### In Scope

- End-to-end pipeline execution test using an offline multi-week synthetic or fixture dataset:
  $$\text{Raw Ingestion} \rightarrow \text{Validation} \rightarrow \text{RTH Processing} \rightarrow \text{OR Calc} \rightarrow \text{Signals} \rightarrow \text{Backtest} \rightarrow \text{Metrics} \rightarrow \text{Reporting} \rightarrow \text{Plots}$$
- Verification of all physical artifacts generated on disk:
  - `results/backtest/{test_run_id}/trades.csv`
  - `results/backtest/{test_run_id}/equity_curve.csv`
  - `results/backtest/{test_run_id}/daily_summary.csv`
  - `results/backtest/{test_run_id}/metrics.json`
  - `plots/equity_curves/equity_curve.png`
  - `plots/drawdowns/drawdown_curve.png`
  - `plots/distributions/r_multiples.png`
  - `plots/trades/trade_*.png`
- CLI entry-point invocation test via `subprocess` or `sys.argv` runner (`python -m src.main`).
- Verification that exit codes, logs, and console summaries conform to system specifications.

### Out of Scope

- Live broker order execution or live WebSocket connections.

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-005
- TASK-006
- TASK-007
- TASK-008
- TASK-009
- TASK-010
- TASK-011
- TASK-012
- TASK-013
- TASK-014
- TASK-015
- TASK-016
- TASK-017
- TASK-018
- TASK-019
- TASK-020

## Requirements

1. Test must execute completely offline using test fixtures in `tests/fixtures/`.
2. Clean up temporary test output directories after test completion using `pytest` fixtures (`tmp_path`).
3. Validate that generated CSV files match expected row schemas and non-empty records.
4. Validate that generated PNG files are valid readable images.

## Implementation Details

1. Create `tests/fixtures/` with a deterministic 5-day SPY 1-minute bar fixture (`spy_sample_5d.parquet` or generated in fixture).
2. Create `tests/integration/test_pipeline_e2e.py`.
3. Implement test cases:
   ```python
   def test_pipeline_e2e_execution(tmp_path):
       """Runs full ORBPipeline on sample data and asserts all outputs are generated."""
       ...

   def test_cli_main_entrypoint(tmp_path):
       """Tests CLI invocation via python -m src.main with custom arguments."""
       ...
   ```

## Interfaces / Contracts

```text
tests/
└── integration/
    ├── __init__.py
    └── test_pipeline_e2e.py
```

## Data / File Changes

- Create `tests/integration/__init__.py`
- Create `tests/integration/test_pipeline_e2e.py`

## Validation

1. Run `pytest tests/integration/ -v`.
2. Inspect the temporary test run directory to ensure all artifact files are present, well-formed, and populated with valid data.

## Acceptance Criteria

- [ ] Full pipeline runs end-to-end from raw data to plots without throwing uncaught exceptions.
- [ ] All 4 result artifact files and all 4 plot categories are verified on disk.
- [ ] CLI entry point executes with exit code 0.
- [ ] Integration test suite completes in $< 15$ seconds.

## Notes

- End-to-end integration tests prove that all subpackages interact seamlessly under real execution workflows.
