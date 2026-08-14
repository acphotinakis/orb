"""
src.data.alpaca_client
======================
Authenticated Alpaca historical data client with exponential-backoff retry
and standard OHLCV DataFrame output.

This module is the sole integration point with the Alpaca ``alpaca-py``
library.  No other module in the ORB pipeline imports from ``alpaca.*``
directly — all Alpaca interaction flows through :class:`AlpacaDataClient`.

Authentication
--------------
API credentials are loaded in priority order:

1. Constructor arguments (``api_key``, ``secret_key``) — highest priority.
2. Environment variables / ``.env`` file:
   - Paper:  ``PAPER_APCA_API_KEY_ID`` / ``PAPER_APCA_API_SECRET_KEY``
   - Live:   ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``

The raw secret value is **never** logged.

Output Schema
-------------
:meth:`AlpacaDataClient.fetch_bars` always returns a DataFrame with the
following columns (all others are dropped):

=============  =========  ========================================
Column         dtype      Description
=============  =========  ========================================
timestamp      datetime64 Bar open time, UTC-aware
open           float64    Opening price
high           float64    Intraday high
low            float64    Intraday low
close          float64    Closing price
volume         float64    Total volume traded in the bar
vwap           float64    Volume-weighted average price (optional)
trade_count    Int64      Number of trades in the bar (optional)
=============  =========  ========================================

Usage
-----
    from src.data.alpaca_client import AlpacaDataClient
    from datetime import datetime, timezone

    client = AlpacaDataClient(is_paper=True)
    df = client.fetch_bars(
        symbol="SPY",
        start_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 5, tzinfo=timezone.utc),
        feed="iex",
    )
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from src.common.exceptions import DataFetchError
from src.common.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy Alpaca imports — wrapped to give a clean error when not installed
# ---------------------------------------------------------------------------
try:
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    _ALPACA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ALPACA_AVAILABLE = False
    DataFeed = None  # type: ignore[assignment, misc]
    StockHistoricalDataClient = None  # type: ignore[assignment, misc]
    StockBarsRequest = None  # type: ignore[assignment, misc]
    TimeFrame = None  # type: ignore[assignment, misc]
    TimeFrameUnit = None  # type: ignore[assignment, misc]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TIMEFRAME_MAP: dict[str, object] = {}  # populated lazily after import check
_STANDARD_COLUMNS: list[str] = [
    "timestamp", "open", "high", "low", "close", "volume", "vwap", "trade_count"
]
_MAX_RETRIES: int = 3
_BACKOFF_BASE_SECONDS: float = 2.0  # wait = base ^ attempt  (2, 4, 8 seconds)


def _get_timeframe(timeframe_str: str) -> object:
    """Map a timeframe string to the corresponding ``alpaca-py`` ``TimeFrame``.

    Args:
        timeframe_str: Timeframe identifier, e.g. ``"1Min"``.

    Returns:
        The matching ``alpaca.data.timeframe.TimeFrame`` instance.

    Raises:
        ValueError: If *timeframe_str* is not supported.
    """
    if not _ALPACA_AVAILABLE:
        raise ImportError(
            "alpaca-py is not installed. Run: pip install alpaca-py"
        )

    mapping = {
        "1Min": TimeFrame.Minute,
        "1min": TimeFrame.Minute,
        "1m":   TimeFrame.Minute,
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1H":   TimeFrame.Hour,
        "1D":   TimeFrame.Day,
    }
    if timeframe_str not in mapping:
        raise ValueError(
            f"Unsupported timeframe '{timeframe_str}'. "
            f"Supported: {list(mapping.keys())}"
        )
    return mapping[timeframe_str]


def _get_feed_enum(feed_str: str) -> object:
    """Map a feed string to an ``alpaca-py`` ``DataFeed`` enum member.

    Args:
        feed_str: Feed name, one of ``"iex"`` or ``"sip"`` (case-insensitive).

    Returns:
        The matching ``DataFeed`` enum value.

    Raises:
        ValueError: If *feed_str* is not ``"iex"`` or ``"sip"``.
    """
    if not _ALPACA_AVAILABLE:
        raise ImportError("alpaca-py is not installed.")

    feed_lower = feed_str.lower()
    if feed_lower == "iex":
        return DataFeed.IEX
    elif feed_lower == "sip":
        return DataFeed.SIP
    else:
        raise ValueError(
            f"Unsupported data feed '{feed_str}'. Must be 'iex' or 'sip'."
        )


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def _load_credentials(
    api_key: Optional[str],
    secret_key: Optional[str],
    is_paper: bool,
) -> tuple[str, str]:
    """Resolve Alpaca API credentials from arguments or environment.

    Args:
        api_key: Explicit API key, or ``None`` to read from environment.
        secret_key: Explicit secret key, or ``None`` to read from environment.
        is_paper: When ``True``, read ``PAPER_APCA_*`` env vars; otherwise
            read ``APCA_*`` vars.

    Returns:
        2-tuple ``(api_key, secret_key)`` of validated, non-empty strings.

    Raises:
        DataFetchError: If either credential is missing after all lookups.
    """
    load_dotenv()

    if api_key is None:
        env_key = "PAPER_APCA_API_KEY_ID" if is_paper else "APCA_API_KEY_ID"
        api_key = os.getenv(env_key, "")
    if secret_key is None:
        env_sec = "PAPER_APCA_API_SECRET_KEY" if is_paper else "APCA_API_SECRET_KEY"
        secret_key = os.getenv(env_sec, "")

    missing: list[str] = []
    if not api_key:
        missing.append("PAPER_APCA_API_KEY_ID" if is_paper else "APCA_API_KEY_ID")
    if not secret_key:
        missing.append("PAPER_APCA_API_SECRET_KEY" if is_paper else "APCA_API_SECRET_KEY")

    if missing:
        raise DataFetchError(
            f"Missing Alpaca API credentials: {missing}. "
            "Set them in your .env file or as environment variables.",
        )

    # Log key prefix only — never log the full secret
    logger.debug("Alpaca credentials loaded. Key prefix: %s***", api_key[:4])
    return api_key, secret_key


# ---------------------------------------------------------------------------
# DataFrame normalisation
# ---------------------------------------------------------------------------

def _normalise_bars_df(raw_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Convert a raw Alpaca bars DataFrame to the standard ORB schema.

    Alpaca's ``bars.df`` returns a MultiIndex ``(symbol, timestamp)`` when a
    single symbol is requested via the dict-based interface.  This function
    handles both the MultiIndex and the flat-index cases.

    Args:
        raw_df: Raw DataFrame as returned by ``StockHistoricalDataClient``.
        symbol: Ticker symbol (used only for logging).

    Returns:
        Normalised DataFrame with columns from :data:`_STANDARD_COLUMNS`,
        sorted chronologically by ``timestamp``, index reset.
    """
    if raw_df.empty:
        logger.warning("Empty bars DataFrame returned for %s.", symbol)
        return pd.DataFrame(columns=_STANDARD_COLUMNS)

    df = raw_df.copy()

    # Flatten MultiIndex (symbol, timestamp) → flat with timestamp column
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
        # Drop symbol level if present
        if "symbol" in df.columns:
            df = df.drop(columns=["symbol"])
    elif df.index.name == "timestamp":
        df = df.reset_index()
    else:
        df = df.reset_index(drop=True)

    # Ensure timestamp column exists
    if "timestamp" not in df.columns:
        raise DataFetchError(
            "Alpaca response missing 'timestamp' column after index reset.",
            ticker=symbol,
        )

    # Normalise timestamp to UTC-aware datetime
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    else:
        ts = ts.dt.tz_convert("UTC")
    df["timestamp"] = ts

    # Coerce numeric columns
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "vwap" in df.columns:
        df["vwap"] = pd.to_numeric(df["vwap"], errors="coerce")

    if "trade_count" in df.columns:
        df["trade_count"] = pd.to_numeric(
            df["trade_count"], errors="coerce"
        ).astype("Int64")

    # Keep only the standard columns that are present; add missing ones as NaN
    out_cols: list[str] = []
    for col in _STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
        out_cols.append(col)

    df = df[out_cols].sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class AlpacaDataClient:
    """Authenticated Alpaca historical bar client with retry logic.

    This class wraps ``alpaca.data.historical.StockHistoricalDataClient`` and
    provides a resilient, retry-capable interface that outputs data in the
    standardised ORB OHLCV schema.

    Args:
        api_key: Alpaca API key.  If ``None``, read from environment.
        secret_key: Alpaca secret key.  If ``None``, read from environment.
        is_paper: When ``True`` (default), use paper-trading credentials
            (``PAPER_APCA_*``).  When ``False``, use live credentials.
        max_retries: Maximum number of fetch attempts before raising.
            Defaults to 3.
        backoff_base: Base in seconds for exponential backoff between retries.
            Retry waits are ``backoff_base ^ attempt`` seconds.

    Raises:
        ImportError: If ``alpaca-py`` is not installed.
        DataFetchError: If credentials are missing or invalid.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        is_paper: bool = True,
        max_retries: int = _MAX_RETRIES,
        backoff_base: float = _BACKOFF_BASE_SECONDS,
    ) -> None:
        if not _ALPACA_AVAILABLE:
            raise ImportError(
                "alpaca-py is not installed. Run: pip install alpaca-py>=0.30.0"
            )

        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._is_paper = is_paper

        resolved_key, resolved_secret = _load_credentials(
            api_key, secret_key, is_paper
        )

        self._client = StockHistoricalDataClient(
            api_key=resolved_key,
            secret_key=resolved_secret,
        )
        logger.info(
            "AlpacaDataClient initialised (mode=%s).",
            "paper" if is_paper else "live",
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_bars(
        self,
        symbol: str = "SPY",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        timeframe: str = "1Min",
        feed: str = "iex",
    ) -> pd.DataFrame:
        """Fetch historical 1-minute OHLCV bars from Alpaca.

        Internally builds a :class:`StockBarsRequest`, executes it with
        exponential-backoff retry on transient failures (timeouts, HTTP 429),
        and returns a normalised :data:`_STANDARD_COLUMNS` DataFrame.

        Args:
            symbol: Ticker symbol (e.g. ``"SPY"``).
            start_date: Inclusive start datetime.  Must be UTC-aware or naive
                UTC.  If ``None``, defaults to 5 trading days before *end_date*.
            end_date: Exclusive end datetime.  If ``None``, defaults to the
                most recent completed minute (``now - 16 min`` to avoid
                IEX 15-minute delay).
            timeframe: Bar resolution string, e.g. ``"1Min"``.
            feed: Data feed — ``"iex"`` (free tier) or ``"sip"`` (paid).

        Returns:
            DataFrame with columns ``timestamp``, ``open``, ``high``, ``low``,
            ``close``, ``volume``, ``vwap``, ``trade_count`` sorted
            chronologically.  Empty DataFrame if no bars are available.

        Raises:
            DataFetchError: After all retry attempts are exhausted.
            ValueError: If *timeframe* or *feed* are unrecognised.
        """
        # ── Default date bounds ──────────────────────────────────────
        if end_date is None:
            end_date = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            # Subtract 16 minutes to avoid IEX 15-minute delay on current day
            from datetime import timedelta
            end_date = end_date - timedelta(minutes=16)

        if start_date is None:
            from datetime import timedelta
            start_date = end_date - timedelta(days=5)

        # ── Ensure UTC-awareness ─────────────────────────────────────
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        tf = _get_timeframe(timeframe)
        feed_enum = _get_feed_enum(feed)

        logger.info(
            "Fetching %s bars for %s | %s → %s | feed=%s",
            timeframe, symbol,
            start_date.strftime("%Y-%m-%d %H:%M UTC"),
            end_date.strftime("%Y-%m-%d %H:%M UTC"),
            feed.upper(),
        )

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start_date,
            end=end_date,
            feed=feed_enum,
        )

        # ── Retry loop ───────────────────────────────────────────────
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                bars = self._client.get_stock_bars(request)
                raw_df: pd.DataFrame = bars.df
                break  # success
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc)

                is_rate_limit = "429" in exc_str or "rate limit" in exc_str.lower()
                is_transient = is_rate_limit or any(
                    kw in exc_str.lower()
                    for kw in ("timeout", "connection", "reset", "502", "503", "504")
                )

                if not is_transient or attempt == self._max_retries:
                    raise DataFetchError(
                        f"Alpaca API request failed (attempt {attempt}/{self._max_retries}): {exc}",
                        ticker=symbol,
                        start_date=start_date.strftime("%Y-%m-%d"),
                        end_date=end_date.strftime("%Y-%m-%d"),
                    ) from exc

                wait = self._backoff_base ** attempt
                logger.warning(
                    "Alpaca API error (attempt %d/%d) — retrying in %.1fs. Error: %s",
                    attempt, self._max_retries, wait, exc_str,
                )
                time.sleep(wait)

        df = _normalise_bars_df(raw_df, symbol)

        logger.info(
            "Fetched %d bars for %s. Range: %s → %s",
            len(df), symbol,
            df["timestamp"].min() if not df.empty else "N/A",
            df["timestamp"].max() if not df.empty else "N/A",
        )
        return df
