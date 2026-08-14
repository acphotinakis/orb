"""
src.data.processor
==================
RTH session processor and processed dataset builder for the SPY Opening Range
Breakout (ORB) quantitative trading system.

This module receives validated raw bars from :mod:`src.data.validator` and
produces the canonical processed dataset used by all downstream strategy,
backtest, and visualization modules.

Processing Pipeline
-------------------
1. **Timezone normalisation** — convert all timestamps to ``America/New_York``
   via :func:`~src.common.time_utils.to_eastern`.
2. **RTH filter** — discard bars outside ``[09:30:00, 16:00:00]`` ET using
   :func:`~src.common.time_utils.filter_rth`.
3. **Session metadata** — add ``session_id``, ``minute_of_day``,
   ``is_opening_range``, ``is_trading_window``, ``is_force_exit``.
4. **Incomplete session pruning** — drop sessions where the opening range
   (09:30–09:44) contains fewer than the configured ``opening_range_minutes``
   bars (default 15).
5. **Persist** — write to ``data/processed/SPY/sessions.parquet`` with snappy
   compression (configurable via ``output_path``).

Output Schema
-------------
+------------------+-----------+----------------------------------------------+
| Column           | dtype     | Description                                  |
+==================+===========+==============================================+
| session_id       | str       | Trading date ``YYYY-MM-DD`` (ET)             |
+------------------+-----------+----------------------------------------------+
| timestamp        | datetime  | Bar timestamp, ``America/New_York``-aware    |
+------------------+-----------+----------------------------------------------+
| open             | float64   | Opening price                                |
+------------------+-----------+----------------------------------------------+
| high             | float64   | Intraday high                                |
+------------------+-----------+----------------------------------------------+
| low              | float64   | Intraday low                                 |
+------------------+-----------+----------------------------------------------+
| close            | float64   | Closing price                                |
+------------------+-----------+----------------------------------------------+
| volume           | float64   | Bar volume                                   |
+------------------+-----------+----------------------------------------------+
| vwap             | float64   | VWAP (if available, else NaN)                |
+------------------+-----------+----------------------------------------------+
| trade_count      | Int64     | Trade count (if available, else NaN)         |
+------------------+-----------+----------------------------------------------+
| minute_of_day    | int32     | Minutes since 09:30 open (0 = 09:30 bar)     |
+------------------+-----------+----------------------------------------------+
| is_opening_range | bool      | True iff bar ∈ [09:30, 09:30+OR_minutes)     |
+------------------+-----------+----------------------------------------------+
| is_trading_window| bool      | True iff bar ∈ [09:45, 15:59)               |
+------------------+-----------+----------------------------------------------+
| is_force_exit    | bool      | True iff bar ∈ [15:59, 16:00]               |
+------------------+-----------+----------------------------------------------+

Usage
-----
    from src.common.config import load_config
    from src.data.processor import DataProcessor

    cfg = load_config()
    processor = DataProcessor(config=cfg)
    processed_df = processor.process(raw_df, save_to_disk=True)
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from src.common.config import AppConfig
from src.common.logger import get_logger
from src.common.time_utils import (
    EASTERN_TZ,
    filter_rth,
    to_eastern,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OUTPUT_FILENAME: str = "sessions.parquet"
_PARQUET_ENGINE: str  = "pyarrow"
_COMPRESSION: str     = "snappy"

_MARKET_OPEN_H: int  = 9
_MARKET_OPEN_M: int  = 30

# Required columns coming in from the validated raw DataFrame
_REQUIRED_INPUT_COLS: list[str] = ["timestamp", "open", "high", "low", "close", "volume"]

# Output column order
_OUTPUT_COLS: list[str] = [
    "session_id", "timestamp",
    "open", "high", "low", "close", "volume",
    "vwap", "trade_count",
    "minute_of_day", "is_opening_range", "is_trading_window", "is_force_exit",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_session_metadata(
    df: pd.DataFrame,
    or_minutes: int,
    force_exit_time: str,
) -> pd.DataFrame:
    """Add all session-level metadata columns to an ET-aware bar DataFrame.

    Assumes ``df["timestamp"]`` is already ``America/New_York``-aware.

    Columns added:
    * ``session_id``         — ``YYYY-MM-DD`` string of Eastern date
    * ``minute_of_day``      — integer minutes since 09:30 open (0-indexed)
    * ``is_opening_range``   — True iff bar ∈ [09:30, 09:30+or_minutes)
    * ``is_trading_window``  — True iff bar ∈ [09:30+or_minutes, force_exit_time)
    * ``is_force_exit``      — True iff bar ∈ [force_exit_time, 16:00]

    Args:
        df: RTH-filtered bar DataFrame with ET-aware timestamps.
        or_minutes: Opening range duration (minutes from 09:30).
        force_exit_time: Force-exit boundary as ``HH:MM:SS`` string.

    Returns:
        Copy of *df* with metadata columns appended.
    """
    df = df.copy()
    ts = df["timestamp"]

    # session_id — Eastern calendar date (immune to UTC midnight splits)
    df["session_id"] = ts.dt.strftime("%Y-%m-%d")

    # minute_of_day — 0-indexed minutes from 09:30
    minutes_since_midnight = ts.dt.hour * 60 + ts.dt.minute
    market_open_minutes = _MARKET_OPEN_H * 60 + _MARKET_OPEN_M
    df["minute_of_day"] = (minutes_since_midnight - market_open_minutes).astype("int32")

    # Time-of-day for boundary comparisons
    time_of_day = ts.dt.time

    # Opening range boundary: [09:30, 09:30+or_minutes)
    or_end_total_m = _MARKET_OPEN_H * 60 + _MARKET_OPEN_M + or_minutes
    or_end_h, or_end_m = divmod(or_end_total_m, 60)
    or_start   = datetime.time(_MARKET_OPEN_H, _MARKET_OPEN_M, 0)
    or_end     = datetime.time(or_end_h, or_end_m, 0)

    # Force-exit boundary
    fe_parts = force_exit_time.split(":")
    fe_time  = datetime.time(int(fe_parts[0]), int(fe_parts[1]), int(fe_parts[2]))

    # Market close
    market_close = datetime.time(16, 0, 0)

    df["is_opening_range"] = (time_of_day >= or_start) & (time_of_day < or_end)
    df["is_trading_window"] = (time_of_day >= or_end) & (time_of_day < fe_time)
    df["is_force_exit"]    = (time_of_day >= fe_time) & (time_of_day <= market_close)

    return df


def _prune_incomplete_sessions(
    df: pd.DataFrame,
    or_minutes: int,
) -> pd.DataFrame:
    """Remove sessions whose opening range contains fewer than *or_minutes* bars.

    A session is considered incomplete if the number of bars marked
    ``is_opening_range=True`` is less than *or_minutes*.  This filters out
    trading days where data was truncated before the OR window closed.

    Args:
        df: Bar DataFrame with ``session_id`` and ``is_opening_range`` columns.
        or_minutes: Required minimum number of opening-range bars.

    Returns:
        Filtered DataFrame with incomplete sessions dropped.
    """
    or_counts = df.groupby("session_id")["is_opening_range"].sum()
    complete_sessions = or_counts[or_counts >= or_minutes].index
    n_pruned = df["session_id"].nunique() - len(complete_sessions)

    if n_pruned > 0:
        logger.warning(
            "Pruned %d incomplete session(s) (OR bar count < %d).",
            n_pruned, or_minutes,
        )

    return df[df["session_id"].isin(complete_sessions)].reset_index(drop=True)


def _enforce_output_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Reorder / add missing optional columns to match the canonical output schema.

    Args:
        df: Processed DataFrame.

    Returns:
        DataFrame with columns in :data:`_OUTPUT_COLS` order.  Optional
        columns (``vwap``, ``trade_count``) are added with ``pd.NA`` if absent.
    """
    for col in _OUTPUT_COLS:
        if col not in df.columns:
            df[col] = pd.NA

    return df[_OUTPUT_COLS]


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class DataProcessor:
    """RTH session processor and processed dataset builder.

    Transforms a validated raw bar DataFrame into the canonical processed
    dataset used by the strategy, backtest, and visualization layers.

    Args:
        config: Loaded :class:`~src.common.config.AppConfig` instance.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._processed_dir = Path(config.data.processed_dir)
        self._or_minutes = config.strategy.opening_range_minutes
        self._force_exit_time = config.strategy.force_exit_time

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def process(
        self,
        raw_df: pd.DataFrame,
        save_to_disk: bool = True,
        output_path: Optional[Path] = None,
    ) -> pd.DataFrame:
        """Process validated raw bars into the canonical processed session dataset.

        Steps:

        1. Validate required input columns.
        2. Normalise timestamps → ``America/New_York``.
        3. Filter to RTH (09:30–16:00 ET).
        4. Add session metadata columns.
        5. Prune incomplete sessions (OR bar count < ``or_minutes``).
        6. Enforce output column schema and ordering.
        7. Optionally persist to Parquet.

        Args:
            raw_df: Validated raw bar DataFrame.  Must contain
                ``["timestamp", "open", "high", "low", "close", "volume"]``.
            save_to_disk: When ``True`` (default), write the processed
                DataFrame to ``data/processed/SPY/sessions.parquet``.
            output_path: Override for the Parquet output path.  If ``None``,
                defaults to ``{processed_dir}/sessions.parquet``.

        Returns:
            Fully processed DataFrame with the canonical output schema.

        Raises:
            ValueError: If required input columns are missing.
        """
        logger.info(
            "DataProcessor: processing %d raw bars | or_minutes=%d | force_exit=%s",
            len(raw_df), self._or_minutes, self._force_exit_time,
        )

        # 1. Validate input columns
        missing = [c for c in _REQUIRED_INPUT_COLS if c not in raw_df.columns]
        if missing:
            raise ValueError(
                f"DataProcessor.process: required columns missing: {missing}."
            )

        # 2. Normalise timestamps to Eastern
        df_et = to_eastern(raw_df, time_col="timestamp")
        logger.debug("Timestamps normalised to %s.", EASTERN_TZ)

        # 3. Filter to RTH
        df_rth = filter_rth(df_et, time_col="timestamp")
        n_pre_filter = len(raw_df)
        n_post_filter = len(df_rth)
        logger.info(
            "RTH filter: %d → %d bars (%d pre-/post-market dropped).",
            n_pre_filter, n_post_filter, n_pre_filter - n_post_filter,
        )

        if df_rth.empty:
            logger.warning("No RTH bars remain after filtering.")
            return pd.DataFrame(columns=_OUTPUT_COLS)

        # 4. Add session metadata
        df_meta = _add_session_metadata(
            df_rth,
            or_minutes=self._or_minutes,
            force_exit_time=self._force_exit_time,
        )

        # 5. Prune incomplete sessions
        df_complete = _prune_incomplete_sessions(df_meta, or_minutes=self._or_minutes)

        n_sessions = df_complete["session_id"].nunique()
        logger.info(
            "Processed %d complete trading session(s) | %d bars total.",
            n_sessions, len(df_complete),
        )

        # 6. Enforce output schema
        df_out = _enforce_output_schema(df_complete)

        # 7. Persist
        if save_to_disk:
            dest = output_path if output_path is not None else (
                self._processed_dir / _OUTPUT_FILENAME
            )
            self._save_parquet(df_out, dest)

        return df_out

    # ------------------------------------------------------------------
    # Persistence helper
    # ------------------------------------------------------------------

    def _save_parquet(self, df: pd.DataFrame, path: Path) -> None:
        """Persist the processed DataFrame to a snappy-compressed Parquet file.

        Args:
            df: Processed DataFrame.
            path: Destination path.  Parent directories are created if absent.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(
            path,
            engine=_PARQUET_ENGINE,
            compression=_COMPRESSION,
            index=False,
        )
        logger.info(
            "Saved %d processed bars → %s (snappy, %s engine).",
            len(df), path, _PARQUET_ENGINE,
        )

    # ------------------------------------------------------------------
    # Convenience: load processed dataset from disk
    # ------------------------------------------------------------------

    def load_processed(self, path: Optional[Path] = None) -> pd.DataFrame:
        """Load the processed sessions Parquet file from disk.

        Args:
            path: Override path.  Defaults to ``{processed_dir}/sessions.parquet``.

        Returns:
            Processed DataFrame.

        Raises:
            FileNotFoundError: If the Parquet file does not exist.
        """
        src = path if path is not None else (self._processed_dir / _OUTPUT_FILENAME)
        if not src.exists():
            raise FileNotFoundError(
                f"Processed dataset not found at '{src}'. "
                "Run DataProcessor.process() first."
            )
        df = pd.read_parquet(src, engine=_PARQUET_ENGINE)
        logger.info("Loaded %d processed bars from %s.", len(df), src)
        return df
