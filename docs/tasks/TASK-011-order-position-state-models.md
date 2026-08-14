# TASK-011: Order, Position & Trade State Models

## Objective

Design and implement the object-oriented data structures for order management, active position tracking, and closed trade recording in `src/backtest/models.py`, ensuring comprehensive accounting of fills, fees, slippage, and R-multiples.

## Scope

### In Scope

- `PositionState` enumeration (`FLAT`, `LONG`, `SHORT`).
- `ExitReason` enumeration (`TARGET`, `STOP`, `EOD`).
- `Position` dataclass representing active in-market holdings:
  - Direction, entry time, entry fill price, current stop loss, current take profit, share count, risk dollar amount ($1R$).
- `Trade` dataclass representing a finalized, closed trade:
  - Full execution audit trail: `trade_id`, `date`, `symbol`, `direction`, `entry_time`, `exit_time`, `entry_price`, `exit_price`, `stop_price`, `target_price`, `or_high`, `or_low`, `or_width`, `shares`, `pnl_dollars`, `return_pct`, `r_multiple`, `exit_reason`, `slippage_paid`, `commission_paid`.
- P&L and R-multiple calculation methods:
  - Long P&L: $(\text{Exit Price} - \text{Entry Price}) \times \text{Shares} - \text{Frictions}$
  - Short P&L: $(\text{Entry Price} - \text{Exit Price}) \times \text{Shares} - \text{Frictions}$
  - R-Multiple: $\frac{\text{Gross PnL Per Share}}{\text{Initial Risk Per Share}}$

### Out of Scope

- Bar replay iteration logic (handled in TASK-013).
- Visualization and reporting (handled in TASK-015 and TASK-016).

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-010

## Requirements

1. Match the exact Trade Log Schema specified in Section 5.1 of `/docs/plans/ORB_SYSTEM_PLAN.md`.
2. Provide a clean `Position.close(exit_time, exit_price, reason, slippage, commission) -> Trade` transition method.
3. Guarantee accurate sign conventions for Short trades (price drop = positive P&L; price rise = negative P&L).
4. Guard against division by zero during R-multiple calculation.

## Implementation Details

1. Create `src/backtest/models.py`.
2. Define enums and classes:
   ```python
   class PositionSide(str, Enum):
       FLAT = "FLAT"
       LONG = "LONG"
       SHORT = "SHORT"

   class ExitReason(str, Enum):
       TARGET = "TARGET"
       STOP = "STOP"
       EOD = "EOD"

   @dataclass
   class Position:
       side: PositionSide
       symbol: str
       entry_time: pd.Timestamp
       entry_price: float
       stop_loss: float
       take_profit: float
       shares: int
       initial_risk_per_share: float
       or_high: float
       or_low: float
       or_width: float

       def close(
           self,
           exit_time: pd.Timestamp,
           exit_price: float,
           reason: ExitReason,
           slippage: float = 0.0,
           commission: float = 0.0,
       ) -> Trade:
           ...
   ```

## Interfaces / Contracts

```python
# src/backtest/models.py

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd

class PositionSide(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"

class ExitReason(str, Enum):
    TARGET = "TARGET"
    STOP = "STOP"
    EOD = "EOD"

@dataclass
class Trade:
    trade_id: int
    date: str
    symbol: str
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    or_high: float
    or_low: float
    or_width: float
    shares: int
    pnl_dollars: float
    return_pct: float
    r_multiple: float
    exit_reason: str
    slippage_paid: float
    commission_paid: float

    def to_dict(self) -> dict:
        ...
```

## Data / File Changes

- Create `src/backtest/models.py`

## Validation

1. Instantiate a Long `Position` at entry \$500, stop \$495, target \$510 with 100 shares.
   - Close at \$510 via `TARGET`: assert P&L = +\$1,000 (minus fees) and R-multiple = +2.0.
   - Close at \$495 via `STOP`: assert P&L = -\$500 (minus fees) and R-multiple = -1.0.
2. Instantiate a Short `Position` at entry \$500, stop \$505, target \$490 with 100 shares.
   - Close at \$490 via `TARGET`: assert P&L = +\$1,000 and R-multiple = +2.0.
   - Close at \$505 via `STOP`: assert P&L = -\$500 and R-multiple = -1.0.

## Acceptance Criteria

- [ ] `Trade` class fields match Section 5.1 of `/docs/plans/ORB_SYSTEM_PLAN.md` 1-to-1.
- [ ] Long and Short P&L equations calculate correctly.
- [ ] Realized R-multiple matches theoretical expected values (+2.0 for target, -1.0 for stop).
- [ ] `Trade.to_dict()` outputs clean primitive datatypes for CSV export.

## Notes

- Accurate position tracking is essential for calculating drawdown curves and expectancy metrics.
