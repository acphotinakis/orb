"""
src.pipeline
============
End-to-End Pipeline Orchestrator for the SPY ORB quantitative trading system.

Coordinates:
1. Data Ingestion (Alpaca Data Client & Parquet cache)
2. Bar Validation & Cleaning
3. RTH Session Processing
4. Event-Driven Backtest Simulation
5. Performance & Risk Metrics Calculation
6. Artifact Reporting & Disk Persistence
7. Visualization & Chart Generation
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd

from src.common.config import AppConfig
from src.common.logger import get_logger
from src.common.exceptions import ORBBaseException
from src.data.fetcher import DataFetcher
from src.data.validator import validate_and_clean_bars
from src.data.processor import DataProcessor
from src.backtest.engine import BacktestEngine, BacktestResult
from src.evaluation.metrics import generate_performance_report
from src.evaluation.reporter import ResultsReporter
from src.visualization.candlestick_data_plotter import CandlestickDataPlotter
from src.visualization.candlestick_plotter import CandlestickTradePlotter
from src.visualization.performance_plotter import PerformancePlotter

import sys

logger = get_logger(__name__)


@dataclass
class PipelineRunResult:
    """Summary record returned upon successful pipeline execution."""

    config: AppConfig
    metrics: Dict[str, Any]
    artifacts: Dict[str, Path]
    total_trades: int
    execution_time_seconds: float


class ORBPipeline:
    """Orchestrates end-to-end execution of the SPY ORB trading strategy."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.fetcher = DataFetcher(config=config)
        self.processor = DataProcessor(config=config)
        self.engine = BacktestEngine(config=config)
        self.reporter = ResultsReporter(base_results_dir=config.output.results_dir)
        self.candlestick_data_plotter = CandlestickDataPlotter(
            output_dir=f"{config.output.plots_dir}/candlesticks/{config.strategy.ticker}"
        )
        self.candlestick_plotter = CandlestickTradePlotter(
            output_dir=f"{config.output.plots_dir}/trades"
        )
        self.performance_plotter = PerformancePlotter(
            plots_base_dir=config.output.plots_dir
        )

    def run(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        refresh_cache: bool = False,
        generate_plots: bool = True,
        run_id: str = "SPY_baseline_v1",
    ) -> PipelineRunResult:
        """Executes the full pipeline workflow from data ingestion to reporting."""
        t0 = time.time()
        logger.info("=" * 60)
        logger.info("STARTING SPY ORB BACKTEST PIPELINE [Run: %s]", run_id)
        logger.info("=" * 60)

        # Parse date bounds if provided
        s_dt: Optional[datetime] = None
        e_dt: Optional[datetime] = None
        if start_date:
            s_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        if end_date:
            e_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, tzinfo=timezone.utc
            )

        # Step 1: Data Ingestion
        logger.info("[1/6] Ingesting raw historical bars (cache-first)...")
        raw_df = self.fetcher.fetch_and_cache(
            symbol=self.config.data.symbol,
            timeframe=self.config.data.timeframe,
            start_date=s_dt,
            end_date=e_dt,
            force_refresh=refresh_cache,
        )
        if raw_df.empty:
            raise RuntimeError("No historical bars retrieved. Pipeline aborted.")
        logger.info("Raw bars available: %d", len(raw_df))
        logger.info("Generating session market data candlestick charts...")
        candlestick_plots = self.candlestick_data_plotter.plot_all_sessions(
            df=raw_df,
            symbol=self.config.strategy.ticker,
            max_plots=20,
        )
        import sys 
        sys.exit(0)

        # Step 2: Data Validation & Cleaning
        logger.info("[2/6] Validating OHLCV bar integrity and checking for gaps...")
        cleaned_df, report = validate_and_clean_bars(raw_df, strict=False)
        logger.info("Validation complete: %s", report.summary())

        # Step 3: RTH Session Processing
        logger.info(
            "[3/6] Normalizing timezone to ET, filtering RTH (09:30-16:00), and tagging session metadata..."
        )
        processed_df = self.processor.process(cleaned_df, save_to_disk=True)
        if processed_df.empty:
            raise RuntimeError(
                "No RTH session bars after processing. Pipeline aborted."
            )
        logger.info(
            "Processed dataset ready: %d bars across %d session(s).",
            len(processed_df),
            processed_df["session_id"].nunique(),
        )

        # Step 4: Event-Driven Backtest Simulation
        logger.info(
            "[4/6] Executing bar-by-bar backtest simulation & dual-touch resolver..."
        )
        backtest_result: BacktestResult = self.engine.run(processed_df)
        logger.info(
            "Simulation completed: %d trades executed.", len(backtest_result.trades)
        )

        # Step 5: Performance & Risk Metrics
        logger.info("[5/6] Computing quantitative performance and risk metrics...")
        metrics = generate_performance_report(
            trades_df=backtest_result.trades_df,
            equity_df=backtest_result.equity_curve,
            config=self.config,
        )

        # Step 6: Artifact Reporting & Persistence
        logger.info("[6/6] Exporting CSV/JSON artifacts and generating plots...")
        artifacts = self.reporter.export_all(
            backtest_result=backtest_result,
            metrics=metrics,
            run_id=run_id,
        )

        # Visualizations (Optional)
        if generate_plots:
            logger.info("Generating session market data candlestick charts...")
            candlestick_plots = self.candlestick_data_plotter.plot_all_sessions(
                df=processed_df,
                symbol=self.config.strategy.ticker,
                max_plots=20,
            )
            for idx, cp in enumerate(candlestick_plots, 1):
                artifacts[f"candlestick_chart_{idx}"] = cp

            logger.info("Generating portfolio performance curves...")
            perf_plots = self.performance_plotter.generate_all_plots(
                backtest_result, metrics
            )
            artifacts.update(perf_plots)

            logger.info("Generating session trade candlestick charts...")
            trade_plots = self.candlestick_plotter.plot_all_trades(
                trades=backtest_result.trades,
                processed_bars=processed_df,
                max_plots=20,  # Cap trade charts to top 20 for fast execution
            )
            for idx, tp in enumerate(trade_plots, 1):
                artifacts[f"trade_chart_{idx}"] = tp

        # Display terminal executive summary
        self.reporter.display_console_summary(metrics)

        elapsed = time.time() - t0
        logger.info("PIPELINE EXECUTION FINISHED in %.2f seconds.", elapsed)

        return PipelineRunResult(
            config=self.config,
            metrics=metrics,
            artifacts=artifacts,
            total_trades=len(backtest_result.trades),
            execution_time_seconds=elapsed,
        )
