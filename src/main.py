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

from src.common.config import load_config, validate_config
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
        "--run-id",
        type=str,
        default="Sbaseline_v1",
        help="Custom run ID tag for result artifact folder naming.",
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
        validate_config(cfg)

        # Apply CLI overrides if provided
        import dataclasses
        strat_override = cfg.strategy
        data_override = cfg.data

        if args.symbol:
            sym = args.symbol.upper()
            strat_override = dataclasses.replace(strat_override, ticker=sym)
            data_override = dataclasses.replace(data_override, symbol=sym)

        if args.feed:
            data_override = dataclasses.replace(data_override, feed=args.feed.lower())

        cfg = dataclasses.replace(cfg, strategy=strat_override, data=data_override)

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
