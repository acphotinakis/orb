"""
src.data — Historical data ingestion, validation, and processing pipeline.

Provides:
- AlpacaDataClient: authenticated Alpaca API wrapper with retry logic (TASK-005)
- DataFetcher: raw 1-minute bar download and Parquet disk cache (TASK-006)
- validate_and_clean_bars: OHLCV integrity validator and anomaly cleaner (TASK-007)
- DataProcessor: RTH session filter, timezone normalization, session tagging (TASK-008)
"""
