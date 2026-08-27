"""
src.data.validator
==================
Rigorous OHLCV bar integrity validation and anomaly cleaning for the SPY
Opening Range Breakout (ORB) quantitative trading system.

This module operates on raw, unfiltered bar DataFrames (pre-RTH-filter) and
produces clean, chronologically sorted bars together with a structured audit
report.  It is the gating layer between raw Alpaca data and all downstream
strategy and backtest modules.

Cleaning Pipeline (in order)
-----------------------------
1. **Schema check** — assert required columns are present.
2. **Sort** — sort by timestamp ascending (handles out-of-order delivery).
3. **Duplicate removal** — keep the bar with the highest volume per timestamp.
4. **Infinite / NaN purge** — drop rows with ∞ or NaN in any OHLCV column.
5. **OHLC geometric validation** — drop rows violating:
   * ``high >= max(open, close, low)``
   * ``low  <= min(open, close, high)``
   * ``open, high, low, close > 0``
   * ``volume >= 0``
6. **Gap audit** — during RTH hours, flag sessions with missing bars > 5 min.
7. **Strict mode** — raise ``DataValidationError`` if > 1% of input bars were
   removed by OHLC checks.

Usage
-----
    from src.data.validator import validate_and_clean_bars, ValidationReport

    clean_df, report = validate_and_clean_bars(raw_df)
    if not report.is_valid:
        print("Validation failed:", report.flagged_sessions)
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
import pandas as pd
import re

from src.common.exceptions import DataValidationError
from src.common.logger import get_logger
from src.common.time_utils import EASTERN_TZ, filter_rth

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUIRED_COLS: list[str] = ["timestamp", "open", "high", "low", "close", "volume"]
_OHLC_ERROR_THRESHOLD: float = 0.01   # 1 % of input bars
_MARKET_OPEN  = datetime.time(9, 30, 0)
_MARKET_CLOSE = datetime.time(16, 0, 0)


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------

@dataclass
class ValidationReport:
    """Structured audit record produced by :func:`validate_and_clean_bars`.

    Attributes:
        total_bars_input: Number of bars in the original (uncleaned) DataFrame.
        total_bars_output: Number of bars in the cleaned output DataFrame.
        duplicates_removed: Bars eliminated due to duplicate timestamps.
        invalid_ohlc_removed: Bars eliminated due to NaN/Inf/negative/inverted
            OHLC values or non-positive prices.
        missing_timestamps_count: Total number of expected 1-minute slots
            within RTH that were absent from the data.
        flagged_sessions: List of session date strings (``YYYY-MM-DD``) whose
            RTH bars contained a gap exceeding :data:`_MAJOR_GAP_MINUTES`
            consecutive missing minutes.
        is_valid: ``True`` if the cleaned DataFrame passed all quality gates;
            ``False`` if any session was flagged or OHLC errors exceeded the
            1 % threshold (when ``strict=True``).
    """

    total_bars_input: int
    total_bars_output: int
    duplicates_removed: int
    invalid_ohlc_removed: int
    missing_timestamps_count: int
    flagged_sessions: List[str] = field(default_factory=list)
    is_valid: bool = True

    def summary(self) -> str:
        """Return a single-line human-readable summary of the report.

        Returns:
            Summary string suitable for logging.
        """
        return (
            f"ValidationReport("
            f"in={self.total_bars_input}, "
            f"out={self.total_bars_output}, "
            f"dups_removed={self.duplicates_removed}, "
            f"ohlc_removed={self.invalid_ohlc_removed}, "
            f"missing={self.missing_timestamps_count}, "
            f"flagged_sessions={len(self.flagged_sessions)}, "
            f"is_valid={self.is_valid})"
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
def _timeframe_to_pandas_freq(timeframe: str) -> str:                                                                                                                                                                
    """Normalize Alpaca timeframe string (e.g. 15Min, 1Hour, 1Day) to Pandas offset string."""                                                                                                                       
    tf = timeframe.strip()                                                                                                                                                                                           
    if tf.lower().endswith("min") or tf.lower().endswith("t"):                                                                                                                                                       
        num = re.findall(r"\d+", tf)                                                                                                                                                                                 
        n = num[0] if num else "1"                                                                                                                                                                                   
        return f"{n}min"                                                                                                                                                                                             
    elif tf.lower().endswith("hour") or tf.lower().endswith("h"):                                                                                                                                                    
        num = re.findall(r"\d+", tf)                                                                                                                                                                                 
        n = num[0] if num else "1"                                                                                                                                                                                   
        return f"{n}h"                                                                                                                                                                                               
    elif tf.lower().endswith("day") or tf.lower().endswith("d"):                                                                                                                                                     
        return "1D"                                                                                                                                                  
    return "1min"

def _check_required_columns(df: pd.DataFrame) -> None:
    """Assert all required OHLCV columns are present.

    Args:
        df: Input DataFrame.

    Raises:
        DataValidationError: If any required column is absent.
    """
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise DataValidationError(
            f"Required columns missing from bar DataFrame: {missing}. "
            f"Available: {list(df.columns)}"
        )


def _sort_chronologically(df: pd.DataFrame) -> pd.DataFrame:
    """Sort the DataFrame by ``timestamp`` ascending.

    Args:
        df: Input bar DataFrame (must contain ``"timestamp"`` column).

    Returns:
        Sorted copy, index reset.
    """
    return df.sort_values("timestamp").reset_index(drop=True)


def _remove_duplicates(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Remove duplicate timestamps, keeping the bar with the highest volume.

    When two bars share the same timestamp, the one with the greater volume
    is retained (heuristic: higher volume = more representative bar).
    If volumes are equal, the last occurrence is kept.

    Args:
        df: Sorted bar DataFrame.

    Returns:
        ``(deduped_df, n_removed)`` where ``n_removed`` is the count of rows
        eliminated.
    """
    n_before = len(df)
    # Sort by timestamp then volume desc so that highest-volume bar comes first
    # within each duplicate group, then keep the first (=highest volume).
    df_sorted = df.sort_values(
        ["timestamp", "volume"], ascending=[True, False]
    )
    df_deduped = df_sorted.drop_duplicates(subset=["timestamp"], keep="first")
    # Restore chronological order
    df_deduped = df_deduped.sort_values("timestamp").reset_index(drop=True)
    n_removed = n_before - len(df_deduped)
    return df_deduped, n_removed


