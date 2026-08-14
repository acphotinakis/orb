# TASK-013: Event-Driven Backtesting Engine & Dual-Touch Resolver

## Objective

Implement the core sequential, event-driven backtesting engine in `src/backtest/engine.py` that replays 1-minute historical bars session-by-session, manages position transitions, enforces conservative dual-touch stop/target resolution, handles 15:59:00 ET force-flattening, and produces a complete trade ledger and equity curve.

## Scope

### In Scope

- Stateful bar-by-bar session replay across the entire processed dataset.
- Session lifecycle orchestration:
  1. Opening Range phase ($09:30 - 09:44$): compute and freeze `OpeningRange`.
  2. Active Trading phase ($09:45 - 15:58$): evaluate signals on bar close; manage open bracket orders.
  3. Force Exit phase ($15:59$): force-close any open position at market close price.
- In-position bracket order evaluation on each 1-minute bar:
  - **Long Position:**
    - Stop Loss hit if $\text{Low}_t \le \text{Stop Price}$
    - Take Profit hit if $\text{High}_t \ge \text{Target Price}$
  - **Short Position:**
    - Stop Loss hit if $\text{High}_t \ge \text{Stop Price}$
    - Take Profit hit if $\text{Low}_t \le \text{Target Price}$
- **Conservative Dual-Touch Resolution (CRITICAL REQUIREMENT):**
  - If a 1-minute bar touches BOTH Stop Loss and Take Profit levels ($\text{Low}_t \le \text{Stop}$ and $\text{High}_t \ge \text{Target}$ for Long), the engine **MUST assume Stop Loss occurred first** and exit with `ExitReason.STOP`.
- Daily trade constraint: strict enforcement of maximum 1 trade per session.
- Real-time equity curve tracking (cash + mark-to-market value at each minute).

### Out of Scope

- Statistical performance metric calculation (handled in TASK-014).
- Graph plotting (handled in TASK-016 and TASK-017).

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-008
- TASK-009
- TASK-010
- TASK-011
- TASK-012

## Requirements

1. Process sessions in strict chronological order with zero forward-looking state sharing.
2. Opening range must be locked before bar 09:45:00 ET is evaluated.
3. Dual-touch condition MUST result in a Stop Loss exit, never a Take Profit exit.
4. No position may remain open overnight; all positions must be closed by `15:59:00 ET` (`ExitReason.EOD`).
5. Output:
   - `trades`: List of finalized `Trade` objects.
   - `equity_curve`: DataFrame with minute-by-minute portfolio equity.
   - `daily_summary`: DataFrame summarizing daily P&L and session outcomes.

## Implementation Details

1. Create `src/backtest/engine.py`.
2. Implement `BacktestEngine` class:
   ```python
   class BacktestEngine:
       def __init__(self, config: AppConfig):
           self.config = config
           self.or_calculator = OpeningRangeCalculator(config.strategy.opening_range_minutes)
           self.signal_generator = SignalGenerator(config.strategy)
           self.execution_model = ExecutionModel(config.execution)

       def run(self, df_processed: pd.DataFrame) -> BacktestResult:
           ...
   ```
3. Implement `_process_bar(bar, position) -> tuple[Position, Optional[Trade]]` enforcing bracket order priority and dual-touch resolution.

## Interfaces / Contracts

```python
# src/backtest/engine.py

from dataclasses import dataclass
from typing import List
import pandas as pd
from src.common.config import AppConfig
from src.backtest.models import Trade

@dataclass
class BacktestResult:
    trades: List[Trade]
    trades_df: pd.DataFrame
    equity_curve: pd.DataFrame
    daily_summary: pd.DataFrame
    initial_capital: float
    final_capital: float

class BacktestEngine:
    def __init__(self, config: AppConfig):
        ...

    def run(self, df_processed: pd.DataFrame) -> BacktestResult:
        """
        Executes backtest over all sessions in df_processed.
        Returns BacktestResult containing trades, equity curve, and daily summaries.
        """
        ...
```

## Data / File Changes

- Create `src/backtest/engine.py`

## Validation

1. **Dual-Touch Test:** Construct a synthetic bar with $\text{Low} = \$490$ (below stop \$495) and $\text{High} = \$515$ (above target \$510). Assert the engine closes the trade with `exit_reason = "STOP"` and `exit_price = $495`.
2. **One-Trade-Per-Day Test:** Construct a session where a trade stops out at 10:15 and a second breakout occurs at 11:00. Assert that no second trade is entered.
3. **EOD Flatten Test:** Construct a trade that does not hit SL or TP by 15:58. Assert that the trade is closed at 15:59 with `exit_reason = "EOD"`.

## Acceptance Criteria

- [ ] Complete chronological bar-by-bar simulation executes without lookahead bias.
- [ ] Dual-touch bars resolve strictly to Stop Loss.
- [ ] Exactly $\le 1$ trade is executed per session.
- [ ] All open trades are flattened at 15:59:00 ET with zero overnight carryover.
- [ ] `BacktestResult` contains valid DataFrames for trades, equity curve, and daily summaries.

## Notes

- Bar-by-bar evaluation accurately simulates real-world order execution and avoids the optimistic biases of vectorization.
