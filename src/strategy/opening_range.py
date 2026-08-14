"""
src.strategy.opening_range
==========================
Opening Range (OR) calculation engine for the SPY ORB quantitative system.

This module is the **first module in the signal generation pipeline**.  It
computes the frozen OR boundaries for each trading session and enforces the
core temporal causality invariant: **no bar timestamped at or after 09:45:00
ET may influence the opening range calculation**.

Theory
------
The Opening Range is defined as the extreme price levels reached during the
first ``or_minutes`` minutes of the NYSE regular session (default: 15 min):

    OR High  = max( High_t )   for t ∈ [09:30:00, 09:30:00 + or_minutes)
    OR Low   = min( Low_t  )   for t ∈ [09:30:00, 09:30:00 + or_minutes)
    OR Width = OR High − OR Low

Once calculated, the OR is **immutable** (``frozen=True`` dataclass).  The
backtest engine reads these values for the entire remainder of the session
without re-computing them.

Temporal Causality Enforcement
-------------------------------
Two defence layers are applied:

1. :class:`OpeningRangeCalculator` only passes ``is_opening_range=True``
   bars to the calculation.
2. If the caller passes a session DataFrame that contains *any* bar with
   ``is_opening_range=False`` and ``minute_of_day >= or_minutes``, a
   :class:`~src.common.exceptions.TemporalLeakageError` is raised
   immediately before any calculation is attempted.

Usage
-----
    from src.strategy.opening_range import OpeningRangeCalculator, OpeningRange
    from src.common.config import load_config

    cfg = load_config()
    calc = OpeningRangeCalculator(or_minutes=cfg.strategy.opening_range_minutes)

    # Dict keyed by session_id ("YYYY-MM-DD")
    ranges: dict[str, OpeningRange] = calc.calculate_all(processed_df)
    or_today = ranges["2024-01-02"]
    print(or_today.or_high, or_today.or_low)
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from src.common.exceptions import DataValidationError, TemporalLeakageError
from src.common.logger import get_logger
from src.common.time_utils import EASTERN_TZ

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PARQUET_ENGINE: str = "pyarrow"
_COMPRESSION:    str = "snappy"
_OR_OUTPUT_FILENAME: str = "daily_ranges.parquet"

# Required columns from the processed dataset (TASK-008 output)
_REQUIRED_COLS: list[str] = [
    "session_id", "timestamp", "high", "low", "volume",
    "is_opening_range", "minute_of_day",
]


# ---------------------------------------------------------------------------
# OpeningRange dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpeningRange:
    """Immutable opening range record for a single trading session.

    All fields are set once at calculation time (09:45 ET freeze point) and
    cannot be modified afterward.

    Attributes:
        session_id: Trading date string ``YYYY-MM-DD``.
        start_time: Timestamp of the first OR bar (09:30:00 ET).
        end_time: Timestamp of the last OR bar included in the calculation
            (09:44:00 ET for the default 15-minute window).
        or_high: Maximum ``High`` across all OR bars.
        or_low: Minimum ``Low`` across all OR bars.
        or_width: ``or_high - or_low``.  Must be ``> 0`` for a valid range.
        total_volume: Sum of ``Volume`` across all OR bars.
        bar_count: Number of 1-minute bars used in the calculation.
        is_valid: ``True`` iff ``bar_count == expected_bar_count`` (i.e.
            exactly ``or_minutes`` bars are present) and ``or_width > 0``.
    """

    session_id:   str
    start_time:   pd.Timestamp
    end_time:     pd.Timestamp
    or_high:      float
    or_low:       float
    or_width:     float
    total_volume: float
    bar_count:    int
    is_valid:     bool

    def __str__(self) -> str:
        return (
            f"OpeningRange({self.session_id} | "
            f"High={self.or_high:.4f} Low={self.or_low:.4f} "
            f"Width={self.or_width:.4f} bars={self.bar_count} "
            f"valid={self.is_valid})"
        )


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------

class OpeningRangeCalculator:
    """Computes frozen opening range boundaries from processed session bars.

    Args:
        or_minutes: Duration of the opening range window in minutes.
            Must be a positive integer.  Default: 15.
        output_dir: Optional directory for persisting
            ``daily_ranges.parquet``.  If ``None``, persistence is skipped
            unless explicitly requested via :meth:`save_ranges`.
    """

    def __init__(
        self,
        or_minutes: int = 15,
        output_dir: Optional[Path] = None,
    ) -> None:
        if or_minutes <= 0:
            raise ValueError(
                f"or_minutes must be a positive integer; got {or_minutes}."
            )
        self._or_minutes = or_minutes
        self._output_dir = output_dir

    # ------------------------------------------------------------------
    # Core single-session calculation
    # ------------------------------------------------------------------

    def calculate_session_or(
        self,
        session_bars: pd.DataFrame,
    ) -> OpeningRange:
        """Compute the opening range for a single trading session.

        **Temporal causality guard**: raises :class:`TemporalLeakageError`
        if *session_bars* contains any bar with ``minute_of_day >=
        or_minutes`` whose ``is_opening_range`` flag is ``False`` but which
        was somehow included in the input slice.  This prevents the
        calculation from accidentally consuming post-OR data.

        Args:
            session_bars: Processed bar DataFrame for **one session only**.
                Must contain columns from :data:`_REQUIRED_COLS` and all
                timestamps must be ``America/New_York``-aware.

        Returns:
            A frozen :class:`OpeningRange` instance.

        Raises:
            DataValidationError: If required columns are missing or the
                DataFrame spans more than one session.
            TemporalLeakageError: If any bar outside the OR window is
                detected in the input.
        """
        # --- Column validation ---
        missing = [c for c in _REQUIRED_COLS if c not in session_bars.columns]
        if missing:
            raise DataValidationError(
                f"OpeningRangeCalculator: missing required columns: {missing}.",
            )

        # --- Isolate OR bars only (is_opening_range = True) ---
        or_bars = session_bars[session_bars["is_opening_range"] == True].copy()

        # --- Temporal causality guard ---
        # Any bar with minute_of_day >= or_minutes that is NOT flagged as
        # opening range is fine (it's a trading bar).  What we must prevent
        # is accidentally slipping a trading bar into or_bars.
        if not or_bars.empty:
            max_mod = int(or_bars["minute_of_day"].max())
            if max_mod >= self._or_minutes:
                offending_ts = or_bars[
                    or_bars["minute_of_day"] >= self._or_minutes
                ]["timestamp"].iloc[0]
                raise TemporalLeakageError(
                    f"OR calculation received bar with minute_of_day="
                    f"{max_mod} >= or_minutes={self._or_minutes}. "
                    "Post-OR bar included in opening range window.",
                    offending_timestamp=str(offending_ts),
                )

        # --- Session identity ---
        if "session_id" in session_bars.columns:
            session_id_vals = session_bars["session_id"].unique()
            if len(session_id_vals) > 1:
                raise DataValidationError(
                    f"calculate_session_or received bars from multiple sessions: "
                    f"{list(session_id_vals)}. Pass one session at a time."
                )
            session_id = str(session_id_vals[0])
        else:
            # Derive from timestamp if session_id column absent
            session_id = (
                or_bars["timestamp"].iloc[0].tz_convert(EASTERN_TZ).strftime("%Y-%m-%d")
                if not or_bars.empty
                else "UNKNOWN"
            )

        # --- Handle empty OR window ---
        if or_bars.empty:
            logger.warning(
                "Session %s: no OR bars found — returning invalid OpeningRange.",
                session_id,
            )
            return OpeningRange(
                session_id=session_id,
                start_time=pd.NaT,
                end_time=pd.NaT,
                or_high=float("nan"),
                or_low=float("nan"),
                or_width=float("nan"),
                total_volume=0.0,
                bar_count=0,
                is_valid=False,
            )

        # --- Core calculation (vectorised) ---
        or_high:      float = float(or_bars["high"].max())
        or_low:       float = float(or_bars["low"].min())
        or_width:     float = or_high - or_low
        total_volume: float = float(or_bars["volume"].sum())
        bar_count:    int   = len(or_bars)

        start_time = or_bars["timestamp"].min()
        end_time   = or_bars["timestamp"].max()

        # Validity gate
        is_valid: bool = (bar_count == self._or_minutes) and (or_width > 0)

        if not is_valid:
            logger.warning(
                "Session %s: OR invalid — bar_count=%d (expected %d), "
                "or_width=%.4f.",
                session_id, bar_count, self._or_minutes, or_width,
            )
        else:
            logger.debug(
                "Session %s: OR frozen — High=%.4f Low=%.4f Width=%.4f bars=%d.",
                session_id, or_high, or_low, or_width, bar_count,
            )

        return OpeningRange(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            or_high=or_high,
            or_low=or_low,
            or_width=or_width,
            total_volume=total_volume,
            bar_count=bar_count,
            is_valid=is_valid,
        )

    # ------------------------------------------------------------------
    # Multi-session batch calculation
    # ------------------------------------------------------------------

    def calculate_all(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, OpeningRange]:
        """Compute opening ranges for all sessions in a processed dataset.

        Iterates over sessions in chronological order.  Each session's OR is
        calculated independently; a failure in one session (e.g. missing OR
        bars) does not abort the entire batch — the session receives an
        ``is_valid=False`` record instead.

        Args:
            df: Processed bar DataFrame (output of :class:`~src.data.processor.DataProcessor`).
                Must contain a ``session_id`` column and all columns in
                :data:`_REQUIRED_COLS`.

        Returns:
            Dictionary mapping ``session_id`` strings (``"YYYY-MM-DD"``) to
            their corresponding :class:`OpeningRange` instances.

        Raises:
            DataValidationError: If required columns are missing.
        """
        missing = [c for c in _REQUIRED_COLS if c not in df.columns]
        if missing:
            raise DataValidationError(
                f"calculate_all: missing required columns: {missing}."
            )

        session_ids = sorted(df["session_id"].unique())
        logger.info(
            "OpeningRangeCalculator: computing OR for %d session(s).",
            len(session_ids),
        )

        ranges: Dict[str, OpeningRange] = {}
        for sid in session_ids:
            session_df = df[df["session_id"] == sid]
            try:
                opening_range = self.calculate_session_or(session_df)
            except (DataValidationError, TemporalLeakageError) as exc:
                logger.error(
                    "Session %s: OR calculation failed — %s", sid, exc
                )
                # Re-raise TemporalLeakageError — it is always fatal
                if isinstance(exc, TemporalLeakageError):
                    raise
                # For non-fatal validation errors, record an invalid OR
                opening_range = OpeningRange(
                    session_id=sid,
                    start_time=pd.NaT,
                    end_time=pd.NaT,
                    or_high=float("nan"),
                    or_low=float("nan"),
                    or_width=float("nan"),
                    total_volume=0.0,
                    bar_count=0,
                    is_valid=False,
                )
            ranges[sid] = opening_range

        n_valid = sum(1 for r in ranges.values() if r.is_valid)
        logger.info(
            "OR calculation complete: %d/%d sessions valid.",
            n_valid, len(ranges),
        )

        # Auto-save if output_dir is configured
        if self._output_dir is not None:
            self.save_ranges(ranges, self._output_dir)

        return ranges

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_ranges(
        self,
        ranges: Dict[str, OpeningRange],
        output_dir: Path,
    ) -> Path:
        """Serialise computed opening ranges to a Parquet file.

        Args:
            ranges: Dictionary of ``session_id → OpeningRange`` as returned
                by :meth:`calculate_all`.
            output_dir: Directory in which ``daily_ranges.parquet`` is written.

        Returns:
            Path to the written Parquet file.
        """
        records = []
        for sid, r in sorted(ranges.items()):
            records.append({
                "session_id":   r.session_id,
                "start_time":   r.start_time,
                "end_time":     r.end_time,
                "or_high":      r.or_high,
                "or_low":       r.or_low,
                "or_width":     r.or_width,
                "total_volume": r.total_volume,
                "bar_count":    r.bar_count,
                "is_valid":     r.is_valid,
            })

        out_df = pd.DataFrame(records)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / _OR_OUTPUT_FILENAME

        out_df.to_parquet(
            out_path, engine=_PARQUET_ENGINE, compression=_COMPRESSION, index=False
        )
        logger.info(
            "Saved %d opening ranges → %s", len(out_df), out_path
        )
        return out_path

    @staticmethod
    def load_ranges(path: Path) -> Dict[str, OpeningRange]:
        """Load persisted opening ranges from a Parquet file.

        Args:
            path: Path to ``daily_ranges.parquet``.

        Returns:
            Dictionary mapping ``session_id → OpeningRange``.

        Raises:
            FileNotFoundError: If *path* does not exist.
        """
        if not path.exists():
            raise FileNotFoundError(
                f"Opening ranges file not found: '{path}'."
            )
        df = pd.read_parquet(path, engine=_PARQUET_ENGINE)
        result: Dict[str, OpeningRange] = {}
        for _, row in df.iterrows():
            result[row["session_id"]] = OpeningRange(
                session_id=str(row["session_id"]),
                start_time=pd.Timestamp(row["start_time"]),
                end_time=pd.Timestamp(row["end_time"]),
                or_high=float(row["or_high"]),
                or_low=float(row["or_low"]),
                or_width=float(row["or_width"]),
                total_volume=float(row["total_volume"]),
                bar_count=int(row["bar_count"]),
                is_valid=bool(row["is_valid"]),
            )
        return result


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def compute_opening_ranges(
    df: pd.DataFrame,
    or_minutes: int = 15,
    output_dir: Optional[Path] = None,
) -> Dict[str, OpeningRange]:
    """Functional convenience wrapper around :class:`OpeningRangeCalculator`.

    Args:
        df: Processed bar DataFrame (TASK-008 output).
        or_minutes: Opening range window duration in minutes.
        output_dir: Optional directory for Parquet persistence.

    Returns:
        Dictionary mapping ``session_id → OpeningRange``.
    """
    calc = OpeningRangeCalculator(or_minutes=or_minutes, output_dir=output_dir)
    return calc.calculate_all(df)