def _remove_nan_inf(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Drop rows containing NaN or ±Inf in any OHLCV column.

    Args:
        df: Input bar DataFrame.

    Returns:
        ``(clean_df, n_removed)``.
    """
    n_before = len(df)
    ohlcv = ["open", "high", "low", "close", "volume"]
    numeric_cols = [c for c in ohlcv if c in df.columns]

    # Replace ±Inf with NaN then drop any row that has NaN in numeric columns
    df_clean = df.copy()
    df_clean[numeric_cols] = df_clean[numeric_cols].replace(
        [np.inf, -np.inf], np.nan
    )
    df_clean = df_clean.dropna(subset=numeric_cols).reset_index(drop=True)
    return df_clean, n_before - len(df_clean)


def _validate_ohlc_geometry(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Remove bars that violate OHLC geometric consistency constraints.

    Constraints applied (vectorised):
    * ``high >= max(open, close, low)``
    * ``low  <= min(open, close, high)``
    * ``open > 0, high > 0, low > 0, close > 0``
    * ``volume >= 0``

    Args:
        df: Bar DataFrame (NaN/Inf already removed).

    Returns:
        ``(valid_df, n_removed)``.
    """
    n_before = len(df)

    positive_prices = (
        (df["open"]  > 0) &
        (df["high"]  > 0) &
        (df["low"]   > 0) &
        (df["close"] > 0)
    )
    non_negative_volume = df["volume"] >= 0
    high_is_max = df["high"] >= df[["open", "close", "low"]].max(axis=1)
    low_is_min  = df["low"]  <= df[["open", "close", "high"]].min(axis=1)

    valid_mask = positive_prices & non_negative_volume & high_is_max & low_is_min
    n_invalid = (~valid_mask).sum()

    if n_invalid > 0:
        logger.warning(
            "Dropping %d bar(s) with OHLC geometric violations.", n_invalid
        )

    return df.loc[valid_mask].reset_index(drop=True), int(n_invalid)


def _audit_rth_gaps(
    df: pd.DataFrame,                                                                                                                                                                                                
    timeframe: str,
) -> Tuple[int, List[str]]:                                                                                                                                                                                          
    """Audit RTH bars for missing bar slots and flag sessions with major gaps."""                                                                                                                                    
    if df.empty:                                                                                                                                                                                                     
        return 0, []                                                                                                                                                                                                 
                                                                                                                                                                                                                        
    freq = _timeframe_to_pandas_freq(timeframe)                                                                                                                                                                      
    # Convert timestamps to Eastern for session-level analysis                                                                                                                                                       
    ts_et = df["timestamp"].dt.tz_convert(EASTERN_TZ)                                                                                                                                                                
    session_dates = ts_et.dt.date.unique()                                                                                                                                                                           
                                                                                                                                                                                                                        
    total_missing = 0                                                                                                                                                                                                
    flagged: list[str] = []                                                                                                                                                                                          
                                                                                                                                                                                                                        
    for date in sorted(session_dates):                                                                                                                                                                               
        market_open_et  = pd.Timestamp(date, tz=EASTERN_TZ).replace(hour=9,  minute=30)                                                                                                                              
        market_close_et = pd.Timestamp(date, tz=EASTERN_TZ).replace(hour=16, minute=0)                                                                                                                               
                                                                                                                                                                                                                        
        # Actual timestamps cleanly preserved in Eastern TZ                                                                                                                                                          
        session_mask = ts_et.dt.date == date                                                                                                                                                                         
        actual = pd.DatetimeIndex(ts_et[session_mask].dt.floor(freq))                                                                                                                                                
                                                                                                                                                                                                                        
        if len(actual) == 0:                                                                                                                                                                                         
            continue                                                                                                                                                                                                 
                                                                                                                                                                                                                        
        # Window expected range between first and last actual bar of session                                                                                                                                         
        windowed_expected = pd.date_range(                                                                                                                                                                           
            start=max(market_open_et, actual.min()),                                                                                                                                                                 
            end=min(market_close_et, actual.max()),                                                                                                                                                                  
            freq=freq,                                                                                                                                                                                               
        )                                                                                                                                                                                                            
                                                                                                                                                                                                                        
        missing_slots = windowed_expected.difference(actual)                                                                                                                                                         
        n_missing = len(missing_slots)                                                                                                                                                                               
        total_missing += n_missing                                                                                                                                                                                   
                                                                                                                                                                                                                        
        # Flag if more than 3 consecutive intervals or > 10% of session bars are missing                                                                                                                             
        if n_missing > 3:                                                                                                                                                                                            
            date_str = date.strftime("%Y-%m-%d")                                                                                                                                                                     
            flagged.append(date_str)                                                                                                                                                                                 
            logger.warning("Session %s: gap detected — %d missing bar(s).", date_str, n_missing)                                                                                                                     
                                                                                                                                                                                                                        
    return total_missing, flagged                                                                                                                                                                                    


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_and_clean_bars(
    df: pd.DataFrame,
    timeframe: str,
    strict: bool = False,
) -> Tuple[pd.DataFrame, ValidationReport]:
    """Validate and clean a raw 1-minute OHLCV bar DataFrame.

    Executes the full cleaning pipeline in a deterministic order:
    schema check → sort → deduplicate → NaN/Inf purge → OHLC geometry →
    gap audit → strict-mode gate.

    Args:
        df: Raw bar DataFrame.  Must contain at minimum
            ``["timestamp", "open", "high", "low", "close", "volume"]``.
            The ``timestamp`` column must be timezone-aware.
        strict: When ``True``, raises :class:`DataValidationError` if the
            fraction of OHLC-invalid bars exceeds 1 % of the input.

    Returns:
        A 2-tuple ``(clean_df, report)`` where:

        * ``clean_df`` — cleaned, chronologically sorted DataFrame.
        * ``report`` — :class:`ValidationReport` with audit metrics.

    Raises:
        DataValidationError: If required columns are missing, or if
            ``strict=True`` and OHLC error rate > 1 %.
    """
    logger.info("Validating %d input bars.", len(df))
    n_input = len(df)

    # 1. Schema check
    _check_required_columns(df)

    # 2. Sort chronologically
    df_work = _sort_chronologically(df)

    # 3. Remove duplicates
    df_work, n_dups = _remove_duplicates(df_work)
    if n_dups:
        logger.info("Removed %d duplicate timestamp(s).", n_dups)

    # 4. Purge NaN / Inf
    df_work, n_nan_inf = _remove_nan_inf(df_work)
    if n_nan_inf:
        logger.warning("Dropped %d bar(s) containing NaN/Inf values.", n_nan_inf)

    # 5. OHLC geometric validation
    df_work, n_ohlc_invalid = _validate_ohlc_geometry(df_work)

    # Count total invalid removals (NaN/Inf + OHLC geometry)
    n_invalid_total = n_nan_inf + n_ohlc_invalid

    # 6. Strict-mode gate: >1% OHLC errors → raise
    if strict and n_input > 0:
        error_rate = n_invalid_total / n_input
        if error_rate > _OHLC_ERROR_THRESHOLD:
            raise DataValidationError(
                f"OHLC error rate {error_rate:.2%} exceeds 1% threshold "
                f"({n_invalid_total} of {n_input} bars invalid).",
                column="ohlc",
            )

    # 7. Gap audit (RTH only)
    n_missing, flagged_sessions = _audit_rth_gaps(df_work, timeframe)

    is_valid = len(flagged_sessions) == 0 and (
        n_input == 0 or (n_invalid_total / max(n_input, 1)) <= _OHLC_ERROR_THRESHOLD
    )

    report = ValidationReport(
        total_bars_input=n_input,
        total_bars_output=len(df_work),
        duplicates_removed=n_dups,
        invalid_ohlc_removed=n_invalid_total,
        missing_timestamps_count=n_missing,
        flagged_sessions=flagged_sessions,
        is_valid=is_valid,
    )

    logger.info(report.summary())
    return df_work, report
