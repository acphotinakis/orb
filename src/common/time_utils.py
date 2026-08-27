"""
src.common.time_utils
=====================
Centralized timezone conversion and Regular Trading Hours (RTH) session
utilities for the SPY Opening Range Breakout (ORB) quantitative system.

All timestamps across the pipeline are normalised to ``America/New_York``
(the NYSE/NASDAQ trading timezone) via this module.  No other module should
perform timezone conversion directly; they should delegate here instead.

Session Phase Classification
-----------------------------
Every 1-minute bar inside RTH belongs to exactly one of three phases:

+------------------+-------------------------------+-----------------------------+
| Phase            | Time Range (ET, inclusive)    | Meaning                     |
+==================+===============================+=============================+
| OPENING_RANGE    | [09:30:00, 09:45:00)          | Opening range construction  |
+------------------+-------------------------------+-----------------------------+
| TRADING          | [09:45:00, 15:59:00)          | Active breakout window      |
+------------------+-------------------------------+-----------------------------+
| FORCE_EXIT       | [15:59:00, 16:00:00]          | Hard EOD liquidation zone   |
+------------------+-------------------------------+-----------------------------+

Bars outside ``[09:30:00, 16:00:00]`` are pre-market or post-market and are
excluded from all downstream calculations.

DST Safety
----------
``pandas`` uses ``pytz`` / ``dateutil`` under the hood and handles the
US DST transitions (second Sunday in March, first Sunday in November)
transparently when the canonical ``"America/New_York"`` IANA key is used.
Every function in this module explicitly works with timezone-aware
``pd.Timestamp`` objects so there is never an ambiguous ``fold`` situation.
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Tuple
import re

import pandas as pd

# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------

EASTERN_TZ: str = "America/New_York"
"""Canonical IANA timezone key used throughout the ORB pipeline."""


# ---------------------------------------------------------------------------
# Session phase enum
# ---------------------------------------------------------------------------


class SessionPhase(str, Enum):
    """Discrete intraday phase label for each RTH 1-minute bar.

    Values are plain strings so they can be stored directly in DataFrame
    columns without an extra conversion step.
    """

    OPENING_RANGE = "OPENING_RANGE"
    """09:30:00 – 09:44:59 ET: opening range construction window."""

    TRADING = "TRADING"
    """09:45:00 – 15:58:59 ET: active breakout trading window."""

    FORCE_EXIT = "FORCE_EXIT"
    """15:59:00 – 16:00:00 ET: mandatory end-of-day liquidation window."""

    PRE_MARKET = "PRE_MARKET"
    """Before 09:30:00 ET: excluded from all ORB calculations."""

    POST_MARKET = "POST_MARKET"
    """After 16:00:00 ET: excluded from all ORB calculations."""


# ---------------------------------------------------------------------------
# Core timezone helpers
# ---------------------------------------------------------------------------


def ensure_eastern(ts: pd.Timestamp) -> pd.Timestamp:
    """Convert or localise a single ``pd.Timestamp`` to ``America/New_York``.

    Handles three input variants:

    * **Naive** — treated as UTC, then converted to Eastern.
    * **UTC-aware** — converted to Eastern.
    * **Eastern-aware** — returned unchanged.

    Args:
        ts: Input timestamp.

    Returns:
        Timezone-aware timestamp in ``America/New_York``.

    Raises:
        TypeError: If *ts* is not a ``pd.Timestamp``.

    Example::

        ts = pd.Timestamp("2024-01-02 14:30:00", tz="UTC")
        et = ensure_eastern(ts)
        # 2024-01-02 09:30:00-05:00
    """
    if not isinstance(ts, pd.Timestamp):
        raise TypeError(
            f"ensure_eastern expects a pd.Timestamp, got {type(ts).__name__}."
        )

    if ts.tzinfo is None:
        # Treat naive as UTC (Alpaca returns UTC by default)
        ts = ts.tz_localize("UTC")

    if str(ts.tzinfo) != EASTERN_TZ and ts.tzname() not in ("EST", "EDT"):
        ts = ts.tz_convert(EASTERN_TZ)

    return ts


def to_eastern(
    df: pd.DataFrame,
    time_col: str = "timestamp",
) -> pd.DataFrame:
    """Ensure a DataFrame's timestamp column is ``America/New_York``-aware.

    Operates on a copy; the original DataFrame is never mutated.

    Handles three input variants per-element:

    * **Naive series** — localised to UTC, then converted to Eastern.
    * **UTC-aware series** — converted to Eastern.
    * **Already-Eastern series** — left unchanged.

    Args:
        df: Input DataFrame containing a timestamp column.
        time_col: Name of the timestamp column.  Defaults to ``"timestamp"``.

    Returns:
        A copy of *df* with the timestamp column replaced by timezone-aware
        ``America/New_York`` timestamps.

    Raises:
        KeyError: If *time_col* is not present in *df*.
        TypeError: If the column cannot be coerced to ``pd.DatetimeTZDtype``.

    Example::

        df = to_eastern(raw_df)
        assert str(df["timestamp"].dt.tz) == "America/New_York"
    """
    if time_col not in df.columns:
        raise KeyError(
            f"Column '{time_col}' not found in DataFrame. "
            f"Available columns: {list(df.columns)}"
        )

    out = df.copy()
    series: pd.Series = pd.to_datetime(out[time_col], utc=True)
    out[time_col] = series.dt.tz_convert(EASTERN_TZ)
    return out


# ---------------------------------------------------------------------------
# RTH filtering
# ---------------------------------------------------------------------------


def filter_rth(
    df: pd.DataFrame,
    time_col: str = "timestamp",
) -> pd.DataFrame:
    """Filter a bar DataFrame to Regular Trading Hours (09:30–16:00 ET).

    Bars timestamped before 09:30:00 ET or after 16:00:00 ET are dropped.
    The 16:00:00 bar itself is retained so that the force-exit logic has a
    closing price available when needed.

    Internally calls :func:`to_eastern` first, so the input may carry any
    timezone (or no timezone).

    Args:
        df: OHLCV bar DataFrame.  Must contain *time_col*.
        time_col: Name of the timestamp column.  Defaults to ``"timestamp"``.

    Returns:
        Filtered copy of *df* restricted to RTH bars, with timestamps
        normalised to ``America/New_York``.  Index is reset.

    Raises:
        KeyError: If *time_col* is not present in *df*.
    """
    df_et = to_eastern(df, time_col=time_col)

    ts = df_et[time_col]
    time_of_day = ts.dt.time

    market_open = datetime.time(9, 30, 0)
    market_close = datetime.time(16, 0, 0)

    mask = (time_of_day >= market_open) & (time_of_day <= market_close)
    return df_et.loc[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Opening range bar classification
# ---------------------------------------------------------------------------


def is_opening_range_bar(
    timestamp: pd.Timestamp,
    or_minutes: int = 15,
) -> bool:
    """Return ``True`` iff *timestamp* falls inside the opening range window.

    The opening range window is defined as::

        [09:30:00, 09:30:00 + or_minutes)   (half-open interval)

    For the default 15-minute configuration this means bars timestamped
    09:30:00 through 09:44:59 are in-range; 09:45:00 is **not** in-range.

    Args:
        timestamp: A timezone-aware ``pd.Timestamp``.  If not Eastern-aware,
            it is converted before comparison.
        or_minutes: Length of the opening range in minutes.  Defaults to 15.

    Returns:
        ``True`` if the bar belongs to the opening range; ``False`` otherwise.

    Raises:
        ValueError: If *or_minutes* is not a positive integer.

    Example::

        ts_in  = pd.Timestamp("2024-01-02 09:44:00", tz="America/New_York")
        ts_out = pd.Timestamp("2024-01-02 09:45:00", tz="America/New_York")
        assert is_opening_range_bar(ts_in)   is True
        assert is_opening_range_bar(ts_out)  is False
    """
    if or_minutes <= 0:
        raise ValueError(f"or_minutes must be a positive integer; got {or_minutes}.")

    ts_et = ensure_eastern(timestamp)
    t = ts_et.time()

    or_start = datetime.time(9, 30, 0)
    or_end_h, or_end_m = divmod(9 * 60 + 30 + or_minutes, 60)
    or_end = datetime.time(or_end_h, or_end_m, 0)

    return or_start <= t < or_end


# ---------------------------------------------------------------------------
# Session phase classifier
# ---------------------------------------------------------------------------


def classify_session_phase(
    timestamp: pd.Timestamp,
    or_minutes: int = 15,
    force_exit_time: str = "15:59:00",
) -> SessionPhase:
    """Classify a single bar timestamp into its intraday session phase.

    Args:
        timestamp: Bar timestamp (any timezone; converted to Eastern internally).
        or_minutes: Opening range duration in minutes.  Defaults to 15.
        force_exit_time: HH:MM:SS string marking the force-exit window start.
            Defaults to ``"15:59:00"``.

    Returns:
        The :class:`SessionPhase` for this bar.

    Example::

        phase = classify_session_phase(
            pd.Timestamp("2024-01-02 09:35:00", tz="America/New_York")
        )
        assert phase == SessionPhase.OPENING_RANGE
    """
    ts_et = ensure_eastern(timestamp)
    t = ts_et.time()

    # Parse force_exit_time
    h, m, s = (int(x) for x in force_exit_time.split(":"))
    force_exit_dt = datetime.time(h, m, s)

    or_end_minutes = 9 * 60 + 30 + or_minutes
    or_end_h, or_end_m = divmod(or_end_minutes, 60)
    or_end_dt = datetime.time(or_end_h, or_end_m, 0)

    market_open_dt = datetime.time(9, 30, 0)
    market_close_dt = datetime.time(16, 0, 0)

    if t < market_open_dt:
        return SessionPhase.PRE_MARKET
    if t > market_close_dt:
        return SessionPhase.POST_MARKET
    if t < or_end_dt:
        return SessionPhase.OPENING_RANGE
    if t < force_exit_dt:
        return SessionPhase.TRADING
    return SessionPhase.FORCE_EXIT


def add_session_phase_column(
    df: pd.DataFrame,
    time_col: str = "timestamp",
    or_minutes: int = 15,
    force_exit_time: str = "15:59:00",
) -> pd.DataFrame:
    """Add a ``session_phase`` string column to a bar DataFrame.

    Internally calls :func:`to_eastern` so the input timezone is handled
    transparently.

    Args:
        df: OHLCV bar DataFrame containing *time_col*.
        time_col: Name of the timestamp column.  Defaults to ``"timestamp"``.
        or_minutes: Opening range duration in minutes.  Defaults to 15.
        force_exit_time: HH:MM:SS string for force-exit window start.

    Returns:
        Copy of *df* with an added ``"session_phase"`` string column.
    """
    df_et = to_eastern(df, time_col=time_col)

    df_et["session_phase"] = df_et[time_col].apply(
        lambda ts: classify_session_phase(
            ts, or_minutes=or_minutes, force_exit_time=force_exit_time
        ).value
    )
    return df_et


# ---------------------------------------------------------------------------
# Session boundary builder
# ---------------------------------------------------------------------------


def get_session_timestamps(
    session_date: datetime.date,
    or_minutes: int = 15,
    force_exit_time: str = "15:59:00",
    tz: str = EASTERN_TZ,
) -> Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """Return the four canonical boundary timestamps for a single trading day.

    All returned timestamps are fully timezone-aware in *tz*.

    Args:
        session_date: The trading date.
        or_minutes: Opening range duration in minutes.  Defaults to 15.
        force_exit_time: HH:MM:SS string for the force-exit time.
            Defaults to ``"15:59:00"``.
        tz: IANA timezone key.  Must be ``"America/New_York"`` for ORB use.

    Returns:
        A 4-tuple ``(market_open, or_end, force_exit, market_close)`` where:

        * ``market_open``  — ``09:30:00`` on *session_date*
        * ``or_end``       — ``09:30:00 + or_minutes``; the first bar **excluded**
          from the opening range (i.e. the trading window starts here)
        * ``force_exit``   — timestamp at which all open positions must be closed
        * ``market_close`` — ``16:00:00`` on *session_date*

    Raises:
        ValueError: If *force_exit_time* is not in ``HH:MM:SS`` format or if
            *or_minutes* is not positive.

    Example::

        d = datetime.date(2024, 1, 2)
        open_, or_end, fe, close = get_session_timestamps(d)
        # open_  → 2024-01-02 09:30:00-05:00
        # or_end → 2024-01-02 09:45:00-05:00
        # fe     → 2024-01-02 15:59:00-05:00
        # close  → 2024-01-02 16:00:00-05:00
    """
    if or_minutes <= 0:
        raise ValueError(f"or_minutes must be a positive integer; got {or_minutes}.")

    parts = force_exit_time.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"force_exit_time must be in HH:MM:SS format; got '{force_exit_time}'."
        )
    fe_h, fe_m, fe_s = int(parts[0]), int(parts[1]), int(parts[2])

    date_str = session_date.strftime("%Y-%m-%d")

    market_open = pd.Timestamp(f"{date_str} 09:30:00", tz=tz)

    # or_end is the first bar excluded from the OR (trading window begins here)
    or_end = market_open + pd.Timedelta(minutes=or_minutes)

    force_exit = pd.Timestamp(f"{date_str} {fe_h:02d}:{fe_m:02d}:{fe_s:02d}", tz=tz)

    market_close = pd.Timestamp(f"{date_str} 16:00:00", tz=tz)

    return market_open, or_end, force_exit, market_close


# ---------------------------------------------------------------------------
# Session ID helper
# ---------------------------------------------------------------------------


def get_session_id(timestamp: pd.Timestamp) -> str:
    """Return the trading date string (``YYYY-MM-DD``) for a bar timestamp.

    The session ID is derived from the date in ``America/New_York`` time,
    so UTC midnight crossings never split a US intraday session.

    Args:
        timestamp: Bar timestamp (any timezone; converted internally).

    Returns:
        ISO date string, e.g. ``"2024-01-02"``.

    Example::

        # A bar at 21:00 UTC on Jan 2 is 16:00 ET on Jan 2 — same session.
        ts = pd.Timestamp("2024-01-02 21:00:00", tz="UTC")
        assert get_session_id(ts) == "2024-01-02"
    """
    return ensure_eastern(timestamp).date().isoformat()


def add_session_id_column(
    df: pd.DataFrame,
    time_col: str = "timestamp",
) -> pd.DataFrame:
    """Add a ``session_id`` column (``YYYY-MM-DD``) derived from Eastern date.

    Args:
        df: Bar DataFrame containing *time_col*.
        time_col: Name of the timestamp column.  Defaults to ``"timestamp"``.

    Returns:
        Copy of *df* with ``"session_id"`` column added.
    """
    df_et = to_eastern(df, time_col=time_col)
    df_et["session_id"] = df_et[time_col].dt.strftime("%Y-%m-%d")
    return df_et

def get_timeframe_minutes(timeframe: str) -> int:
    """Parse minutes from timeframe string (e.g. '1Min' -> 1, '5Min' -> 5, '1Hour' -> 60)."""
    tf = timeframe.strip().lower()
    if "min" in tf or "t" in tf:
        nums = re.findall(r"\d+", tf)
        return int(nums[0]) if nums else 1
    elif "hour" in tf or "h" in tf:
        nums = re.findall(r"\d+", tf)
        return int(nums[0]) * 60 if nums else 60
    return 1