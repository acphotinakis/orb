# TASK-017: Portfolio Performance Curves & Distribution Visualizer

## Objective

Implement the portfolio-level visual analytics module in `src/visualization/performance_plotter.py` to generate publication-grade charts of cumulative equity curves, underwater drawdown profiles, R-multiple distribution histograms, and monthly return heatmaps.

## Scope

### In Scope

- Generation and export of:
  - **Cumulative Equity Curve:** Total portfolio equity over time vs. SPY Buy & Hold benchmark (`plots/equity_curves/equity_curve.png`).
  - **Underwater Drawdown Chart:** Peak-to-trough percentage drawdown series over time (`plots/drawdowns/drawdown_curve.png`).
  - **R-Multiple Distribution:** Histogram and KDE of realized R-multiples with win/loss zones (`plots/distributions/r_multiples.png`).
  - **Trade Duration Distribution:** Histogram showing distribution of trade holding times in minutes (`plots/distributions/trade_durations.png`).
  - **Monthly Returns Summary:** Bar chart / matrix showing performance by month (`plots/distributions/monthly_returns.png`).
- Clean, publication-ready visual styling (consistent gridlines, readable legends, high DPI).

### Out of Scope

- Rendering individual candlestick trade charts (handled in TASK-016).

## Dependencies

- TASK-001
- TASK-002
- TASK-004
- TASK-013
- TASK-014

## Requirements

1. Save output images as high-DPI (minimum 150 DPI) PNG files to their respective directories:
   - `plots/equity_curves/`
   - `plots/drawdowns/`
   - `plots/distributions/`
2. Overlay key metrics summary box on the equity curve chart (e.g. Sharpe Ratio, Max Drawdown %, Win Rate %, Total R).
3. Ensure charts handle both positive and negative performance regimes without visual distortion.

## Implementation Details

1. Create `src/visualization/performance_plotter.py`.
2. Implement `PerformancePlotter` class:
   ```python
   class PerformancePlotter:
       def __init__(self, plots_dir: Path = Path("plots")):
           self.plots_dir = plots_dir
           self.equity_dir = plots_dir / "equity_curves"
           self.dd_dir = plots_dir / "drawdowns"
           self.dist_dir = plots_dir / "distributions"

       def plot_equity_curve(self, equity_df: pd.DataFrame, metrics: dict) -> Path:
           ...
       def plot_drawdowns(self, equity_df: pd.DataFrame) -> Path:
           ...
       def plot_r_distribution(self, trades_df: pd.DataFrame) -> Path:
           ...
       def plot_all(self, result: BacktestResult, metrics: dict) -> dict[str, Path]:
           ...
   ```
3. Use `matplotlib.pyplot` in a headless/non-interactive backend (`Agg`) to prevent GUI display blocking.

## Interfaces / Contracts

```python
# src/visualization/performance_plotter.py

import pandas as pd
from pathlib import Path
from typing import Dict
from src.backtest.engine import BacktestResult

class PerformancePlotter:
    def __init__(self, plots_base_dir: str = "plots"):
        self.base_dir = Path(plots_base_dir)

    def generate_all_plots(
        self,
        backtest_result: BacktestResult,
        metrics: Dict,
    ) -> Dict[str, Path]:
        """
        Generates and saves:
          - equity_curves/equity_curve.png
          - drawdowns/drawdown_curve.png
          - distributions/r_multiples.png
          - distributions/trade_durations.png
        Returns dictionary of generated file paths.
        """
        ...
```

## Data / File Changes

- Create `src/visualization/performance_plotter.py`
- Writes image files to `plots/equity_curves/`, `plots/drawdowns/`, `plots/distributions/`

## Validation

1. Pass a mock `BacktestResult` (with 50 trades and equity series) and metrics dictionary to `generate_all_plots()`.
2. Verify that all expected PNG files are created and valid.
3. Verify that `matplotlib` executes headlessly without requiring an active X-server display.

## Acceptance Criteria

- [ ] `equity_curve.png` renders equity trajectory and metrics summary box.
- [ ] `drawdown_curve.png` renders underwater chart accurately.
- [ ] `r_multiples.png` renders distribution centered around -1.0R and +2.0R.
- [ ] All plots are saved into their designated folders under `plots/`.

## Notes

- Visual distributions allow fast evaluation of strategy skewness and tail risk.
