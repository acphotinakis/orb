# TASK-008: RTH Session Processor & Processed Dataset Builder

## Objective

Implement the session-level dataset processor in `src/data/processor.py` that ingests validated raw bars, normalizes timestamps to `America/New_York`, filters data strictly to Regular Trading Hours (RTH 09:30–16:00 ET), partitions the data by trading day, and writes the clean dataset to `data/processed/SPY/sessions.parquet`.

## Scope

### In Scope

- Timezone transformation of validated bars to `America/New_York`.
- Filtering strictly to Regular Trading Hours (09:30:00 to 16:00:00 ET).
- Partitioning by distinct trading date (`session_date` / `session_id`).
- Adding session metadata columns:
  - `session_id`: String formatted as `YYYY-MM-DD`
  - `minute_of_day`: Integer indexing minute from market open ($0 \dots 389$)
  - `is_opening_range`: Boolean (`True` for $09:30 \le t < 09:45$)
  - `is_trading_window`: Boolean (`True` for $09:45 \le t < 15:59$)
  - `is_force_exit`: Boolean (`True` for $t = 15:59$)
- Persisting output to `data/processed/SPY/sessions.parquet`.

### Out of Scope

- Generating trading signals or executing orders.

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-006
- TASK-007

## Requirements

1. Convert all timestamps to `America/New_York`.
2. Filter out all bars before `09:30:00 ET` and all bars after `16:00:00 ET`.
3. Discard sessions with fewer than 15 opening-range bars (incomplete open).
4. Assign unambiguous session identifiers to each trading day.
5. Save the output to `data/processed/SPY/sessions.parquet` with snappy compression for high read throughput during backtesting.

## Implementation Details

1. Create `src/data/processor.py`.
2. Implement `DataProcessor` class:
   ```python
   class DataProcessor:
       def __init__(self, config: AppConfig):
           ...
       def process_raw_dataset(
           self,
           raw_df: pd.DataFrame,
           output_path: Optional[Path] = None,
       ) -> pd.DataFrame:
           ...
   ```
3. Vectorize timestamp filtering and session tagging using `pandas` and `src/common/time_utils.py`.

## Interfaces / Contracts

```python
# src/data/processor.py

import pandas as pd
from pathlib import Path
from typing import Optional
from src.common.config import AppConfig

class DataProcessor:
    def __init__(self, config: AppConfig):
        ...

    def process(
        self,
        raw_df: pd.DataFrame,
        save_to_disk: bool = True,
    ) -> pd.DataFrame:
        """
        Takes raw/validated bars, normalizes timezone, filters RTH,
        adds session columns, and writes to data/processed/SPY/sessions.parquet.

        Output schema:
        ['session_id', 'timestamp', 'open', 'high', 'low', 'close', 'volume',
         'minute_of_day', 'is_opening_range', 'is_trading_window', 'is_force_exit']
        """
        ...
```

## Data / File Changes

- Create `src/data/processor.py`
- Writes to `data/processed/SPY/sessions.parquet`

## Validation

1. Process a sample multi-day dataset.
2. Verify that all resulting rows have timestamps between 09:30:00 and 16:00:00 America/New_York.
3. Verify that `is_opening_range` is `True` for exactly the 15 bars from 09:30 to 09:44.
4. Verify that `data/processed/SPY/sessions.parquet` can be read back with identical schema and values.

## Acceptance Criteria

- [ ] All timestamps in the output dataset are timezone-aware `America/New_York`.
- [ ] No pre-market or post-market bars remain.
- [ ] Session partitions are contiguous and accurately labeled with `session_id`.
- [ ] `data/processed/SPY/sessions.parquet` is successfully written and readable.

## Notes

- Processed sessions serve as the canonical input for strategy calculation and backtest replay.
