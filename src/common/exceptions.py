"""
src.common.exceptions
=====================
Domain-specific exception hierarchy for the SPY Opening Range Breakout (ORB)
quantitative trading system.

All ORB-specific exceptions inherit from :class:`ORBBaseException` so that
callers can catch the entire family with a single ``except ORBBaseException``
clause, or narrow to a specific sub-class as needed.

Exception Hierarchy
-------------------
::

    ORBBaseException
    ├── ConfigurationError      — invalid / missing configuration values
    ├── DataFetchError          — Alpaca API / network failures
    ├── DataValidationError     — OHLCV integrity violations
    ├── TemporalLeakageError    — lookahead bias / causality violations  ★ FATAL
    └── BacktestExecutionError  — simulation / order-tracking runtime errors

Usage
-----
    from src.common.exceptions import (
        TemporalLeakageError,
        DataValidationError,
    )

    if bar_index >= or_end_index:
        raise TemporalLeakageError(
            "Opening range calculation read a bar past the OR window.",
            bar_index=bar_index,
            session_date="2024-01-02",
        )
"""

from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class ORBBaseException(Exception):
    """Base exception for all ORB quantitative trading system errors.

    All pipeline exceptions inherit from this class, enabling callers to
    distinguish ORB-domain errors from unexpected third-party exceptions.

    Args:
        message: Human-readable error description.
        **context: Arbitrary keyword metadata attached to the exception for
            structured logging and debugging (e.g. ``session_date``,
            ``bar_index``, ``ticker``).

    Example::

        raise ORBBaseException("Something went wrong", ticker="SPY", bar=42)
    """

    def __init__(self, message: str, **context: Any) -> None:
        self.context: dict[str, Any] = context
        formatted = message
        if context:
            ctx_str = ", ".join(f"{k}={v!r}" for k, v in context.items())
            formatted = f"{message} [{ctx_str}]"
        super().__init__(formatted)


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------

class ConfigurationError(ORBBaseException):
    """Raised when a configuration file fails schema or value validation.

    This is the canonical definition; ``src.common.config`` imports from here.

    Args:
        message: Human-readable description of the constraint violation.
        field: Dotted YAML field path that caused the error, e.g.
            ``"strategy.opening_range_minutes"``.
        **context: Additional metadata passed to :class:`ORBBaseException`.

    Example::

        raise ConfigurationError(
            "opening_range_minutes must be > 0; got 0.",
            field="strategy.opening_range_minutes",
        )
    """

    def __init__(
        self,
        message: str,
        field: str = "",
        **context: Any,
    ) -> None:
        self.field = field
        prefix = f"[config{':' + field if field else ''}] "
        super().__init__(prefix + message, **context)


# ---------------------------------------------------------------------------
# Data layer errors
# ---------------------------------------------------------------------------

class DataFetchError(ORBBaseException):
    """Raised when a network or API failure occurs while querying Alpaca.

    Args:
        message: Human-readable description of the fetch failure.
        ticker: Symbol being fetched (e.g. ``"SPY"``).
        start_date: ISO date string of the requested start of the fetch window.
        end_date: ISO date string of the requested end of the fetch window.
        **context: Additional metadata passed to :class:`ORBBaseException`.

    Example::

        raise DataFetchError(
            "Alpaca API returned HTTP 429 (rate limit).",
            ticker="SPY",
            start_date="2024-01-02",
            end_date="2024-12-31",
        )
    """

    def __init__(
        self,
        message: str,
        ticker: str = "",
        start_date: str = "",
        end_date: str = "",
        **context: Any,
    ) -> None:
        if ticker:
            context["ticker"] = ticker
        if start_date:
            context["start_date"] = start_date
        if end_date:
            context["end_date"] = end_date
        super().__init__(message, **context)


