# TASK-010: ORB Breakout Signal Generation Engine

## Objective

Implement the Opening Range Breakout (ORB) signal generation engine in `src/strategy/signals.py` that processes incoming 1-minute trading bars, detects bar-close breakouts beyond the frozen opening range boundaries, calculates dynamic stop-loss and $2R$ profit-target levels, and enforces the single-trade-per-session constraint.

## Scope

### In Scope

- Evaluation of trading bars in the active trading window ($09:45:00 \le t < 15:59:00$ ET).
- Implementation of bar-close breakout logic:
  - **Long Signal:** $\text{Close}_t > \text{OR High} + \text{Buffer}$
  - **Short Signal:** $\text{Close}_t < \text{OR Low} - \text{Buffer}$
- Calculation of risk parameters upon signal trigger:
  - $\text{Entry Price} = \text{Close}_t$
  - $\text{Long Stop Loss} = \text{OR Low}$
  - $\text{Long Risk} (1R) = \text{Entry Price} - \text{Stop Loss}$
  - $\text{Long Take Profit} (2R) = \text{Entry Price} + 2.0 \times \text{Long Risk}$
  - $\text{Short Stop Loss} = \text{OR High}$
  - $\text{Short Risk} (1R) = \text{Stop Loss} - \text{Entry Price}$
  - $\text{Short Take Profit} (2R) = \text{Entry Price} - 2.0 \times \text{Short Risk}$
- Session signal lock: once a signal has triggered in a session, no further signals may be generated for that session (enforcing `max_trades_per_day = 1`).
- Optional filter hooks (RVOL and VWAP placeholders for future version compatibility).

### Out of Scope

- Simulating trade fills, slippage, and position tracking (handled in TASK-011 and TASK-013).

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-008
- TASK-009

## Requirements

1. Do NOT evaluate breakouts before 09:45:00 ET.
2. Rely strictly on bar close price ($\text{Close}_t$) for breakout confirmation in baseline v1.
3. Reject invalid signals where calculated risk $\le 0$ (e.g. inverted bounds).
4. Strictly enforce `max_trades_per_day = 1` per session.
5. Provide a deterministic `Signal` dataclass containing all trade entry parameters.

## Implementation Details

1. Create `src/strategy/signals.py`.
2. Define `Signal` dataclass:
   ```python
   @dataclass(frozen=True)
   class Signal:
       session_id: str
       timestamp: pd.Timestamp
       symbol: str
       direction: str  # "LONG" or "SHORT"
       entry_price: float
       stop_loss: float
       take_profit: float
       risk_amount: float  # 1R
       or_high: float
       or_low: float
       or_width: float
   ```
3. Implement `SignalGenerator` class:
   - Iterates through a session's trading bars in sequential order.
   - Evaluates breakout conditions against the session's frozen `OpeningRange`.
   - Returns a `Signal` or `None`.

## Interfaces / Contracts

```python
# src/strategy/signals.py

from dataclasses import dataclass
from typing import Optional
import pandas as pd
from src.common.config import StrategyConfig
from src.strategy.opening_range import OpeningRange

@dataclass(frozen=True)
class Signal:
    session_id: str
    timestamp: pd.Timestamp
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    or_high: float
    or_low: float
    or_width: float

class SignalGenerator:
    def __init__(self, config: StrategyConfig):
        ...

    def evaluate_session_signals(
        self,
        session_bars: pd.DataFrame,
        opening_range: OpeningRange,
    ) -> Optional[Signal]:
        """
        Evaluates session trading bars (09:45 - 15:59) sequentially.
        Returns first valid Signal or None if no breakout occurs.
        """
        ...
```

## Data / File Changes

- Create `src/strategy/signals.py`

## Validation

1. Supply a session where bar at 09:48 closes above `or_high`:
   - Assert `Signal` generated with `direction="LONG"`, `stop_loss=or_low`, and `take_profit=entry + 2 * (entry - stop)`.
2. Supply subsequent breakout bars later in the same session:
   - Assert that no second signal is generated.
3. Supply a session where price breaks out at 09:35:
   - Assert that NO signal is generated because 09:35 is still inside the opening range.

## Acceptance Criteria

- [ ] Long breakout triggers when $\text{Close}_t > \text{OR High}$.
- [ ] Short breakout triggers when $\text{Close}_t < \text{OR Low}$.
- [ ] $2R$ target and stop loss are mathematically exact and tied to entry price.
- [ ] Exactly $\le 1$ signal is generated per trading session.
- [ ] No signals are produced during the 09:30–09:44 opening range window.

## Notes

- Bar-close confirmation avoids intrabar noise and false breakouts compared to tick-level triggers.
