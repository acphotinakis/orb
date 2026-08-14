# TASK-006: Raw Historical Data Ingestion & Disk Caching

## Objective

Build the high-level raw data ingestion pipeline in `src/data/fetcher.py` that downloads historical 1-minute bars for SPY, partitions the data, and persists it into the local cache directory (`data/raw/SPY/`) using Apache Parquet (with CSV fallback).

## Scope

### In Scope

- Querying raw 1-minute historical SPY data across arbitrary date ranges (e.g. 90 days, 1 year, multiple years).
- Chunking large multi-year date ranges into manageable monthly or yearly download batches.
- Efficient disk caching to `data/raw/SPY/` in Parquet format (`SPY_1min_{start}_{end}.parquet`).
- Cache lookup mechanism: if requested date range is already cached on disk, bypass Alpaca API calls and load directly from storage.
- CLI argument support for force-refresh (`--refresh-cache`).

### Out of Scope

- RTH filtering or dropping pre-market/after-market data (raw cache must preserve all returned bars).
- Feature calculations or signal processing.

## Dependencies

- TASK-001
- TASK-002
- TASK-004
- TASK-005

## Requirements

1. Target asset: `SPY` (SPDR S&P 500 ETF Trust).
2. Timeframe: 1-minute bars (`1Min`).
3. Store files in `data/raw/SPY/` with deterministic naming: `SPY_1min_{YYYYMMDD}_{YYYYMMDD}.parquet`.
4. Cache lookup:
   - Check if cached file exists and spans the required date range.
   - If present and `force_refresh=False`, load from Parquet.
   - If missing or partial, query Alpaca via `AlpacaDataClient`, merge chronologically, deduplicate, and write to disk.
5. Provide a summary log reporting total bars fetched, date range, and cache status.

## Implementation Details

1. Create `src/data/fetcher.py`.
2. Implement `DataFetcher` class:
   ```python
   class DataFetcher:
       def __init__(self, config: AppConfig, client: Optional[AlpacaDataClient] = None):
           ...
       def get_raw_bars(
           self,
           symbol: str = "SPY",
           start_date: datetime = ...,
           end_date: datetime = ...,
           force_refresh: bool = False,
       ) -> pd.DataFrame:
           ...
   ```
3. Use `pyarrow` engine for reading and writing Parquet files to maintain precision of datetime timestamps and numerical floats.

## Interfaces / Contracts

```python
# src/data/fetcher.py

import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional
from src.common.config import AppConfig
from src.data.alpaca_client import AlpacaDataClient

class DataFetcher:
    def __init__(self, config: AppConfig, client: Optional[AlpacaDataClient] = None):
        ...

    def fetch_and_cache(
        self,
        symbol: str = "SPY",
        start_date: datetime = ...,
        end_date: datetime = ...,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Retrieves 1-minute bars. Returns DataFrame with UTC timestamps.
        Persists to data/raw/{symbol}/.
        """
        ...
```

## Data / File Changes

- Create `src/data/fetcher.py`
- Writes files to `data/raw/SPY/`

## Validation

1. Run `DataFetcher` with a mock or live 5-day window; verify that the file is created in `data/raw/SPY/`.
2. Run the call a second time; verify from logs that the data is loaded from disk cache without making an API request.
3. Test with `force_refresh=True` and verify that the cache file is re-written.

## Acceptance Criteria

- [ ] Raw data is cached under `data/raw/SPY/` in valid Parquet format.
- [ ] Subsequent queries covering the same date range load from disk without network calls.
- [ ] Timestamps and bar data remain untruncated and identical to Alpaca source outputs.

## Notes

- Raw caching saves API rate limits and enables fast, repeatable offline backtesting.
