# TASK-003: Timezone & Market Session Utilities

## Objective

Implement centralized timezone conversion and Regular Trading Hours (RTH) market session utilities in `src/common/time_utils.py` to ensure exact session boundary enforcement and robust handling of Daylight Saving Time (DST) transitions.

## Scope

### In Scope

- Timezone localization and conversion helpers (`UTC` $\leftrightarrow$ `America/New_York`).
- Regular Trading Hours (RTH) boundary definitions:
  - Market Open: `09:30:00 America/New_York`
  - Opening Range End: `09:44:59 America/New_York` (first 15 minutes)
  - Active Trading Window: `09:45:00 – 15:58:59 America/New_York`
  - Force Exit Timestamp: `15:59:00 America/New_York`
  - Session Close: `16:00:00 America/New_York`
- Session identification and partitioning helpers for pandas `DatetimeIndex` / `Series`.
- Verification of historical DST switch dates (e.g. March and November clock shifts).

### Out of Scope

- Fetching data from Alpaca API.
- Executing trade orders.

## Dependencies

- TASK-001
- TASK-002

## Requirements

1. All incoming bar timestamps must be converted and localized to `America/New_York`.
2. Provide functions to filter out pre-market (< 09:30:00 ET) and after-hours (> 16:00:00 ET) bars.
3. Classify bars into three discrete session phases:
   - `OPENING_RANGE`: $[09:30:00, 09:45:00)$
   - `TRADING`: $[09:45:00, 15:59:00)$
   - `FORCE_EXIT`: $[15:59:00, 16:00:00]$
4. Correctly identify distinct trading calendar days (`session_id` or `session_date`), preventing UTC midnight boundaries from splitting an intraday session.
5. Handle half-day market sessions (e.g., Black Friday 13:00 ET close) safely.

## Implementation Details

1. Create `src/common/time_utils.py`.
2. Implement key helper functions:
   - `ensure_eastern_time(dt_series: pd.Series) -> pd.Series`: Localizes naive timestamps or converts UTC timestamps to `America/New_York`.
   - `filter_rth(df: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame`: Filters rows to $09:30 \le t \le 16:00$ ET.
   - `is_opening_range_bar(timestamp: pd.Timestamp, or_minutes: int = 15) -> bool`: Returns `True` iff $09:30 \le \text{time} < 09:30 + \text{or\_minutes}$.
   - `get_session_bounds(session_date: datetime.date, tz: str = "America/New_York") -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]`: Returns `(market_open, or_end, force_exit, market_close)` as localized Timestamps.

## Interfaces / Contracts

```python
# src/common/time_utils.py

import pandas as pd
from datetime import date
from typing import Tuple

EASTERN_TZ = "America/New_York"

def to_eastern(df: pd.DataFrame, time_col: str = "timestamp") -> pd.DataFrame:
    """Ensure timestamp column is timezone-aware America/New_York."""
    ...

def filter_rth(df: pd.DataFrame, time_col: str = "timestamp") -> pd.DataFrame:
    """Filter DataFrame to Regular Trading Hours (09:30 - 16:00 ET)."""
    ...

def get_session_timestamps(
    session_date: date,
    or_minutes: int = 15,
    force_exit_time: str = "15:59:00",
    tz: str = EASTERN_TZ,
) -> Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """
    Returns:
      (market_open, or_end, force_exit, market_close)
    """
    ...
```

## Data / File Changes

- Create `src/common/time_utils.py`

## Validation

1. Verify timestamp conversions for sample UTC timestamps during standard time (EST, UTC-5) and daylight saving time (EDT, UTC-4).
2. Validate that 09:30:00 and 09:44:00 bars are identified as Opening Range, while 09:45:00 is identified as Trading window.
3. Validate that 15:59:00 bar triggers the force exit window.

## Acceptance Criteria

- [ ] `to_eastern()` reliably handles naive, UTC-aware, and already-Eastern timestamps.
- [ ] `filter_rth()` discards all pre-market ($< 09:30$) and post-market ($> 16:00$) bars.
- [ ] Session boundary timestamps are mathematically exact down to the microsecond level.
- [ ] Zero timezone ambiguity or DST shifting errors across transition dates.

## Notes

- SPY trades on US equity market schedules (NYSE/NASDAQ calendar).
- Standardizing time calculations across all modules is critical to avoid look-ahead bias and misaligned opening ranges.
