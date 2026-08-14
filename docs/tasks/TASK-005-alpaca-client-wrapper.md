# TASK-005: Alpaca Historical Data Client Wrapper

## Objective

Implement a resilient Alpaca API client wrapper in `src/data/alpaca_client.py` using `alpaca-py` to securely authenticate, query historical stock bars, handle pagination, and manage rate-limit retries.

## Scope

### In Scope

- Credential extraction and validation from `.env` or system environment variables:
  - Paper: `PAPER_APCA_API_KEY_ID`, `PAPER_APCA_API_SECRET_KEY`
  - Live: `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`
- Initialization of `alpaca.data.historical.StockHistoricalDataClient`.
- Support for data feed selection: IEX (`DataFeed.IEX`) or SIP (`DataFeed.SIP`).
- Automatic pagination across long date ranges with chunking.
- Exponential backoff retry logic for network timeouts and HTTP 429 rate limit responses.
- Output conversion from Alpaca bar objects to standardized pandas DataFrames.

### Out of Scope

- Disk caching or local storage (handled in TASK-006).
- RTH session filtering and data cleaning (handled in TASK-007 and TASK-008).

## Dependencies

- TASK-001
- TASK-002
- TASK-004

## Requirements

1. Securely load API keys via `python-dotenv` without logging raw secret values.
2. Allow selecting paper vs. live trading keys via configuration or constructor parameter (`is_paper: bool = True`).
3. Query 1-minute historical bars for `SPY` between specified `start` and `end` datetime bounds.
4. Normalize Alpaca bar structures into a standard DataFrame with columns:
   - `timestamp` (UTC datetime)
   - `open` (float)
   - `high` (float)
   - `low` (float)
   - `close` (float)
   - `volume` (float / int)
   - `trade_count` (int, if present)
   - `vwap` (float, if present)
5. Implement retry mechanism (up to 3 retries with exponential backoff) if Alpaca API returns rate limit or transient connection errors.

## Implementation Details

1. Create `src/data/alpaca_client.py`.
2. Implement `AlpacaDataClient` class:
   ```python
   class AlpacaDataClient:
       def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None, is_paper: bool = True):
           ...
       def get_stock_bars(
           self,
           symbol: str,
           start: datetime,
           end: datetime,
           timeframe: str = "1Min",
           feed: str = "iex",
       ) -> pd.DataFrame:
           ...
   ```
3. Ensure timezone awareness of query parameters (Alpaca expects UTC or ISO 8601 timestamps).

## Interfaces / Contracts

```python
# src/data/alpaca_client.py

from datetime import datetime
from typing import Optional
import pandas as pd

class AlpacaDataClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        is_paper: bool = True,
    ):
        ...

    def fetch_bars(
        self,
        symbol: str = "SPY",
        start_date: datetime = ...,
        end_date: datetime = ...,
        timeframe: str = "1Min",
        feed: str = "iex",
    ) -> pd.DataFrame:
        """
        Returns DataFrame with columns:
        ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'vwap', 'trade_count']
        sorted chronologically.
        """
        ...
```

## Data / File Changes

- Create `src/data/alpaca_client.py`

## Validation

1. Mock `StockHistoricalDataClient` responses in a unit test to verify error handling, retry logic, and column renaming.
2. Perform a test fetch of 1-day historical SPY bars using credentials from `.env` (if connectivity is available) and assert DataFrame structure and row counts.

## Acceptance Criteria

- [ ] `AlpacaDataClient` cleanly authenticates using `.env` keys.
- [ ] Both IEX and SIP feeds are supported via configuration.
- [ ] Returned DataFrame conforms to the required standard OHLCV schema.
- [ ] Retries and backoff execute cleanly without crashing on transient network issues.

## Notes

- Free Alpaca tier accounts only have access to IEX feed and historical data delayed by 15 minutes for current day. Ensure queries default to valid past windows.
