"""
src.data.fetcher
================
High-level raw data ingestion pipeline with Parquet disk caching.

This module sits above :mod:`src.data.alpaca_client` and is responsible for:

1. **Cache lookup** — checking whether the requested ``(symbol, start, end)``
   window already exists on disk in ``data/raw/{symbol}/``.
2. **Fetch** — calling :class:`~src.data.alpaca_client.AlpacaDataClient`
   only when the cache is cold or ``force_refresh=True``.
3. **Merge & deduplicate** — when partial cache hits exist, merging the
   cached portion with the fresh Alpaca payload.
4. **Persist** — writing the consolidated bars to a deterministically named
   Parquet file using the ``pyarrow`` engine.

Cache File Naming
-----------------
Files are written to::

    data/raw/{symbol}/{feed}/{timeframe}/{symbol}_{timeframe}_{YYYYMMDD}_{YYYYMMDD}.parquet

where the two dates are the ISO start and end of the *actual downloaded*
data range (from the earliest to the latest timestamp in the file, not the
requested bounds).

Parquet is used over CSV for:

* Lossless ``datetime64[ns, UTC]`` timestamp encoding
* Efficient columnar reads for large date ranges
* Native ``Int64`` nullable integer type for ``trade_count``

Usage
-----
    from src.common.config import load_config
    from src.data.fetcher import DataFetcher
    from datetime import datetime, timezone

    cfg = load_config()
    fetcher = DataFetcher(config=cfg)

    df = fetcher.fetch_and_cache(
        symbol="SPY",
        start_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
    )
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal
import sys

import pandas as pd

from src.common.config import AppConfig
from src.common.exceptions import DataFetchError
from src.common.logger import get_logger
from src.data.alpaca_client import AlpacaDataClient

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PARQUET_ENGINE: Literal["pyarrow", "fastparquet", "auto"] = "pyarrow"
_CACHE_FILENAME_TEMPLATE: str = "{symbol}_{timeframe}_{start}_{end}.parquet"
_OHLCV_REQUIRED_COLS: list[str] = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cache_filename(symbol: str, timeframe: str, start: datetime, end: datetime) -> str:
    """Return the deterministic Parquet filename for a ``(symbol, start, end)`` window.

    Args:
        symbol: Ticker symbol.
        start: Window start (date component is used).
        end: Window end (date component is used).

    Returns:
        Filename string, e.g. ``"SPY_1min_20240102_20241231.parquet"``.
    """
    return _CACHE_FILENAME_TEMPLATE.format(
        symbol=symbol.upper(),
        timeframe=timeframe.upper(),
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )


def _parse_cache_dates(filename: str) -> tuple[datetime, datetime] | None:
    """Extract the start and end dates encoded in a cache filename.

    Args:
        filename: Parquet filename, e.g. ``"SPY_1min_20240102_20241231.parquet"``.

    Returns:
        ``(start_dt, end_dt)`` as UTC-aware datetimes, or ``None`` if the
        filename does not match the expected pattern.
    """
    match = re.search(r"(\d{8})_(\d{8})\.parquet$", filename)
    if not match:
        return None
    try:
        start = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=timezone.utc)
        return start, end
    except ValueError:
        return None


def _find_covering_cache_file(
    cache_dir: Path,
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
) -> Optional[Path]:
    """Search *cache_dir* for a cached file that fully covers the requested window.

    A file is considered covering when its encoded date range satisfies
    ``file_start <= start_date`` AND ``file_end >= end_date``.

    Args:
        cache_dir: Directory to search.
        symbol: Ticker symbol used as filename prefix.
        timeframe: Timeframe of the stock data
        start_date: Requested window start (UTC-aware).
        end_date: Requested window end (UTC-aware).

    Returns:
        Path to the first matching file, or ``None`` if no covering cache exists.
    """
    pattern = f"{symbol.upper()}_{timeframe}_*.parquet"
    candidates = sorted(cache_dir.glob(pattern))
    for candidate in candidates:
        dates = _parse_cache_dates(candidate.name)
        if dates is None:
            continue
        file_start, file_end = dates
        # Compare at date boundary level (inclusive date coverage)
        if (
            file_start.date() <= start_date.date()
            and file_end.date() >= end_date.date()
        ):
            return candidate
    return None


def _load_parquet(path: Path) -> pd.DataFrame:
    """Read a cached Parquet file and return a DataFrame.

    Args:
        path: Path to the Parquet file.

    Returns:
        DataFrame with ``timestamp`` as a UTC-aware ``datetime64`` column.

    Raises:
        DataFetchError: If the file cannot be read or is missing required columns.
    """
    try:
        df = pd.read_parquet(path, engine=_PARQUET_ENGINE)  # type: ignore
        _validate_columns(df, context=f"load_parquet({path.name})")
        logger.debug("Loaded %d bars from cache: %s", len(df), path.name)
        return df
    except Exception as exc:
        raise DataFetchError(f"Failed to read cache file '{path}': {exc}") from exc


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Persist a bars DataFrame to Parquet using the ``pyarrow`` engine and zstd compression.

    Args:
        df: Bars DataFrame to write.
        path: Destination path (parent directories are created automatically).

    Raises:
        DataFetchError: If the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, engine=_PARQUET_ENGINE, compression="zstd", index=False)
        logger.debug("Cached %d bars → %s (zstd compression)", len(df), path.name)
    except Exception as exc:
        raise DataFetchError(f"Failed to write cache file '{path}': {exc}") from exc


def _merge_and_deduplicate(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate multiple bar DataFrames and remove duplicate timestamps.

    Args:
        frames: List of DataFrames, each with a ``timestamp`` column.

    Returns:
        Single chronologically sorted DataFrame with unique timestamps.
    """
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["timestamp"])
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined


def _validate_columns(df: pd.DataFrame, context: str) -> None:
    """Assert that required OHLCV columns are present.

    Args:
        df: DataFrame to validate.
        context: Human-readable label used in the error message.

    Raises:
        DataFetchError: If any required column is missing.
    """
    missing = [c for c in _OHLCV_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise DataFetchError(
            f"[{context}] Required columns missing from bar data: {missing}"
        )


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class DataFetcher:
    """High-level raw data ingestion pipeline with Parquet disk caching.

    Wraps :class:`~src.data.alpaca_client.AlpacaDataClient` with a
    cache-first read strategy.  Callers interact exclusively with this class
    for all data acquisition needs; they do not call ``AlpacaDataClient``
    directly.

    Args:
        config: Loaded :class:`~src.common.config.AppConfig` instance.
        client: Optional pre-constructed :class:`AlpacaDataClient`.  If
            ``None``, a new client is built from *config* on first fetch.
        cache_dir: Optional directory path for raw Parquet cache.  When
            provided by :class:`~src.common.paths.PathManager`, this
            overrides the default path construction.
    """

    def __init__(
        self,
        config: AppConfig,
        client: Optional[AlpacaDataClient] = None,
        cache_dir: Optional[Path] = None,
    ) -> None:
        """Initialise the DataFetcher.

        Args:
            config: Loaded AppConfig instance.
            client: Optional pre-constructed AlpacaDataClient.
            cache_dir: Directory for raw Parquet cache files. When provided
                (from PathManager), this overrides any default path logic.
        """
        self._config = config
        self._client: Optional[AlpacaDataClient] = client
        # Accept injected path from PathManager; fall back to a sensible default
        self._cache_dir: Path = (
            cache_dir
            if cache_dir is not None
            else Path("data")
            / "raw"
            / config.data.symbol
            / config.data.feed
            / config.data.timeframe
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> AlpacaDataClient:
        """Lazily initialise the Alpaca client from config credentials.

        Returns:
            The active :class:`AlpacaDataClient`.
        """
        if self._client is None:
            logger.debug("Initialising AlpacaDataClient from config credentials.")
            self._client = AlpacaDataClient(is_paper=self._config.data.is_paper)
        return self._client

    def _fetch_from_alpaca(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """Delegate a bar fetch to AlpacaDataClient.

        Args:
            symbol: Ticker symbol.
            start_date: UTC-aware start datetime.
            end_date: UTC-aware end datetime.

        Returns:
            Raw bars DataFrame from Alpaca.
        """
        client = self._get_client()
        return client.fetch_bars(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            timeframe=self._config.data.timeframe,
            feed=self._config.data.feed,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_and_cache(
        self,
        symbol: str = "SPY",
        timeframe: str = "15Min",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        force_refresh: bool = False,
        plot_chart: bool = False,
    ) -> pd.DataFrame:
        """Retrieve 1-minute bars, using disk cache when available.

        **Behaviour:**

        1. If a covering cache file exists on disk and ``force_refresh=False``,
           load from Parquet and return immediately — **no Alpaca API call**.
        2. Otherwise fetch from Alpaca, merge with any partial cache if
           present, deduplicate, and write the consolidated result to disk.

        Args:
            symbol: Ticker symbol.  Defaults to ``"SPY"``.
            timeframe: timeframe of the data. Defaults to ``"15Min"``.
            start_date: Inclusive start datetime (UTC or naive-UTC).
                Defaults to 365 days before *end_date*.
            end_date: Exclusive end datetime (UTC or naive-UTC).
                Defaults to ``now - 16 min`` (IEX 15-min delay buffer).
            force_refresh: When ``True``, bypass any existing cache and
                re-download from Alpaca.  The cache file is overwritten.

        Returns:
            Chronologically sorted DataFrame with
            ``['timestamp', 'open', 'high', 'low', 'close', 'volume',
            'vwap', 'trade_count']`` columns and UTC-aware timestamps.

        Raises:
            DataFetchError: If Alpaca fetch fails or cache I/O errors occur.
        """
        from datetime import timedelta

        # ── Resolve default date bounds ──────────────────────────────
        if end_date is None:
            end_date = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            end_date = end_date - timedelta(minutes=16)

        if start_date is None:
            start_date = end_date - timedelta(days=365)

        # Ensure UTC-aware
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        symbol = symbol.upper()
        cache_dir = self._cache_dir

        # ── Cache lookup ─────────────────────────────────────────────
        if not force_refresh:
            covering = _find_covering_cache_file(
                cache_dir, symbol, timeframe, start_date, end_date
            )
            if covering is not None:
                logger.info(
                    "Cache hit for %s %s [%s → %s] — loading from %s",
                    symbol,
                    timeframe,
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d"),
                    covering.name,
                )
                df = _load_parquet(covering)
                # Trim to requested window
                ts = df["timestamp"]
                mask = (ts >= start_date) & (ts <= end_date)
                df = df.loc[mask].reset_index(drop=True)
                logger.info(
                    "Cache loaded: %d bars for %s [%s → %s].",
                    len(df),
                    symbol,
                    df["timestamp"].min() if not df.empty else "N/A",
                    df["timestamp"].max() if not df.empty else "N/A",
                )
                return df
            else:
                logger.info(
                    "Cache miss for %s [%s → %s] — fetching from Alpaca.",
                    symbol,
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d"),
                )
        else:
            logger.info(
                "force_refresh=True — bypassing cache for %s [%s → %s].",
                symbol,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )

        # ── Fetch from Alpaca ────────────────────────────────────────
        fresh_df = self._fetch_from_alpaca(symbol, start_date, end_date)

        if fresh_df.empty:
            logger.warning(
                "Alpaca returned 0 bars for %s [%s → %s].",
                symbol,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )
            return fresh_df

        _validate_columns(fresh_df, context="AlpacaDataClient.fetch_bars")

        # ── Merge with any partial existing cache ────────────────────
        frames_to_merge: list[pd.DataFrame] = [fresh_df]
        existing_files = sorted(
            cache_dir.glob(f"{symbol}_{self._config.data.timeframe}_*.parquet")
        )
        for existing in existing_files:
            try:
                cached_df = _load_parquet(existing)
                frames_to_merge.append(cached_df)
                logger.debug(
                    "Merging %d cached bars from %s with fresh data.",
                    len(cached_df),
                    existing.name,
                )
                # Remove old file — will be replaced by consolidated write
                existing.unlink()
            except DataFetchError:
                logger.warning(
                    "Could not read existing cache file %s; skipping.", existing.name
                )

        consolidated = _merge_and_deduplicate(frames_to_merge)

        # ── Persist consolidated result ──────────────────────────────
        actual_start = consolidated["timestamp"].min()
        actual_end = consolidated["timestamp"].max()
        fname = _cache_filename(symbol, timeframe, actual_start, actual_end)
        out_path = cache_dir / fname

        _write_parquet(consolidated, out_path)

        logger.info(
            "Cached %d bars for %s → %s (range: %s → %s).",
            len(consolidated),
            symbol,
            fname,
            actual_start,
            actual_end,
        )

        # Trim to requested window before returning
        ts = consolidated["timestamp"]
        mask = (ts >= start_date) & (ts <= end_date)
        return consolidated.loc[mask].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Convenience: load all cached bars regardless of exact date range
    # ------------------------------------------------------------------

    def load_cached(self, symbol: str = "SPY") -> pd.DataFrame:
        """Load and merge all cached Parquet files for *symbol*.

        Useful for offline analysis without specifying an exact date window.

        Args:
            symbol: Ticker symbol.

        Returns:
            Merged, deduplicated DataFrame of all cached bars for *symbol*,
            or an empty DataFrame if no cache files exist.
        """
        symbol = symbol.upper()
        files = sorted(
            self._cache_dir.glob(f"{symbol}_{self._config.data.timeframe}_*.parquet")
        )
        if not files:
            logger.warning(
                "No cached files found for %s in %s.", symbol, self._cache_dir
            )
            return pd.DataFrame()

        frames = [_load_parquet(f) for f in files]
        merged = _merge_and_deduplicate(frames)
        logger.info(
            "Loaded %d total cached bars for %s from %d file(s).",
            len(merged),
            symbol,
            len(files),
        )
        return merged
