"""
src.main
========
Command-Line Interface (CLI) entry point for the SPY Opening Range Breakout system.

Usage:
    python -m src.main --config config/default_config.yaml
    python src/main.py --start-date 2024-01-01 --end-date 2024-12-31 --symbol SPY
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.common.config import load_config
from src.common.logger import setup_logging, get_logger
from src.common.exceptions import ORBBaseException
from src.pipeline import ORBPipeline

logger = get_logger(__name__)


def parse_args(args=None) -> argparse.Namespace:
    """Parses command-line arguments for the ORB backtest pipeline."""
    parser = argparse.ArgumentParser(
        description="SPY Opening Range Breakout (ORB) Quantitative Backtesting Engine.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config/default_config.yaml",
        help="Path to YAML configuration file.",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date for backtesting window (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date for backtesting window (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Target asset ticker symbol (e.g. SPY).",
    )
    parser.add_argument(
        "--feed",
        type=str,
        choices=["iex", "sip"],
        default=None,
        help="Alpaca market data feed tier.",
    )
    parser.add_argument(
        "--ispaper",
        action="store_true",
        default=True,
        help="Use Alpaca paper trading credentials.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        default=False,
        help="Force re-download of raw historical data from Alpaca.",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        default=False,
        help="Skip visual plot generation for headless execution.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="SPY_baseline_v1",
        help="Custom run ID tag for result artifact folder naming.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging output verbosity level.",
    )

    return parser.parse_args(args)


def main() -> int:
    """Main execution function invoked from CLI."""
    args = parse_args()

    # Configure centralized logging
    setup_logging(level=args.log_level)

    try:
        # Load baseline config
        cfg = load_config(args.config)

        # Apply CLI overrides if provided
        overrides = {}
        if args.symbol:
            overrides["strategy.ticker"] = args.symbol.upper()
            overrides["data.symbol"] = args.symbol.upper()
        if args.feed:
            overrides["data.feed"] = args.feed.lower()

        if overrides:
            cfg = load_config(args.config, overrides=overrides)

        # Execute Pipeline
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
