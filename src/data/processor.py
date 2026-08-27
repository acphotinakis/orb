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
5. **Persist** — write to ``data/processed/{symbol}/{timeframe}_{date_slug}/sessions.parquet``
   with Zstandard (zstd) compression.

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
import numpy as np

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
_COMPRESSION: str     = "zstd"

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
    """Add intraday metadata columns to RTH bar data.

    Calculates:
    * ``session_id``: ``YYYY-MM-DD`` string derived from bar timestamp.
    * ``minute_of_day``: Integer minutes since 09:30:00 open.
    * ``is_opening_range``: ``True`` for bars in ``[09:30, 09:30 + or_minutes)``.
    * ``is_trading_window``: ``True`` for bars in ``[09:30 + or_minutes, force_exit_time)``.
    * ``is_force_exit``: ``True`` for bars at or after ``force_exit_time``.

    Args:
        df: RTH-filtered DataFrame with timestamps in ``America/New_York``.
        or_minutes: Opening range length in minutes.
        force_exit_time: Hard exit time string in ``HH:MM:SS`` format.

    Returns:
        DataFrame with new metadata columns added.
    """
    df = df.copy()

    # Session ID: calendar date in ET
    df["session_id"] = df["timestamp"].dt.strftime("%Y-%m-%d")

    # Minute of day relative to 09:30 ET
    open_minutes = _MARKET_OPEN_H * 60 + _MARKET_OPEN_M  # 570
    bar_minutes  = df["timestamp"].dt.hour * 60 + df["timestamp"].dt.minute
    df["minute_of_day"] = (bar_minutes - open_minutes).astype(np.int32)

    # Opening range flag: [09:30, 09:30 + or_minutes)
    df["is_opening_range"] = df["minute_of_day"] < or_minutes

    # Parse force exit time to minutes since midnight
    fe_parts = [int(p) for p in force_exit_time.split(":")]
    fe_minutes = fe_parts[0] * 60 + fe_parts[1]
    fe_mod = fe_minutes - open_minutes

    # Force exit flag: bars at or after force_exit_time
    df["is_force_exit"] = df["minute_of_day"] >= fe_mod

    # Trading window flag: after OR, before force exit
    df["is_trading_window"] = (
        (~df["is_opening_range"]) & (~df["is_force_exit"])
    )

    return df


def _prune_incomplete_sessions(
    df: pd.DataFrame,
    or_minutes: int,
) -> pd.DataFrame:
    """Remove sessions whose opening range contains fewer than *or_minutes* bars.

    Sessions with incomplete opening ranges (e.g. half days, late opens,
    or data gaps during 09:30–09:44) cannot produce valid OR breakout
    levels and must be excluded to maintain simulation integrity.

    Args:
        df: DataFrame with ``session_id`` and ``is_opening_range`` columns.
        or_minutes: Expected number of 1-minute bars in the opening range.

    Returns:
        Filtered DataFrame with incomplete sessions dropped.
    """
    or_counts = (
        df[df["is_opening_range"]]
        .groupby("session_id")
        .size()
    )
    complete_sessions = or_counts[or_counts >= or_minutes].index
    n_pruned = df["session_id"].nunique() - len(complete_sessions)
    if n_pruned > 0:
        logger.warning(
            "Pruned %d session(s) with incomplete opening ranges (< %d bars).",
            n_pruned, or_minutes,
        )
    return df[df["session_id"].isin(complete_sessions)].reset_index(drop=True)


