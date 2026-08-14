# TASK-015: Trade Reporting & Results Artifact Exporter

## Objective

Implement the reporting and disk persistence module in `src/evaluation/reporter.py` that formats and saves backtest outputs to `results/backtest/{run_id}/`, generating `trades.csv`, `equity_curve.csv`, `daily_summary.csv`, `metrics.json`, and printing an executive summary to the terminal.

## Scope

### In Scope

- Creation of unique, timestamped or tagged result run directories (e.g. `results/backtest/SPY_baseline_v1/`).
- Export of `trades.csv` conforming strictly to the schema in Section 5.1 of `/docs/plans/ORB_SYSTEM_PLAN.md`.
- Export of `equity_curve.csv` containing minute-level timestamp, equity, cash, drawdown, and active position.
- Export of `daily_summary.csv` containing date, daily P&L, trade count, OR High, OR Low, and range width.
- Export of `metrics.json` conforming strictly to the schema in Section 5.2 of `/docs/plans/ORB_SYSTEM_PLAN.md`.
- Rich terminal console summary table with key performance indicators (Total Trades, Win Rate, Profit Factor, Sharpe, Max Drawdown, Total R).

### Out of Scope

- Rendering graphical image plots (handled in TASK-016 and TASK-017).

## Dependencies

- TASK-001
- TASK-002
- TASK-004
- TASK-011
- TASK-013
- TASK-014

## Requirements

1. Export files must adhere exactly to the defined CSV and JSON schemas.
2. Formats must be machine-readable and validatable by automated parsers.
3. Automatically create target directories if they do not exist.
4. Provide a clean `ResultsReporter` class that orchestrates the persistence of all backtest output artifacts.

## Implementation Details

1. Create `src/evaluation/reporter.py`.
2. Implement `ResultsReporter` class:
   ```python
   class ResultsReporter:
       def __init__(self, output_dir: Path):
           self.output_dir = output_dir

       def save_artifacts(
           self,
           result: BacktestResult,
           metrics: dict,
           run_name: str = "SPY_baseline_v1",
       ) -> dict[str, Path]:
           ...

       def print_summary_table(self, metrics: dict) -> None:
           ...
   ```
3. Use `json.dump(..., indent=2)` for clean human readability of `metrics.json`.

## Interfaces / Contracts

```python
# src/evaluation/reporter.py

from pathlib import Path
from typing import Dict
from src.backtest.engine import BacktestResult

class ResultsReporter:
    def __init__(self, base_results_dir: str = "results/backtest"):
        self.base_dir = Path(base_results_dir)

    def export_all(
        self,
        backtest_result: BacktestResult,
        metrics: Dict,
        run_id: str = "SPY_baseline_v1",
    ) -> Dict[str, Path]:
        """
        Exports:
          - results/backtest/{run_id}/trades.csv
          - results/backtest/{run_id}/equity_curve.csv
          - results/backtest/{run_id}/daily_summary.csv
          - results/backtest/{run_id}/metrics.json
        Returns dict mapping artifact names to absolute file paths.
        """
        ...

    def display_console_summary(self, metrics: Dict) -> None:
        """Prints a clean ASCII summary table of performance metrics to stdout."""
        ...
```

## Data / File Changes

- Create `src/evaluation/reporter.py`
- Writes to `results/backtest/{run_id}/`

## Validation

1. Pass a mock `BacktestResult` and `metrics` dict to `export_all()`.
2. Verify that all 4 files (`trades.csv`, `equity_curve.csv`, `daily_summary.csv`, `metrics.json`) are written.
3. Verify that `trades.csv` has the exact column headers specified in Section 5.1.
4. Verify that `metrics.json` loads cleanly with `json.load()` and matches Section 5.2.

## Acceptance Criteria

- [ ] All 4 result artifact files are saved under `results/backtest/{run_id}/`.
- [ ] `trades.csv` headers match Section 5.1 specification.
- [ ] `metrics.json` structure matches Section 5.2 specification.
- [ ] Terminal summary displays clearly without formatting errors.

## Notes

- Machine-readable artifact formats allow downstream parameter comparison and statistical testing.
