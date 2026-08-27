"""
src.pipeline
============
End-to-End Pipeline Orchestrator for the ORB quantitative trading system.

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
from typing import Dict, Any, Optional, Union
import pandas as pd

from src.common.config import AppConfig
from src.common.logger import get_logger, setup_logging
from src.common.paths import PathManager
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
    """Orchestrates end-to-end execution of the ORB trading strategy."""

    def __init__(
        self,
        config: AppConfig,
        paths: Optional[PathManager] = None,
        base_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        """Initialise the pipeline with a validated config.

        Service instances (fetcher, processor, etc.) are constructed lazily
        inside :meth:`run` once the date range and run ID are known and
        :class:`~src.common.paths.PathManager` can be built.

        Args:
            config: Loaded and validated :class:`~src.common.config.AppConfig`.
            paths: Optional pre-constructed :class:`~src.common.paths.PathManager`.
            base_dir: Optional base directory override for filesystem root.
        """
        self.config = config
        self.paths = paths
        self.base_dir = base_dir

    def run(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        refresh_cache: bool = False,
        generate_plots: bool = True,
        run_id: str = "baseline_v1",
        log_level: str = "INFO",
        base_dir: Optional[Union[str, Path]] = None,
    ) -> PipelineRunResult:
        """Execute the full pipeline workflow from data ingestion to reporting.

        Args:
            start_date: Backtest start date (``"YYYY-MM-DD"``).  ``None``
                uses the earliest available cached data.
            end_date: Backtest end date (``"YYYY-MM-DD"``).  ``None``
                uses the most recent available data.
            refresh_cache: When ``True``, bypass the raw Parquet cache and
                re-fetch from Alpaca.
            generate_plots: When ``True``, produce all chart outputs.
            run_id: Experiment identifier used in directory naming.
            log_level: Logging verbosity for this run.
            base_dir: Base directory override for filesystem root.

        Returns:
            :class:`PipelineRunResult` with metrics, artifact paths, and
            timing information.
        """
        t0 = time.time()
        artifacts: Dict[str, Path] = {}

        # ── Parse date bounds ─────────────────────────────────────────
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

        logger.info(
            "Date Bounds — start_date (raw): %s | s_dt (parsed UTC): %s",
            start_date,
            s_dt,
        )
        logger.info(
            "Date Bounds — end_date (raw)  : %s | e_dt (parsed UTC): %s", end_date, e_dt
        )

        # ── Build PathManager — single source of truth for all paths ──
        active_base = base_dir or self.base_dir
        if self.paths is not None:
            paths = self.paths
        else:
            paths = PathManager(
                config=self.config,
                run_id=run_id,
                start_date=s_dt,
                end_date=e_dt,
                base_dir=active_base,
            )

        # ── Redirect logging to per-experiment log file ───────────────
        setup_logging(level=log_level, log_file=paths.execution_log)

        logger.info("=" * 60)
        logger.info("STARTING ORB BACKTEST PIPELINE [Run: %s]", run_id)
        logger.info("=" * 60)
        logger.info("%s", paths.summary())

        # ── Save config snapshot before any computation ───────────────
        self.config.to_yaml(paths.config_snapshot_yaml)
        logger.info("Config snapshot saved: %s", paths.config_snapshot_yaml)

        # ── Build services with injected paths ────────────────────────
        fetcher = DataFetcher(
            config=self.config,
            cache_dir=paths.raw_data_dir,
        )
        processor = DataProcessor(
            config=self.config,
            output_dir=paths.processed_data_dir,
        )
        engine = BacktestEngine(config=self.config)
        reporter = ResultsReporter(output_dir=paths.results_dir)
        data_plotter = CandlestickDataPlotter(output_dir=paths.candlestick_plots_dir)
        trade_plotter = CandlestickTradePlotter(output_dir=paths.trade_plots_dir)
        perf_plotter = PerformancePlotter(
            equity_dir=paths.equity_curves_dir,
            drawdown_dir=paths.drawdowns_dir,
            distributions_dir=paths.distributions_dir,
        )

        # ── Initialize artifacts container upfront ────────────────────
        artifacts: Dict[str, Path] = {}

        # ── Step 1: Data Ingestion ────────────────────────────────────
        logger.info("[1/6] Ingesting raw historical bars (cache-first)...")
        raw_df = fetcher.fetch_and_cache(
            symbol=self.config.data.symbol,
            timeframe=self.config.data.timeframe,
            start_date=s_dt,
            end_date=e_dt,
            force_refresh=refresh_cache,
        )
        if raw_df.empty:
            raise RuntimeError("No historical bars retrieved. Pipeline aborted.")
        logger.info("Raw bars available: %d", len(raw_df))

        if generate_plots:
            raw_plotter = CandlestickDataPlotter(output_dir=paths.raw_candlestick_plots_dir)
            raw_plots = raw_plotter.plot_all_sessions(
                df=raw_df.tail(int(len(raw_df) * 0.2)),
                stage="raw",
                symbol=self.config.strategy.ticker,
                max_plots=20,
            )
            for idx, p in enumerate(raw_plots, 1):
                artifacts[f"raw_candlestick_chart_{idx}"] = p

        # ── Step 2: Data Validation & Cleaning ───────────────────────
        logger.info("[2/6] Validating OHLCV bar integrity and checking for gaps...")
        cleaned_df, report = validate_and_clean_bars(
            raw_df, timeframe=self.config.data.timeframe, strict=False
        )
        logger.info("Validation complete: %s", report.summary())

        if generate_plots:
            cleaned_plotter = CandlestickDataPlotter(output_dir=paths.cleaned_candlestick_plots_dir)
            cleaned_plots = cleaned_plotter.plot_all_sessions(
                df=cleaned_df.tail(int(len(cleaned_df) * 0.2)),
                stage="cleaned",
                symbol=self.config.strategy.ticker,
                max_plots=20,
            )
            for idx, p in enumerate(cleaned_plots, 1):
                artifacts[f"cleaned_candlestick_chart_{idx}"] = p

        # ── Step 3: RTH Session Processing ────────────────────────────
        logger.info(
            "[3/6] Normalizing timezone to ET, filtering RTH (09:30-16:00), "
            "and tagging session metadata..."
        )
        processed_df = processor.process(
            cleaned_df,
            save_to_disk=True,
            output_path=paths.processed_file,
        )
        if processed_df.empty:
            raise RuntimeError(
                "No RTH session bars after processing. Pipeline aborted."
            )
        logger.info(
            "Processed dataset ready: %d bars across %d session(s).",
            len(processed_df),
            processed_df["session_id"].nunique(),
        )

        if generate_plots:
            processed_plotter = CandlestickDataPlotter(output_dir=paths.processed_candlestick_plots_dir)
            processed_plots = processed_plotter.plot_all_sessions(
                df=processed_df.tail(int(len(processed_df) * 0.2)),
                stage="processed",
                symbol=self.config.strategy.ticker,
                max_plots=20,
            )
            for idx, p in enumerate(processed_plots, 1):
                artifacts[f"processed_candlestick_chart_{idx}"] = p

        # ── Step 4: Event-Driven Backtest Simulation ──────────────────
        logger.info(
            "[4/6] Executing bar-by-bar backtest simulation & dual-touch resolver..."
        )
        backtest_result: BacktestResult = engine.run(processed_df)
        logger.info(
            "Simulation completed: %d trades executed.", len(backtest_result.trades)
        )

        # ── Step 5: Performance & Risk Metrics ────────────────────────
        logger.info("[5/6] Computing quantitative performance and risk metrics...")
        metrics = generate_performance_report(
            trades_df=backtest_result.trades_df,
            equity_df=backtest_result.equity_curve,
            config=self.config,
        )

        # ── Step 6: Artifact Reporting & Persistence ──────────────────
        logger.info("[6/6] Exporting CSV/JSON artifacts...")
        exported = reporter.export_all(
            backtest_result=backtest_result,
            metrics=metrics,
        )
        artifacts.update(exported)

        # ── Visualizations (Portfolio Curves & Trades) ────────────────
        if generate_plots:
            logger.info("Generating portfolio performance curves...")
            perf_plots = perf_plotter.generate_all_plots(backtest_result, metrics)
            artifacts.update(perf_plots)

            logger.info("Generating session trade candlestick charts...")
            trade_plots = trade_plotter.plot_all_trades(
                trades=backtest_result.trades,
                processed_bars=processed_df,
                max_plots=20,
            )
            for idx, tp in enumerate(trade_plots, 1):
                artifacts[f"trade_chart_{idx}"] = tp

        # ── Console summary ───────────────────────────────────────────
        reporter.display_console_summary(metrics)

        elapsed = time.time() - t0
        logger.info("PIPELINE EXECUTION FINISHED in %.2f seconds.", elapsed)

        return PipelineRunResult(
            config=self.config,
            metrics=metrics,
            artifacts=artifacts,
            total_trades=len(backtest_result.trades),
            execution_time_seconds=elapsed,
        )
