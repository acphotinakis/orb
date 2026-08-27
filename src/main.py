"""
src.main
========
Command-Line Interface (CLI) entry point for the ORB Opening Range Breakout system.

Usage::

    python -m src.main --config config/default_config.yaml
    python -m src.main --symbol AAPL --timeframe 5Min --start-date 2024-01-01
    python -m src.main --symbol SPY --feed iex --no-plots --log-level DEBUG
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from src.common.config import load_config
from src.common.logger import setup_logging, get_logger
from src.common.exceptions import ORBBaseException
from src.pipeline import ORBPipeline

logger = get_logger(__name__)


def parse_args(args=None) -> argparse.Namespace:
    """Parse and return command-line arguments for the ORB backtest pipeline."""
    parser = argparse.ArgumentParser(
        description="Opening Range Breakout (ORB) Quantitative Backtesting Engine.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config/default_config.yaml",
        help="Path to YAML configuration file.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="baseline_v1",
        help="Run identifier tag used in the experiment directory name.",
    )
    parser.add_argument(
        "--symbol", "-s",
        type=str,
        default=None,
        help="Override ticker symbol (e.g. AAPL, NVDA, QQQ).",
    )
    parser.add_argument(
        "--timeframe", "-tf",
        type=str,
        default=None,
        help="Override bar timeframe (e.g. 1Min, 5Min, 15Min, 1Hour).",
    )
    parser.add_argument(
        "--feed",
        type=str,
        choices=["iex", "sip"],
        default=None,
        help="Override Alpaca market data feed.",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Backtest start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Backtest end date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        default=False,
        help="Force re-download of raw bars, bypassing disk cache.",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        default=False,
        help="Skip all chart generation.",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Use paper trading credentials (default). Negate with --no-paper.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level.",
    )

    return parser.parse_args(args)


def _build_overrides_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    """Convert parsed CLI arguments into a nested override dict for ``load_config()``.

    Only non-None values are included so that YAML defaults are preserved when
    a CLI flag is not provided.

    Args:
        args: Parsed argument namespace from :func:`parse_args`.

    Returns:
        Nested dictionary mirroring the YAML config structure.
    """
    overrides: Dict[str, Any] = {}

    if args.symbol:
        sym = args.symbol.upper()
        overrides.setdefault("strategy", {})["ticker"] = sym
        overrides.setdefault("data", {})["symbol"] = sym

    if args.timeframe:
        overrides.setdefault("data", {})["timeframe"] = args.timeframe

    if args.feed:
        overrides.setdefault("data", {})["feed"] = args.feed

    return overrides


def main() -> int:
    """Main execution function invoked from the CLI.

    Returns:
        Exit code (0 = success, 1 = error).
    """
    args = parse_args()

    # Configure centralized logging before anything else
    setup_logging(level=args.log_level)

    try:
        # Build override dict from CLI flags
        overrides = _build_overrides_from_args(args)

        # Load config — validate_config() is called automatically inside load_config()
        cfg = load_config(args.config, overrides=overrides)

        # Execute pipeline
        pipeline = ORBPipeline(config=cfg)
        pipeline.run(
            start_date=args.start_date,
            end_date=args.end_date,
            refresh_cache=args.refresh_cache,
            generate_plots=not args.no_plots,
            run_id=args.run_id,
        )
        return 0

    except ORBBaseException as orb_err:
        logger.error("ORB Pipeline Error: %s", orb_err, exc_info=True)
        return 1
    except Exception as exc:
        logger.error("Fatal Unexpected Error: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
