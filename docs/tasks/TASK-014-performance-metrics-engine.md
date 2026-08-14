# TASK-014: Quantitative Performance & Risk Metrics Engine

## Objective

Implement the quantitative performance and risk-adjusted metrics engine in `src/evaluation/metrics.py` to calculate comprehensive statistical, portfolio, and execution metrics from trade logs and equity curves.

## Scope

### In Scope

- Computation of statistical trade metrics:
  - Total trades, Long trades count, Short trades count
  - Win count, Loss count, Win rate ($\%$)
  - Profit Factor ($\frac{\sum \text{Gross Profits}}{\sum |\text{Gross Losses}|}$)
  - Total Realized R, Average R per trade, Median R, Standard deviation of R
  - Expectancy ($E_R = W \times \bar{R}_{\text{win}} - (1-W) \times |\bar{R}_{\text{loss}}|$)
  - Average win dollar amount, Average loss dollar amount, Win/Loss payoff ratio
  - Best trade (R and \$), Worst trade (R and \$)
  - Breakdown of exit reasons (Target, Stop, EOD counts and percentages)
- Computation of time-series portfolio risk metrics:
  - Total Return ($\%$) and Annualized Return (CAGR $\%$)
  - Annualized Sharpe Ratio (assuming 252 trading days and configurable $r_f$)
  - Annualized Sortino Ratio (downside deviation based)
  - Maximum Drawdown (MDD $\%$) and Maximum Drawdown Duration (days)
  - Calmar Ratio ($\frac{\text{CAGR}}{\text{MDD}}$)
- Serialization to dictionary structure matching `metrics.json` schema.

### Out of Scope

- Disk persistence (handled in TASK-015).
- Chart plotting (handled in TASK-017).

## Dependencies

- TASK-001
- TASK-002
- TASK-004
- TASK-011
- TASK-013

## Requirements

1. Match the exact schema defined in Section 5.2 of `/docs/plans/ORB_SYSTEM_PLAN.md`.
2. Ensure mathematical robustness against zero-division (e.g. 0 trades, 0 losses, 0 standard deviation).
3. Compute drawdown series from high-water mark:
   $$\text{HWM}_t = \max_{0 \le s \le t} \text{Equity}_s$$
   $$\text{Drawdown}_t = \frac{\text{Equity}_t - \text{HWM}_t}{\text{HWM}_t}$$
4. Calculate Sharpe and Sortino ratios using daily session returns for standard financial comparability.

## Implementation Details

1. Create `src/evaluation/metrics.py`.
2. Implement `calculate_metrics(trades_df: pd.DataFrame, equity_df: pd.DataFrame, config: AppConfig) -> dict`:
   - Extracts trade return series and R-multiples.
   - Computes daily returns from equity curve.
   - Populates structured result dictionary.

## Interfaces / Contracts

```python
# src/evaluation/metrics.py

from typing import Dict, Any
import pandas as pd
from src.common.config import AppConfig

def calculate_trade_metrics(trades_df: pd.DataFrame) -> Dict[str, Any]:
    """Computes trade-level statistical metrics (win rate, profit factor, R-multiples)."""
    ...

def calculate_portfolio_metrics(equity_df: pd.DataFrame, risk_free_rate: float = 0.0) -> Dict[str, Any]:
    """Computes time-series metrics (Sharpe, Sortino, Drawdowns, CAGR)."""
    ...

def generate_performance_report(
    trades_df: pd.DataFrame,
    equity_df: pd.DataFrame,
    config: AppConfig,
) -> Dict[str, Any]:
    """
    Generates complete metrics dictionary conforming to Section 5.2 metrics.json schema.
    """
    ...
```

## Data / File Changes

- Create `src/evaluation/metrics.py`

## Validation

1. Supply a known series of 10 trades (6 wins at +2.0R, 4 losses at -1.0R):
   - Assert Win Rate = 60.0%.
   - Assert Average R = +0.8R.
   - Assert Total R = +8.0R.
   - Assert Profit Factor = 3.0.
   - Assert Expectancy = +0.8R.
2. Supply an equity curve with a known 10% peak-to-trough decline; assert calculated MDD = 10.0%.

## Acceptance Criteria

- [ ] All metrics in Section 5.2 of `/docs/plans/ORB_SYSTEM_PLAN.md` are calculated.
- [ ] Edge cases (e.g. 100% win rate or 0 trades) handle division-by-zero cleanly without throwing uncaught exceptions.
- [ ] Sharpe, Sortino, MDD, and Calmar formulas match industry standards.

## Notes

- Expectancy ($E_R$) is the primary metric for determining whether the ORB edge is statistically significant.