class DataValidationError(ORBBaseException):
    """Raised when OHLCV bar data fails integrity validation.

    Covers NaN values, inverted OHLC (``high < low``), zero volume on a
    trading bar, and any other data-quality violation detected before the
    data enters the strategy or backtest layers.

    Args:
        message: Human-readable description of the integrity violation.
        session_date: Affected trading date (``YYYY-MM-DD``).
        bar_index: Integer index of the offending bar in the session DataFrame.
        column: Name of the column where the violation was detected.
        **context: Additional metadata passed to :class:`ORBBaseException`.

    Example::

        raise DataValidationError(
            "NaN detected in 'close' column.",
            session_date="2024-01-02",
            bar_index=3,
            column="close",
        )
    """

    def __init__(
        self,
        message: str,
        session_date: str = "",
        bar_index: Optional[int] = None,
        column: str = "",
        **context: Any,
    ) -> None:
        if session_date:
            context["session_date"] = session_date
        if bar_index is not None:
            context["bar_index"] = bar_index
        if column:
            context["column"] = column
        super().__init__(message, **context)


# ---------------------------------------------------------------------------
# Temporal causality guard
# ---------------------------------------------------------------------------

class TemporalLeakageError(ORBBaseException):
    """Raised when lookahead bias or a temporal causality violation is detected.

    This exception is **fatal by design**: it must immediately halt execution
    to prevent silent production of invalid, future-contaminated backtest
    results.  It should never be caught and suppressed.

    Temporal leakage violations include (non-exhaustive):

    * Using bar data at or after the OR-end boundary to calculate the opening
      range (e.g. consuming the 09:45 bar during OR construction).
    * Accessing tomorrow's price data to make today's trade decision.
    * Fitting a scaler on data that includes the test / validation period.

    Args:
        message: Human-readable description of the leakage event.
        session_date: Affected trading date (``YYYY-MM-DD``).
        bar_index: Integer index of the bar that triggered the violation.
        offending_timestamp: ISO timestamp string of the future bar accessed.
        **context: Additional metadata passed to :class:`ORBBaseException`.

    Example::

        raise TemporalLeakageError(
            "OR calculation consumed bar at 09:45 ET — outside [09:30, 09:45).",
            session_date="2024-01-02",
            offending_timestamp="2024-01-02T09:45:00-05:00",
        )
    """

    def __init__(
        self,
        message: str,
        session_date: str = "",
        bar_index: Optional[int] = None,
        offending_timestamp: str = "",
        **context: Any,
    ) -> None:
        if session_date:
            context["session_date"] = session_date
        if bar_index is not None:
            context["bar_index"] = bar_index
        if offending_timestamp:
            context["offending_timestamp"] = offending_timestamp
        super().__init__(f"TEMPORAL LEAKAGE DETECTED — {message}", **context)


# ---------------------------------------------------------------------------
# Backtest runtime errors
# ---------------------------------------------------------------------------

class BacktestExecutionError(ORBBaseException):
    """Raised for runtime errors inside the event-driven backtest simulation.

    Covers invalid state transitions, duplicate position opens, order
    mismatches, and any other invariant violation inside the backtest engine.

    Args:
        message: Human-readable description of the execution error.
        session_date: Affected trading date (``YYYY-MM-DD``).
        bar_index: Bar index at which the error occurred.
        trade_id: Identifier of the trade being processed when the error
            was raised.
        **context: Additional metadata passed to :class:`ORBBaseException`.

    Example::

        raise BacktestExecutionError(
            "Attempted to open a second position on an already-active session.",
            session_date="2024-01-02",
            bar_index=12,
        )
    """

    def __init__(
        self,
        message: str,
        session_date: str = "",
        bar_index: Optional[int] = None,
        trade_id: str = "",
        **context: Any,
    ) -> None:
        if session_date:
            context["session_date"] = session_date
        if bar_index is not None:
            context["bar_index"] = bar_index
        if trade_id:
            context["trade_id"] = trade_id
        super().__init__(message, **context)