def _enforce_output_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure all required output columns exist and are ordered canonically.

    Missing optional columns (``vwap``, ``trade_count``) are populated with
    appropriate null values.

    Args:
        df: DataFrame after session tagging and incomplete session pruning.

    Returns:
        DataFrame strictly adhering to :data:`_OUTPUT_COLS`.
    """
    df = df.copy()

    if "vwap" not in df.columns:
        df["vwap"] = np.nan

    if "trade_count" not in df.columns:
        df["trade_count"] = pd.Series(dtype="Int64")

    # Cast types explicitly
    df["open"]   = df["open"].astype(np.float64)
    df["high"]   = df["high"].astype(np.float64)
    df["low"]    = df["low"].astype(np.float64)
    df["close"]  = df["close"].astype(np.float64)
    df["volume"] = df["volume"].astype(np.float64)

    return df[_OUTPUT_COLS].copy()


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class DataProcessor:
    """Orchestrates the conversion of raw bar data into processed RTH sessions.

    Applies timezone normalisation, RTH filtering, session tagging, and
    incomplete session pruning in a single reproducible pipeline.  Output is
    persisted to Parquet format by default, providing the authoritative
    dataset used by the strategy, backtest, and visualization layers.

    Args:
        config: Loaded :class:`~src.common.config.AppConfig` instance.
        output_dir: Directory for processed session Parquet files. When
            provided (from PathManager), this overrides default path logic.
    """

    def __init__(
        self,
        config: AppConfig,
        output_dir: Optional[Path] = None,
    ) -> None:
        """Initialise the DataProcessor.

        Args:
            config: Loaded AppConfig instance.
            output_dir: Directory for processed session Parquet files. When
                provided (from PathManager), this overrides default path logic.
        """
        self._config = config
        # Accept injected path from PathManager; fall back to a sensible default
        self._processed_dir: Path = (
            output_dir
            if output_dir is not None
            else Path("data") / "processed" / config.data.symbol
        )
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
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Process validated raw bars into the canonical processed session dataset.

        Steps:

        1. Check cache hit on processed_file if force_refresh=False.
        2. Validate required input columns.
        3. Normalise timestamps → ``America/New_York``.
        4. Filter to RTH (09:30–16:00 ET).
        5. Add session metadata columns.
        6. Prune incomplete sessions (OR bar count < ``or_minutes``).
        7. Deduplicate on (session_id, timestamp).
        8. Enforce output column schema and ordering.
        9. Optionally persist to Parquet with zstd compression.

        Args:
            raw_df: Validated raw bar DataFrame.  Must contain
                ``["timestamp", "open", "high", "low", "close", "volume"]``.
            save_to_disk: When ``True`` (default), write the processed
                DataFrame to ``sessions.parquet``.
            output_path: Override for the Parquet output path.  If ``None``,
                defaults to ``{processed_dir}/sessions.parquet``.
            force_refresh: When ``True``, ignore existing cached processed file.

        Returns:
            Fully processed DataFrame with the canonical output schema.

        Raises:
            ValueError: If required input columns are missing.
        """
        dest = output_path if output_path is not None else (
            self._processed_dir / _OUTPUT_FILENAME
        )

        # Early return if processed file already exists on disk
        if not force_refresh and dest.exists():
            try:
                cached_df = self.load_processed(dest)
                if not cached_df.empty:
                    logger.info(
                        "Processed session cache hit — loaded %d bars across %d session(s) from %s",
                        len(cached_df), cached_df["session_id"].nunique(), dest,
                    )
                    return cached_df
            except Exception as exc:
                logger.warning(
                    "Failed to read existing processed file %s (%s); re-processing.", dest, exc
                )

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

        # 6. Deduplicate on (session_id, timestamp)
        df_dedup = df_complete.drop_duplicates(subset=["session_id", "timestamp"]).reset_index(drop=True)

        n_sessions = df_dedup["session_id"].nunique()
        logger.info(
            "Processed %d complete trading session(s) | %d bars total.",
            n_sessions, len(df_dedup),
        )

        # 7. Enforce output schema
        df_out = _enforce_output_schema(df_dedup)

        # 8. Persist
        if save_to_disk:
            self._save_parquet(df_out, dest)

        return df_out

    # ------------------------------------------------------------------
    # Persistence helper
    # ------------------------------------------------------------------

    def _save_parquet(self, df: pd.DataFrame, path: Path) -> None:
        """Persist the processed DataFrame to a zstd-compressed Parquet file.

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
            "Saved %d processed bars → %s (%s, %s engine).",
            len(df), path, _COMPRESSION, _PARQUET_ENGINE,
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
            ValueError: If required output columns are missing.
        """
        src = path if path is not None else (self._processed_dir / _OUTPUT_FILENAME)
        if not src.exists():
            raise FileNotFoundError(
                f"Processed dataset not found at '{src}'. "
                "Run DataProcessor.process() first."
            )
        df = pd.read_parquet(src, engine=_PARQUET_ENGINE)
        missing = [c for c in _OUTPUT_COLS if c not in df.columns]
        if missing:
            raise ValueError(
                f"Processed file '{src}' is missing expected columns: {missing}."
            )
        logger.info(
            "Loaded %d processed bars from %s (%d session(s)).",
            len(df), src.name, df["session_id"].nunique() if "session_id" in df.columns else 0,
        )
        return df
