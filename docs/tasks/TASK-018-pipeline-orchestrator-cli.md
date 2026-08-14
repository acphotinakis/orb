# TASK-018: End-to-End Pipeline Orchestrator & CLI Entry Point

## Objective

Implement the central pipeline orchestrator and command-line interface (CLI) in `src/main.py` and `src/pipeline.py` that seamlessly ties together configuration loading, data retrieval, processing, strategy simulation, performance evaluation, artifact persistence, and visualization into a single executable workflow.

## Scope

### In Scope

- CLI interface using `argparse` with flags:
  - `--config`: Path to YAML configuration file (default: `config/default_config.yaml`).
  - `--start-date`: Start date for backtest window (`YYYY-MM-DD`).
  - `--end-date`: End date for backtest window (`YYYY-MM-DD`).
  - `--symbol`: Stock ticker symbol (default: `SPY`).
  - `--feed`: Market data feed (`iex` or `sip`, default: `iex`).
  - `--ispaper`: Use paper trading credentials (default: `True`).
  - `--refresh-cache`: Force re-download of raw historical data.
  - `--no-plots`: Skip image chart generation for faster headless execution.
  - `--log-level`: Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
- Pipeline orchestration flow:
  1. Load and validate `AppConfig`.
  2. Ingest raw bars via `DataFetcher` (using cache when available).
  3. Validate and clean bars via `validate_and_clean_bars()`.
  4. Process RTH sessions via `DataProcessor`.
  5. Execute backtest simulation via `BacktestEngine`.
  6. Compute statistical and portfolio metrics via `generate_performance_report()`.
  7. Export artifacts (`trades.csv`, `equity_curve.csv`, `daily_summary.csv`, `metrics.json`) via `ResultsReporter`.
  8. Generate visualization charts via `CandlestickTradePlotter` and `PerformancePlotter`.
  9. Print executive performance summary to console.
- Exit code standards: `0` for successful execution, `1` for fatal configuration, data, or runtime error.

### Out of Scope

- Live trading order submission.

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

## Requirements

1. Provide standard CLI entry point `python -m src.main` or `python src/main.py`.
2. Allow CLI flags to override specific configuration settings from YAML.
3. Guarantee that all intermediate and final artifacts are properly routed to their designated directories.
4. Catch all unhandled exceptions, log the stack trace cleanly, and exit with status code `1`.

## Implementation Details

1. Create `src/pipeline.py` implementing `ORBPipeline` class.
2. Create `src/main.py` implementing `parse_args()` and invoking `ORBPipeline.run()`.
3. Provide structured progress logs at each major phase of the pipeline.

## Interfaces / Contracts

```python
# src/pipeline.py

from dataclasses import dataclass
from typing import Dict, Any
from src.common.config import AppConfig

@dataclass
class PipelineRunResult:
    config: AppConfig
    metrics: Dict[str, Any]
    artifacts: Dict[str, str]
    total_trades: int
    execution_time_seconds: float

class ORBPipeline:
    def __init__(self, config: AppConfig):
        self.config = config

    def run(
        self,
        start_date: str,
        end_date: str,
        refresh_cache: bool = False,
        generate_plots: bool = True,
    ) -> PipelineRunResult:
        """Runs the entire end-to-end ORB backtesting and reporting pipeline."""
        ...
```

```python
# src/main.py

def main() -> int:
    ...

if __name__ == "__main__":
    import sys
    sys.exit(main())
```

## Data / File Changes

- Create `src/pipeline.py`
- Create `src/main.py`

## Validation

1. Run `python -m src.main --help` and verify all CLI options and default values are described.
2. Execute a dry run of the pipeline using local cached or synthetic data and assert exit code `0`.
3. Verify that all CSV, JSON, and PNG files are generated in `results/` and `plots/`.

## Acceptance Criteria

- [ ] `python -m src.main` runs the complete workflow from ingestion through reporting.
- [ ] CLI arguments cleanly override YAML configuration where specified.
- [ ] Exit codes adhere to POSIX standards ($0 = \text{success}$, $1 = \text{failure}$).
- [ ] Terminal displays a well-formatted summary table upon completion.

## Notes

- The CLI entry point provides an intuitive interface for running automated backtest sweeps and scheduled batch runs.
