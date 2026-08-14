# TASK-009: Opening Range Calculation Engine

## Objective

Implement the Opening Range (OR) computation engine in `src/strategy/opening_range.py` that calculates session-by-session opening range bands (`OR High`, `OR Low`, `OR Width`) strictly from bars in the $[09:30:00, 09:45:00)$ ET interval, enforcing frozen range state and strict temporal causality.

## Scope

### In Scope

- Extraction of opening range bars ($09:30:00 \le t \le 09:44:59$ ET) for each trading session.
- Mathematical computation of range boundaries:
  $$\text{OR High} = \max_{t \in [09:30, 09:45)} \text{High}_t$$
  $$\text{OR Low} = \min_{t \in [09:30, 09:45)} \text{Low}_t$$
  $$\text{OR Width} = \text{OR High} - \text{OR Low}$$
- Range sanity checks (assert $\text{OR Width} > 0$ and range bar count $= 15$).
- State freezing: opening range parameters are frozen at 09:45:00 ET and remain strictly constant for the remainder of the trading day.
- Calculation of session summary metrics (e.g. range relative to 14-day ATR, opening volume).
- Serialization of daily ranges to `data/processed/SPY/daily_ranges.parquet`.

### Out of Scope

- Breakout detection or order placement (handled in TASK-010).

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-008

## Requirements

1. Target universe: `SPY`.
2. Duration: configurable via `StrategyConfig.opening_range_minutes` (default: 15 minutes).
3. Exact window: 09:30:00 through 09:44:59 America/New_York.
4. Temporal anti-leakage rule: No bar timestamped at or after `09:45:00 ET` may be included in the calculation of `OR High` or `OR Low`.
5. Output structured dataclass `OpeningRange` per session containing:
   - `session_id`: Date string (`YYYY-MM-DD`)
   - `or_high`: Float
   - `or_low`: Float
   - `or_width`: Float
   - `or_volume`: Int / Float (total volume accumulated during OR)
   - `is_valid`: Bool

## Implementation Details

1. Create `src/strategy/opening_range.py`.
2. Define `OpeningRange` dataclass:
   ```python
   @dataclass(frozen=True)
   class OpeningRange:
       session_id: str
       start_time: pd.Timestamp
       end_time: pd.Timestamp
       or_high: float
       or_low: float
       or_width: float
       total_volume: float
       bar_count: int
       is_valid: bool
   ```
3. Implement `compute_opening_ranges(df: pd.DataFrame, or_minutes: int = 15) -> dict[str, OpeningRange]`.
4. Ensure vectorization where possible or fast session groupby iteration.

## Interfaces / Contracts

```python
# src/strategy/opening_range.py

import pandas as pd
from dataclasses import dataclass
from typing import Dict

@dataclass(frozen=True)
class OpeningRange:
    session_id: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    or_high: float
    or_low: float
    or_width: float
    total_volume: float
    bar_count: int
    is_valid: bool

class OpeningRangeCalculator:
    def __init__(self, or_minutes: int = 15):
        ...

    def calculate_session_or(self, session_bars: pd.DataFrame) -> OpeningRange:
        """
        Calculates OR boundaries for a single session DataFrame.
        Raises TemporalLeakageError if session_bars contains bars outside session.
        """
        ...

    def calculate_all(self, df: pd.DataFrame) -> Dict[str, OpeningRange]:
        """Calculates OpeningRange for all sessions in processed dataset."""
        ...
```

## Data / File Changes

- Create `src/strategy/opening_range.py`
- Writes to `data/processed/SPY/daily_ranges.parquet`

## Validation

1. Supply a known 15-minute synthetic price bar series:
   - Verify `or_high` equals exact maximum high and `or_low` equals exact minimum low.
2. Supply a 16th bar at 09:45:00 with a new higher high; verify that `or_high` remains unchanged and ignores the 09:45 bar.
3. Test edge case where session has missing bars in the 09:30–09:45 window; verify `is_valid` is set to `False`.

## Acceptance Criteria

- [ ] `OR High`, `OR Low`, and `OR Width` are computed strictly from $[09:30, 09:45)$ bars.
- [ ] Opening range parameters are immutable (`frozen=True`) once calculated.
- [ ] Bars at or after 09:45:00 ET cannot modify the opening range under any condition.
- [ ] Daily range outputs are persisted to `data/processed/SPY/daily_ranges.parquet`.

## Notes

- Freezing the opening range before the first trading bar (09:45) is the core prerequisite for eliminating lookahead bias.
