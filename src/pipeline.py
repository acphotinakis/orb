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
from src.backtest.trace import TraceCollector
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
        run_id: Optional[str] = None,
        log_level: str = "INFO",
        base_dir: Optional[Union[str, Path]] = None,
        run_label: Optional[str] = None,
        on_event: Optional[callable] = None,
        cancel_requested: Optional[callable] = None,
        record_trace: bool = False,
    ) -> PipelineRunResult:
        """Execute the full pipeline workflow from data ingestion to reporting.

        Args:
            start_date: Backtest start date (``"YYYY-MM-DD"``).  ``None``
                uses the earliest available cached data.
            end_date: Backtest end date (``"YYYY-MM-DD"``).  ``None``
                uses the most recent available data.
            refresh_cache: When ``True``, bypass the raw Parquet cache and
                re-fetch from Alpaca; the refresh propagates to processed
                data (P1-O3).
            generate_plots: When ``True``, produce all chart outputs.
            run_id: Experiment identifier.  ``None`` (default) mints a
                server-side UUID so reruns can never overwrite each other;
                explicit values must be filesystem-safe single components.
            log_level: Logging verbosity for this run.
            base_dir: Base directory override for filesystem root.
            run_label: User-facing tag recorded in the manifest; never a
                filesystem identity.
            on_event: Optional ``event -> None`` callback for stage and
                session progress (``run_started``, ``stage_started``,
                ``session_started``, ``session_completed``, ``run_completed``,
                ``run_failed``).  ``None`` preserves plain CLI use.
            cancel_requested: Optional ``() -> bool`` polled between stages
                and sessions (and every 64 bars).  When true at a boundary,
                the run stops before further side effects and raises
                :class:`CancelledRun`.
            record_trace: When ``True``, capture versioned decision events
                from the engine into ``results/decision_trace.jsonl`` (P4;
                explicit opt-in, bounded).

        Completion contract (P1-O3): artifacts are published atomically and
        the validated ``manifest.json`` is written last.  Only a present,
        valid, ``succeeded`` manifest marks a run complete
        (:func:`src.services.artifact_store.is_run_complete`).

        Returns:
            :class:`PipelineRunResult` with metrics, artifact paths, and
            timing information.
        """
        from uuid import uuid4

        from src.services.artifact_store import (
            build_manifest,
            dataset_identity,
            ensure_safe_component,
            fingerprint_dataframe,
            identity_short_hash,
            publish_manifest_last,
            sha256_file,
            source_code_fingerprint,
        )

        t0 = time.time()
        artifacts: Dict[str, Path] = {}
        created_at = datetime.now(timezone.utc).isoformat()

        if run_id is None:
            run_id = uuid4().hex
        else:
            ensure_safe_component(run_id, field_name="run_id")

        from src.common.exceptions import CancelledRun

        def _emit(event_type: str, **fields: Any) -> None:
            if on_event is None:
                return
            try:
                on_event(
                    {
                        "schema_version": 1,
                        "run_id": run_id,
                        "event_type": event_type,
                        "emitted_at": datetime.now(timezone.utc).isoformat(),
                        **fields,
                    }
                )
            except Exception as exc:
                logger.warning("Progress callback failed (%s); continuing.", exc)

        def _check_cancel(stage: str) -> None:
            try:
                requested = bool(cancel_requested and cancel_requested())
            except Exception as exc:
                logger.warning("Cancel hook failed (%s); continuing.", exc)
                requested = False
            if requested:
                logger.info("Cancellation acknowledged before stage '%s'.", stage)
                raise CancelledRun(
                    f"Run cancelled before stage '{stage}'.", stage=stage
                )

        _emit("run_started", run_label=run_label)

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
        _check_cancel("fetch")
        _emit("stage_started", stage="fetch")
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
            raw_plotter = CandlestickDataPlotter(
                output_dir=paths.raw_candlestick_plots_dir
            )
            raw_plots = raw_plotter.plot_all_sessions(
                df=raw_df.tail(int(len(raw_df) * 0.2)),
                stage="raw",
                symbol=self.config.strategy.ticker,
                max_plots=20,
            )
            for idx, p in enumerate(raw_plots, 1):
                artifacts[f"raw_candlestick_chart_{idx}"] = p

        # ── Step 2: Data Validation & Cleaning ───────────────────────
        _check_cancel("validate")
        _emit("stage_started", stage="validate")
        logger.info("[2/6] Validating OHLCV bar integrity and checking for gaps...")
        cleaned_df, report = validate_and_clean_bars(
            raw_df, timeframe=self.config.data.timeframe, strict=False
        )
        logger.info("Validation complete: %s", report.summary())

        if generate_plots:
            cleaned_plotter = CandlestickDataPlotter(
                output_dir=paths.cleaned_candlestick_plots_dir
            )
            cleaned_plots = cleaned_plotter.plot_all_sessions(
                df=cleaned_df.tail(int(len(cleaned_df) * 0.2)),
                stage="cleaned",
                symbol=self.config.strategy.ticker,
                max_plots=20,
            )
            for idx, p in enumerate(cleaned_plots, 1):
                artifacts[f"cleaned_candlestick_chart_{idx}"] = p

        # ── Step 3: RTH Session Processing ────────────────────────────
        _check_cancel("process")
        _emit("stage_started", stage="process")
        # Identity (P1-O3): the processed dataset is keyed by every input
        # affecting its content.  Same identity → shared immutable directory;
        # changed inputs → a different directory, never a silent overwrite.
        logger.info(
            "[3/6] Normalizing timezone to ET, filtering RTH (09:30-16:00), "
            "and tagging session metadata..."
        )
        source_fingerprint = fingerprint_dataframe(cleaned_df)
        cleaned_min = pd.Timestamp(cleaned_df["timestamp"].min())
        cleaned_max = pd.Timestamp(cleaned_df["timestamp"].max())
        identity = dataset_identity(
            source_fingerprint,
            feed=self.config.data.feed,
            symbol=self.config.data.symbol,
            timeframe=self.config.data.timeframe,
            date_min=str(cleaned_min.date()),
            date_max=str(cleaned_max.date()),
            or_minutes=self.config.strategy.opening_range_minutes,
            force_exit_time=self.config.strategy.force_exit_time,
        )
        identity_hash = identity_short_hash(identity)
        processed_file = paths.processed_file_for_identity(identity_hash)
        logger.info("Dataset identity %s → %s", identity_hash, processed_file)
        processed_df = processor.process(
            cleaned_df,
            save_to_disk=True,
            output_path=processed_file,
            force_refresh=refresh_cache,
            identity=identity,
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
            processed_plotter = CandlestickDataPlotter(
                output_dir=paths.processed_candlestick_plots_dir
            )
            processed_plots = processed_plotter.plot_all_sessions(
                df=processed_df.tail(int(len(processed_df) * 0.2)),
                stage="processed",
                symbol=self.config.strategy.ticker,
                max_plots=20,
            )
            for idx, p in enumerate(processed_plots, 1):
                artifacts[f"processed_candlestick_chart_{idx}"] = p

        # ── Step 4: Event-Driven Backtest Simulation ──────────────────
        _check_cancel("simulate")
        _emit("stage_started", stage="simulate")
        logger.info(
            "[4/6] Executing bar-by-bar backtest simulation & dual-touch resolver..."
        )

        def _engine_event(event: dict) -> None:
            event = dict(event)
            event_type = event.pop("event_type", "session_progress")
            _emit(event_type, stage="backtest", **event)

        collector = TraceCollector() if record_trace else None
        backtest_result: BacktestResult = engine.run(
            processed_df, progress=_engine_event, cancel_requested=cancel_requested,
            trace=collector,
        )
        if backtest_result.cancelled:
            raise CancelledRun("Run cancelled during simulation.", stage="simulate")
        logger.info(
            "Simulation completed: %d trades executed.", len(backtest_result.trades)
        )

        # ── Step 5: Performance & Risk Metrics ────────────────────────
        _check_cancel("report")
        _emit("stage_started", stage="report")
        logger.info("[5/6] Computing quantitative performance and risk metrics...")
        metrics = generate_performance_report(
            trades_df=backtest_result.trades_df,
            equity_df=backtest_result.equity_curve,
            config=self.config,
        )

        # ── Step 6: Artifact Reporting & Persistence ──────────────────
        _check_cancel("export")
        _emit("stage_started", stage="export")
        logger.info("[6/6] Exporting CSV/JSON artifacts...")
        exported = reporter.export_all(
            backtest_result=backtest_result,
            metrics=metrics,
        )
        artifacts.update(exported)

        # ── Decision trace (P4, opt-in) ─────────────────────────────────
        # Captured from the executing engine above; written before the
        # manifest so it is checksummed like every other artifact.
        if collector is not None:
            from src.services.artifact_store import atomic_write_text

            trace_path = paths.results_dir / "decision_trace.jsonl"
            atomic_write_text(
                trace_path, collector.to_jsonl(run_id=run_id)
            )
            artifacts["decision_trace"] = trace_path
            logger.info(
                "Decision trace recorded: %d events%s.",
                len(collector.events),
                " (TRUNCATED)" if collector.truncated else "",
            )

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

        # ── Manifest-last publication (P1-O3) ─────────────────────────
        # Every artifact is checksummed; the validated manifest is written
        # last and is the sole completion marker.  Failure anywhere above
        # leaves no manifest, so incomplete runs can never look complete.
        logger.info("Verifying artifacts and publishing manifest...")
        experiment_dir = paths.experiment_dir
        artifact_entries: Dict[str, Dict[str, Any]] = {}
        for sub in ("results", "plots"):
            subdir = experiment_dir / sub
            if subdir.is_dir():
                for file_path in sorted(subdir.rglob("*")):
                    if file_path.is_file():
                        rel = file_path.relative_to(experiment_dir).as_posix()
                        artifact_entries[rel] = {
                            "relative_path": rel,
                            "sha256": sha256_file(file_path),
                            "bytes": file_path.stat().st_size,
                        }
        for single in (paths.config_snapshot_yaml,):
            if single.is_file():
                rel = single.relative_to(experiment_dir).as_posix()
                artifact_entries[rel] = {
                    "relative_path": rel,
                    "sha256": sha256_file(single),
                    "bytes": single.stat().st_size,
                }

        processed_fingerprint = fingerprint_dataframe(processed_df)
        net_pnl = float(backtest_result.trades_df["pnl_dollars"].sum()) if (
            not backtest_result.trades_df.empty
            and "pnl_dollars" in backtest_result.trades_df.columns
        ) else 0.0
        manifest = build_manifest(
            run_id=run_id,
            run_label=run_label,
            config_dict=self.config.to_dict(),
            request_dict={
                "start_date": start_date,
                "end_date": end_date,
                "refresh_cache": refresh_cache,
                "generate_plots": generate_plots,
                "log_level": log_level,
            },
            source_dict=source_code_fingerprint(),
            datasets_dict={
                "raw": {
                    "fingerprint": source_fingerprint,
                    "feed": self.config.data.feed,
                    "symbol": self.config.data.symbol,
                    "timeframe": self.config.data.timeframe,
                    "rows": len(cleaned_df),
                    "date_min": str(cleaned_min.date()),
                    "date_max": str(cleaned_max.date()),
                },
                "processed": {
                    "identity": identity,
                    "identity_hash": identity_hash,
                    "path": processed_file.relative_to(paths.root_dir).as_posix()
                    if processed_file.is_relative_to(paths.root_dir)
                    else str(processed_file),
                    "fingerprint": processed_fingerprint,
                    "rows": len(processed_df),
                    "sessions": int(processed_df["session_id"].nunique()),
                },
            },
            artifacts_dict=artifact_entries,
            accounting_dict={
                "initial_capital": self.config.execution.initial_capital,
                "final_capital": float(backtest_result.final_capital),
                "sum_net_trade_pnl": net_pnl,
                "reconcile_gap": float(
                    metrics["portfolio_metrics"]["ending_equity"]
                    - self.config.execution.initial_capital
                    - metrics["trade_metrics"]["total_pnl_dollars"]
                ),
            },
            created_at=created_at,
        )
        manifest_path = publish_manifest_last(experiment_dir, manifest)
        artifacts["manifest_json"] = manifest_path
        logger.info("Manifest published: %s", manifest_path)
        _emit(
            "run_completed",
            total_trades=len(backtest_result.trades),
            elapsed_seconds=round(time.time() - t0, 2),
        )

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
