# TASK-012: Execution Modeling, Slippage & Commission Simulation

## Objective

Implement realistic trade execution modeling, dynamic position sizing, and institutional friction simulation (slippage and broker commissions) in `src/backtest/execution_model.py`.

## Scope

### In Scope

- Config-driven position sizing algorithms:
  - **Fixed Risk Sizing:**
    $$\text{Shares} = \left\lfloor \frac{\text{Portfolio Capital} \times \text{Risk Pct}}{|\text{Entry Price} - \text{Stop Price}|} \right\rfloor$$
  - **Fixed Shares Sizing:** constant share quantity (e.g. 100 shares).
- Adverse slippage modeling:
  - Long Entry fill: $\text{Fill Price} = \text{Signal Price} + \text{Slippage Per Share}$
  - Long Exit (Stop) fill: $\text{Fill Price} = \text{Stop Price} - \text{Slippage Per Share}$
  - Long Exit (Target) fill: $\text{Fill Price} = \text{Target Price}$
  - Short Entry fill: $\text{Fill Price} = \text{Signal Price} - \text{Slippage Per Share}$
  - Short Exit (Stop) fill: $\text{Fill Price} = \text{Stop Price} + \text{Slippage Per Share}$
  - Short Exit (Target) fill: $\text{Fill Price} = \text{Target Price}$
- Commission calculation:
  $$\text{Commission} = \text{Shares} \times \text{Commission Per Share} \times 2 \quad (\text{Round-trip})$$
- Default SPY parameters: Slippage = \$0.01/share, Commission = \$0.0035/share (from `config/default_config.yaml`).

### Out of Scope

- Bar-by-bar backtest loop management (handled in TASK-013).

## Dependencies

- TASK-001
- TASK-002
- TASK-004
- TASK-011

## Requirements

1. Ensure share sizing always produces non-negative integer values ($\text{Shares} \ge 1$ if capital permits).
2. Cap maximum position value so it does not exceed available portfolio cash (no unconfigured leverage).
3. Compute total friction costs per trade and record them in the `Trade` object.
4. Support zero-slippage and zero-commission modes for theoretical baseline benchmarking.

## Implementation Details

1. Create `src/backtest/execution_model.py`.
2. Implement `ExecutionModel` class:
   ```python
   class ExecutionModel:
       def __init__(self, config: ExecutionConfig):
           ...
       def calculate_position_size(
           self,
           capital: float,
           entry_price: float,
           stop_price: float,
       ) -> int:
           ...
       def get_entry_fill(self, direction: str, price: float) -> tuple[float, float]:
           """Returns (fill_price, slippage_amount)."""
           ...
       def get_exit_fill(self, direction: str, price: float, reason: ExitReason) -> tuple[float, float]:
           """Returns (fill_price, slippage_amount)."""
           ...
       def calculate_commission(self, shares: int) -> float:
           """Returns round-trip commission for trade."""
           ...
   ```

## Interfaces / Contracts

```python
# src/backtest/execution_model.py

from src.common.config import ExecutionConfig
from src.backtest.models import ExitReason

class ExecutionModel:
    def __init__(self, config: ExecutionConfig):
        self.config = config

    def calculate_position_size(self, capital: float, entry_price: float, stop_price: float) -> int:
        ...

    def calculate_entry_execution(self, direction: str, price: float, shares: int) -> tuple[float, float, float]:
        """Returns (fill_price, slippage_dollars, commission_dollars)."""
        ...

    def calculate_exit_execution(self, direction: str, price: float, shares: int, reason: ExitReason) -> tuple[float, float, float]:
        """Returns (fill_price, slippage_dollars, commission_dollars)."""
        ...
```

## Data / File Changes

- Create `src/backtest/execution_model.py`

## Validation

1. Verify position sizing: with \$100,000 capital, 1% risk (\$1,000), entry \$500, stop \$498 (risk \$2.00/share), assert sized shares = 500 shares.
2. Verify slippage: with \$0.01/share slippage on 500 shares, assert Long entry fill = \$500.01 and slippage paid = \$5.00.
3. Verify zero friction: set slippage = 0, commission = 0; assert fills match theoretical levels exactly.

## Acceptance Criteria

- [ ] Position sizing strictly adheres to the configured risk percentage and never exceeds portfolio equity.
- [ ] Slippage applies adversely to market orders and stop fills.
- [ ] Round-trip commissions are accurately accrued.
- [ ] All friction costs are parameterized from `config/default_config.yaml`.

## Notes

- SPY has the deepest liquidity in U.S. equities, making \$0.01/share slippage a conservative, institutional-grade estimate.
