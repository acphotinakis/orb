"""
src.common.logger
=================
Centralized, level-controlled logging factory for the SPY Opening Range
Breakout (ORB) quantitative trading system.

All pipeline modules obtain their logger via :func:`get_logger` rather than
calling ``logging.getLogger()`` directly.  This ensures:

* A single call to :func:`setup_logging` configures the root ``"orb"``
  logger once per process.
* Subsequent calls to :func:`get_logger` return correctly-namespaced child
  loggers that inherit the root handler configuration without duplicating
  handlers.
* The format is consistent and machine-parseable across every module.

Log Format
----------
::

    2024-01-02 09:30:05,123 [INFO ] orb.data.fetcher - Fetching SPY bars …

Usage
-----
    from src.common.logger import setup_logging, get_logger

    # At application entry point (once per process):
    setup_logging(level="INFO", log_file=Path("results/backtest/execution.log"))

    # Inside any module:
    logger = get_logger(__name__)
    logger.info("Backtest started.")
    logger.debug("Bar processed: index=%d close=%.2f", bar_idx, close_price)

Notes
-----
* Calling :func:`setup_logging` multiple times is safe — existing handlers
  on the ``"orb"`` root logger are cleared before new ones are attached,
  preventing the doubled-output problem common in long-running notebooks.
* The log format uses ``%(asctime)s`` which renders in local time by default.
  This is intentional: the pipeline runs in ``America/New_York`` and local
  log times are easier to correlate with bar timestamps during debugging.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ORB_ROOT_LOGGER: str = "orb"
"""Root logger name for the entire ORB pipeline.

All child loggers (e.g. ``"orb.data.fetcher"``, ``"orb.strategy.signal"``)
inherit their handlers from this root, so only one ``setup_logging()`` call
is needed per process.
"""

_LOG_FORMAT: str = "%(asctime)s [%(levelname)-8s] %(name)s - %(message)s"
"""Standardised log record format shared by all handlers.

Columns:
  - ``%(asctime)s``      — Timestamp in local time (ET when running in NY)
  - ``%(levelname)-8s``  — Level name, left-aligned in 8-char field for
                            visual alignment across DEBUG/INFO/WARNING/ERROR
  - ``%(name)s``         — Logger name (module path, e.g. ``orb.backtest.engine``)
  - ``%(message)s``      — The formatted log message
"""

_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
"""strftime format for the timestamp field in log records."""

_VALID_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
) -> logging.Logger:
    """Configure the ORB root logger with console (and optional file) output.

    This function is idempotent — calling it multiple times replaces existing
    handlers on the ``"orb"`` root logger rather than stacking them.

    Args:
        level: Minimum log level string.  One of ``"DEBUG"``, ``"INFO"``,
            ``"WARNING"``, ``"ERROR"``, ``"CRITICAL"``.  Case-insensitive.
            Defaults to ``"INFO"``.
        log_file: Optional path to a log file.  If provided, a
            ``FileHandler`` is attached in addition to the ``StreamHandler``.
            Parent directories are created automatically if they do not exist.

    Returns:
        The configured ``"orb"`` root :class:`logging.Logger`.

    Raises:
        ValueError: If *level* is not a recognised log level string.

    Example::

        from pathlib import Path
        from src.common.logger import setup_logging

        logger = setup_logging(
            level="DEBUG",
            log_file=Path("results/backtest/execution.log"),
        )
        logger.info("ORB pipeline initialised.")
    """
    level_upper = level.upper()
    if level_upper not in _VALID_LEVELS:
        raise ValueError(
            f"Invalid log level '{level}'. "
            f"Must be one of: {sorted(_VALID_LEVELS)}"
        )

    root = logging.getLogger(_ORB_ROOT_LOGGER)

    # Clear existing handlers to prevent duplication on repeated calls
    # (common in Jupyter notebooks and long-lived processes).
    root.handlers.clear()
    root.setLevel(getattr(logging, level_upper))

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler — always active
    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(getattr(logging, level_upper))
    root.addHandler(stream_handler)

    # Optional file handler
    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(getattr(logging, level_upper))
        root.addHandler(file_handler)

    # Prevent propagation to the Python root logger to avoid duplicate output
    # when third-party libraries configure a root handler.
    root.propagate = False

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger scoped under the ``"orb"`` root logger.

    The returned logger inherits the handler and level configuration set by
    :func:`setup_logging`.  If :func:`setup_logging` has not been called yet,
    the logger is returned unconfigured (no handlers); it will silently drop
    all records until the root is configured.

    Naming convention: pass ``__name__`` from the calling module so the
    logger hierarchy mirrors the package hierarchy::

        orb
        ├── orb.data.fetcher
        ├── orb.strategy.signal
        └── orb.backtest.engine

    Args:
        name: Logger name.  For module-level loggers, pass ``__name__``.
            The ``"orb."`` prefix is prepended automatically if not present.

    Returns:
        A :class:`logging.Logger` child of the ``"orb"`` root.

    Example::

        # In src/data/fetcher.py
        from src.common.logger import get_logger
        logger = get_logger(__name__)   # → "orb.data.fetcher" (if __name__ starts with src)
        logger.info("Fetching bars for %s", ticker)
    """
    # Normalise the name so callers can pass __name__ (which starts with "src")
    # or an explicit short name.
    if not name.startswith(_ORB_ROOT_LOGGER):
        # Map "src.common.config" → "orb.common.config" for clarity,
        # or just prefix a bare name like "fetcher" → "orb.fetcher".
        if name.startswith("src."):
            name = _ORB_ROOT_LOGGER + "." + name[len("src."):]
        elif name == "src":
            name = _ORB_ROOT_LOGGER
        else:
            name = f"{_ORB_ROOT_LOGGER}.{name}"

    return logging.getLogger(name)
