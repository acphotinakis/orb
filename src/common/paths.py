"""
src.common.paths
================
Centralized path resolution and filesystem manager for the ORB system.

All path construction, directory creation, and filename formatting is owned
exclusively by :class:`PathManager`. No other module in the pipeline should
construct raw ``Path(...)`` strings from config values directly.

Design
------
* **Raw cache** is shared across runs —
  ``data/raw/{symbol}/{feed}/{timeframe}/``.
  Two runs on ``SPY_15Min`` reuse the same Parquet files without
  re-downloading.
* **Processed sessions** are immutable per dataset identity —
  ``data/processed/{symbol}/{timeframe}_{date_slug}_{identity}/``.
  Every content-affecting input (source fingerprint, feed, OR minutes,
  force-exit, ...) is part of the identity, so incompatible data can never
  share a directory and completed runs keep referencing byte-stable inputs.
* **Experiment artifacts** are fully isolated —
  ``experiments/{experiment_id}/``.
  Each run is self-contained and archivable as a single directory.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.common.config import AppConfig
from src.common.logger import get_logger

logger = get_logger(__name__)


class PathManager:
    """Authoritative manager for all ORB pipeline filesystem paths.

    Constructs and owns every file and directory path used throughout the
    pipeline. All modules receive pre-built :class:`~pathlib.Path` objects
    injected from this class — they never build paths from config strings
    themselves.

    Directory layout
    ----------------
    ::

        data/
          raw/{SYMBOL}/{FEED}/{TIMEFRAME}/
              {SYMBOL}_{TIMEFRAME}_{START}_{END}.parquet   ← shared across runs
          processed/{SYMBOL}/{TIMEFRAME}_{DATE_SLUG}/
              sessions.parquet                             ← shared per date-range
              daily_ranges.parquet

        experiments/
          {RUN_ID}__{SYMBOL}_{TIMEFRAME}_{DATE_SLUG}/      ← isolated per run
              config_snapshot.yaml
              logs/execution.log
              results/
                  trades.csv
                  equity_curve.csv
                  daily_summary.csv
                  metrics.json
              plots/
                  equity_curves/equity_curve.png
                  drawdowns/drawdown_curve.png
                  distributions/r_multiples.png
                  candlesticks/{SYMBOL}_candlestick_{SESSION}.png
                  trades/trade_{ID}_{DATE}_{DIR}.png

    Args:
        config: Loaded and validated :class:`~src.common.config.AppConfig`
            instance.
        run_id: Experiment run identifier (e.g. ``"baseline_v1"``).
            Combined with symbol, timeframe, and date slug to form
            :attr:`experiment_id`.
        start_date: Backtest window start. Used to build the date slug that
            scopes the processed data directory and experiment directory.
        end_date: Backtest window end.
        base_dir: Repository root override. Defaults to the current working
            directory resolved to an absolute path.
    """

    def __init__(
        self,
        config: AppConfig,
        run_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        base_dir: str | Path | None = None,
    ) -> None:
        self._config = config
        self.symbol = config.strategy.ticker.upper()
        self.timeframe = config.data.timeframe
        self.feed = config.data.feed
        self.root_dir = Path(base_dir or ".").resolve()

        # Date-range slug used in directory names, e.g. "20240102_20261231"
        s_str = start_date.strftime("%Y%m%d") if start_date else "start"
        e_str = end_date.strftime("%Y%m%d") if end_date else "end"
        self.date_slug = f"{s_str}_{e_str}"

        # Experiment identifier encodes every distinguishing parameter
        base_run = run_id or "run"
        self.experiment_id = (
            f"{base_run}__{self.symbol}_{self.timeframe}_{self.date_slug}"
        )

        logger.debug("PathManager initialised: experiment_id=%s", self.experiment_id)

    # ------------------------------------------------------------------
    # Shared data directories (reused across multiple runs)
    # ------------------------------------------------------------------

    @property
    def raw_data_dir(self) -> Path:
        """Shared raw Parquet cache directory.

        Path: ``{root}/data/raw/{symbol}/{feed}/{timeframe}/``

        Segregated by feed (``iex`` vs ``sip``) so that delayed and real-time
        data never share the same cache files.
        """
        p = self.root_dir / "data" / "raw" / self.symbol / self.feed / self.timeframe
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def processed_data_dir(self) -> Path:
        """Legacy processed directory, scoped to date range only.

        Path: ``{root}/data/processed/{symbol}/{timeframe}_{date_slug}/``

        .. deprecated::
            Lacks content identity (P1-O3): prefer
            :meth:`processed_dir_for_identity`, which keys the directory by
            every content-affecting input.  Retained for backward
            compatibility only.
        """
        p = (
            self.root_dir
            / "data"
            / "processed"
            / self.symbol
            / f"{self.timeframe}_{self.date_slug}"
        )
        p.mkdir(parents=True, exist_ok=True)
        return p

    def processed_dir_for_identity(self, identity_hash: str) -> Path:
        """Immutable processed dataset directory for one dataset identity.

        Path: ``{root}/data/processed/{symbol}/{timeframe}_{date_slug}_{hash}/``

        Args:
            identity_hash: Short hash from
                :func:`src.services.artifact_store.identity_short_hash`
                (validated as a safe path component).
        """
        from src.services.artifact_store import ensure_safe_component

        ensure_safe_component(identity_hash, field_name="identity_hash")
        p = (
            self.root_dir
            / "data"
            / "processed"
            / self.symbol
            / f"{self.timeframe}_{self.date_slug}_{identity_hash}"
        )
        p.mkdir(parents=True, exist_ok=True)
        return p

    def processed_file_for_identity(self, identity_hash: str) -> Path:
        """Canonical ``sessions.parquet`` inside :meth:`processed_dir_for_identity`."""
        return self.processed_dir_for_identity(identity_hash) / "sessions.parquet"

    # ------------------------------------------------------------------
    # Experiment root and run-specific subdirectories
    # ------------------------------------------------------------------

    @property
    def experiment_dir(self) -> Path:
        """Isolated experiment root directory.

        Path: ``{root}/experiments/{experiment_id}/``
        """
        p = self.root_dir / "experiments" / self.experiment_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def results_dir(self) -> Path:
        """CSV / JSON artifact directory.

        Path: ``{experiment_dir}/results/``
        """
        p = self.experiment_dir / "results"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        """Execution log directory.

        Path: ``{experiment_dir}/logs/``
        """
        p = self.experiment_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def plots_dir(self) -> Path:
        """Plot output root directory.

        Path: ``{experiment_dir}/plots/``
        """
        p = self.experiment_dir / "plots"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def trade_plots_dir(self) -> Path:
        """Per-trade candlestick chart directory.

        Path: ``{plots_dir}/trades/``
        """
        p = self.plots_dir / "trades"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def candlestick_plots_dir(self) -> Path:
        """Session candlestick (data-only) chart directory.

        Path: ``{plots_dir}/candlesticks/``
        """
        p = self.plots_dir / "candlesticks"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def raw_candlestick_plots_dir(self) -> Path:
        """Raw data candlestick chart directory.

        Path: ``{plots_dir}/candlesticks/raw/``
        """
        p = self.candlestick_plots_dir / "raw"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cleaned_candlestick_plots_dir(self) -> Path:
        """Cleaned data candlestick chart directory.

        Path: ``{plots_dir}/candlesticks/cleaned/``
        """
        p = self.candlestick_plots_dir / "cleaned"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def processed_candlestick_plots_dir(self) -> Path:
        """Processed session candlestick chart directory.

        Path: ``{plots_dir}/candlesticks/processed/``
        """
        p = self.candlestick_plots_dir / "processed"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def equity_curves_dir(self) -> Path:
        """Equity curve plot directory.

        Path: ``{plots_dir}/equity_curves/``
        """
        p = self.plots_dir / "equity_curves"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def drawdowns_dir(self) -> Path:
        """Drawdown plot directory.

        Path: ``{plots_dir}/drawdowns/``
        """
        p = self.plots_dir / "drawdowns"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def distributions_dir(self) -> Path:
        """Statistical distribution plot directory.

        Path: ``{plots_dir}/distributions/``
        """
        p = self.plots_dir / "distributions"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------
    # Canonical file paths
    # ------------------------------------------------------------------

    def raw_cache_file(self, start: datetime, end: datetime) -> Path:
        """Return the deterministic Parquet path for a fetched date window.

        Args:
            start: Actual start of data in the file.
            end: Actual end of data in the file.

        Returns:
            Full path to the Parquet file within :attr:`raw_data_dir`.
        """
        fname = (
            f"{self.symbol}_{self.timeframe}"
            f"_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.parquet"
        )
        return self.raw_data_dir / fname

    def raw_cache_glob_pattern(self) -> str:
        """Return the glob pattern to find all cached files for this symbol/timeframe.

        Returns:
            Glob pattern string, e.g. ``"SPY_15Min_*.parquet"``.
        """
        return f"{self.symbol}_{self.timeframe}_*.parquet"

    @property
    def processed_file(self) -> Path:
        """Canonical processed sessions Parquet path.

        Path: ``{processed_data_dir}/sessions.parquet``
        """
        return self.processed_data_dir / "sessions.parquet"

    @property
    def daily_ranges_file(self) -> Path:
        """Daily opening range calculations Parquet path.

        Path: ``{processed_data_dir}/daily_ranges.parquet``
        """
        return self.processed_data_dir / "daily_ranges.parquet"

    @property
    def trades_csv(self) -> Path:
        """Trade log CSV path: ``{results_dir}/trades.csv``"""
        return self.results_dir / "trades.csv"

    @property
    def equity_curve_csv(self) -> Path:
        """Equity curve CSV path: ``{results_dir}/equity_curve.csv``"""
        return self.results_dir / "equity_curve.csv"

    @property
    def daily_summary_csv(self) -> Path:
        """Daily P&L summary CSV path: ``{results_dir}/daily_summary.csv``"""
        return self.results_dir / "daily_summary.csv"

    @property
    def metrics_json(self) -> Path:
        """Performance metrics JSON path: ``{results_dir}/metrics.json``"""
        return self.results_dir / "metrics.json"

    @property
    def config_snapshot_yaml(self) -> Path:
        """Frozen config snapshot path: ``{experiment_dir}/config_snapshot.yaml``"""
        return self.experiment_dir / "config_snapshot.yaml"

    @property
    def execution_log(self) -> Path:
        """Per-experiment execution log: ``{logs_dir}/execution.log``"""
        return self.logs_dir / "execution.log"

    def candlestick_file(self, session_id: str) -> Path:
        """Return the path for a session candlestick chart PNG.

        Args:
            session_id: Session identifier, typically ``"YYYY-MM-DD"``.

        Returns:
            Full path within :attr:`candlestick_plots_dir`.
        """
        return (
            self.candlestick_plots_dir / f"{self.symbol}_candlestick_{session_id}.png"
        )

    def trade_chart_file(self, trade_id: int, date: str, direction: str) -> Path:
        """Return the path for a per-trade execution chart PNG.

        Args:
            trade_id: Integer trade identifier.
            date: Session date string (``"YYYY-MM-DD"``).
            direction: Trade direction (``"LONG"`` or ``"SHORT"``).

        Returns:
            Full path within :attr:`trade_plots_dir`.
        """
        return self.trade_plots_dir / f"trade_{trade_id:03d}_{date}_{direction}.png"

    def equity_curve_file(self, filename: str = "equity_curve.png") -> Path:
        """Return the equity curve plot path within :attr:`equity_curves_dir`."""
        return self.equity_curves_dir / filename

    def drawdown_file(self, filename: str = "drawdown_curve.png") -> Path:
        """Return the drawdown plot path within :attr:`drawdowns_dir`."""
        return self.drawdowns_dir / filename

    def distribution_file(self, filename: str) -> Path:
        """Return a named distribution plot path within :attr:`distributions_dir`."""
        return self.distributions_dir / filename

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of the key managed paths.

        Returns:
            Multiline string suitable for logging at INFO level.
        """
        lines = [
            f"PathManager — experiment: {self.experiment_id}",
            f"  root_dir          : {self.root_dir}",
            f"  raw_data_dir      : {self.raw_data_dir}",
            f"  processed_data_dir: {self.processed_data_dir}",
            f"  experiment_dir    : {self.experiment_dir}",
            f"  results_dir       : {self.results_dir}",
            f"  plots_dir         : {self.plots_dir}",
        ]
        return "\n".join(lines)
