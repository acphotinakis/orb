# SFIX-004: Integrate `PathManager` Into All Pipeline Modules

## Objective

Update every module that currently constructs paths independently to consume `PathManager` injected from `pipeline.py`. Remove all hardcoded `Path(...)` string assembly from these files.

## Problem

After SFIX-001 creates `PathManager`, the following 8 modules still contain their own path logic:

| Module | Current Broken Path Logic |
|:-------|:--------------------------|
| `src/pipeline.py` | Inline `f"{config.output.plots_dir}/candlesticks/{ticker}"`, `sys.exit(0)` debug left in |
| `src/data/fetcher.py` | `self._cache_dir = Path(config.data.raw_dir)`, broken glob `{symbol}_1min_*.parquet` |
| `src/data/processor.py` | `self._processed_dir = Path(config.data.processed_dir)`, `_OUTPUT_FILENAME` constant |
| `src/evaluation/reporter.py` | `run_dir = self.base_dir / run_id`, manual CSV name strings |
| `src/visualization/performance_plotter.py` | `self.equity_dir = self.base_dir / "equity_curves"` etc. |
| `src/visualization/candlestick_plotter.py` | `output_dir="plots/trades"` default |
| `src/visualization/candlestick_data_plotter.py` | `output_dir="plots/candlesticks"` default |
| `src/strategy/opening_range.py` | `_OR_OUTPUT_FILENAME = "daily_ranges.parquet"` constant |

## Implementation Details

### `src/pipeline.py` — build `PathManager` in `run()`

`ORBPipeline.__init__` should not construct `PathManager` because dates and `run_id` are not known until `run()` is called.

```python
class ORBPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        # No path construction here — dates unknown until run()

    def run(self, start_date, end_date, run_id, generate_plots, refresh_cache, log_level="INFO"):
        # Resolve dates
        s_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if start_date else None
        e_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if end_date else None

        # Build PathManager — single source of truth for all paths
        paths = PathManager(config=self.config, run_id=run_id, start_date=s_dt, end_date=e_dt)
        logger.info(paths.summary())

        # Redirect logging to per-experiment log file
        setup_logging(level=log_level, log_file=paths.execution_log)

        # Save config snapshot before any computation
        self.config.to_yaml(paths.config_snapshot_yaml)
        logger.info("Config snapshot: %s", paths.config_snapshot_yaml)

        # Inject paths into all services
        fetcher   = DataFetcher(config=self.config, cache_dir=paths.raw_data_dir)
        processor = DataProcessor(config=self.config, output_dir=paths.processed_data_dir)
        reporter  = ResultsReporter(output_dir=paths.results_dir)
        data_plotter  = CandlestickDataPlotter(output_dir=paths.candlestick_plots_dir)
        trade_plotter = CandlestickTradePlotter(output_dir=paths.trade_plots_dir)
        perf_plotter  = PerformancePlotter(
            equity_dir=paths.equity_curves_dir,
            drawdown_dir=paths.drawdowns_dir,
            distributions_dir=paths.distributions_dir,
        )
        ...
```

### `src/data/fetcher.py`

```python
class DataFetcher:
    def __init__(
        self,
        config: AppConfig,
        client: Optional[AlpacaDataClient] = None,
        cache_dir: Optional[Path] = None,   # Injected from PathManager
    ) -> None:
        self._config = config
        self._client = client
        self._cache_dir = cache_dir or Path("data/raw") / config.data.symbol

    def fetch_and_cache(self, ...):
        ...
        # FIX: Use configured timeframe, not hardcoded "1min"
        pattern = f"{symbol}_{self._config.data.timeframe}_*.parquet"
        existing_files = sorted(cache_dir.glob(pattern))
        ...
```

### `src/data/processor.py`

```python
class DataProcessor:
    def __init__(
        self,
        config: AppConfig,
        output_dir: Optional[Path] = None,   # Injected from PathManager
    ) -> None:
        self._config = config
        self._processed_dir = output_dir or Path("data/processed") / config.data.symbol
        self._or_minutes = config.strategy.opening_range_minutes
        self._force_exit_time = config.strategy.force_exit_time

    def process(self, raw_df, save_to_disk=True, output_path=None, force_refresh=False):
        # Check cache before re-processing
        out = output_path or (self._processed_dir / "sessions.parquet")
        if not force_refresh and out.exists():
            logger.info("Processed session cache hit — loading %s", out)
            return pd.read_parquet(out, engine="pyarrow")
        ...
```

### `src/evaluation/reporter.py`

```python
class ResultsReporter:
    def __init__(self, output_dir: Path) -> None:
        # Path is fully pre-resolved by PathManager — no run_id joining
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_all(self, backtest_result, metrics) -> Dict[str, Path]:
        artifact_paths: Dict[str, Path] = {}
        trades_path = self.output_dir / "trades.csv"
        equity_path = self.output_dir / "equity_curve.csv"
        daily_path  = self.output_dir / "daily_summary.csv"
        metrics_path = self.output_dir / "metrics.json"
        ...
```

### `src/visualization/performance_plotter.py`

```python
class PerformancePlotter:
    def __init__(
        self,
        equity_dir: Path,
        drawdown_dir: Path,
        distributions_dir: Path,
    ) -> None:
        # Accept pre-resolved paths — no internal mkdir for subdirs
        self.equity_dir = equity_dir
        self.dd_dir = drawdown_dir
        self.dist_dir = distributions_dir
```

### `src/strategy/opening_range.py`

Remove `_OR_OUTPUT_FILENAME = "daily_ranges.parquet"` constant.
Accept output path as a parameter injected from `PathManager.daily_ranges_file`.

## Affected Files

| File | Type of Change |
|:-----|:---------------|
| `src/pipeline.py` | Build `PathManager` in `run()`, inject into all services, remove `sys.exit(0)`, uncomment candlestick plotting |
| `src/data/fetcher.py` | Accept `cache_dir: Optional[Path]`, fix both `"1min"` glob patterns |
| `src/data/processor.py` | Accept `output_dir: Optional[Path]`, add cache-hit early return |
| `src/evaluation/reporter.py` | Accept `output_dir: Path` directly, remove `run_id` joining |
| `src/visualization/performance_plotter.py` | Accept individual dir paths, remove internal `mkdir` for subdirs |
| `src/visualization/candlestick_plotter.py` | Accept `output_dir: Path` (no default string) |
| `src/visualization/candlestick_data_plotter.py` | Accept `output_dir: Path` (no default string) |
| `src/strategy/opening_range.py` | Remove `_OR_OUTPUT_FILENAME` constant, accept path parameter |

## Acceptance Criteria

- [ ] No `Path(config.data.raw_dir)`, `Path(config.data.processed_dir)`, or `Path(config.output.plots_dir)` call remains outside `PathManager`
- [ ] No `"{symbol}_1min_*.parquet"` hardcoded string remains anywhere
- [ ] `pipeline.py` constructs `PathManager` once in `run()` and injects paths into all services
- [ ] `config_snapshot.yaml` is written to `paths.config_snapshot_yaml` before any computation
- [ ] Per-experiment log file written to `paths.execution_log`
- [ ] Full pipeline produces artifacts in the correct experiment directory tree
- [ ] `sys.exit(0)` debug call removed from `pipeline.py`

## Dependencies

- SFIX-001 (PathManager must exist)
- SFIX-002 (DataConfig no longer has `raw_dir`, `processed_dir`, `plots_dir`)
- SFIX-003 (Directory structure design reference)
