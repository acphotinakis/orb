# SFIX-001: Implement Centralized `PathManager` Class

## Objective

Create `src/common/paths.py` with a single authoritative `PathManager` class that owns **all** path construction, directory creation, and filename generation for the entire ORB pipeline. Remove all scattered `Path(...)` string assembly from `fetcher.py`, `processor.py`, `reporter.py`, `pipeline.py`, and all visualization modules.

## Problem

Path logic is currently spread across 8+ files:

| File | Hardcoded Path Logic |
|:-----|:---------------------|
| `src/common/config.py` | `data/raw/SPY`, `data/processed/SPY` in dataclass defaults |
| `src/data/fetcher.py` | `_CACHE_FILENAME_TEMPLATE`, broken glob `{symbol}_1min_*.parquet` |
| `src/data/processor.py` | `_OUTPUT_FILENAME = "sessions.parquet"` constant |
| `src/evaluation/reporter.py` | `run_dir = self.base_dir / run_id`, hardcoded CSV names |
| `src/pipeline.py` | Inline `f"{config.output.plots_dir}/candlesticks/{ticker}"` |
| `src/visualization/performance_plotter.py` | `"plots/equity_curves"`, `"plots/drawdowns"`, `"plots/distributions"` |
| `src/strategy/opening_range.py` | `_OR_OUTPUT_FILENAME = "daily_ranges.parquet"` |

This causes:
- **Run collisions** when running multiple symbols/timeframes simultaneously
- **Impossible to change layout** without editing 8+ files
- **No single source of truth** for output locations
- **Symbol contamination** — `data/raw/SPY/` breaks for AAPL without code changes

## Scope

### In Scope
- Create `src/common/paths.py` with `PathManager` class
- `PathManager` receives `AppConfig`, `run_id`, `start_date`, `end_date` at construction
- `PathManager` owns all `mkdir()` calls
- All path access is via typed `@property` attributes or methods
- Export `PathManager` from `src/common/__init__.py`

### Out of Scope
- Changing config dataclass fields (SFIX-002)
- Updating downstream modules to consume `PathManager` (SFIX-004)

## New File: `src/common/paths.py`

### Key design

```
data/
  raw/{SYMBOL}/{FEED}/{TIMEFRAME}/
      {SYMBOL}_{TIMEFRAME}_{START}_{END}.parquet      ← shared across runs
  processed/{SYMBOL}/{TIMEFRAME}_{DATE_SLUG}/
      sessions.parquet                                ← shared per date-range
      daily_ranges.parquet

experiments/
  {RUN_ID}__{SYMBOL}_{TIMEFRAME}_{DATE_SLUG}/         ← fully isolated run
      config_snapshot.yaml
      logs/execution.log
      results/trades.csv, equity_curve.csv, daily_summary.csv, metrics.json
      plots/
          equity_curves/equity_curve.png
          drawdowns/drawdown_curve.png
          distributions/r_multiples.png, trade_durations.png
          candlesticks/{SYMBOL}_candlestick_{SESSION}.png
          trades/trade_{ID}_{DATE}_{DIR}.png
```

### Class skeleton

```python
class PathManager:
    def __init__(self, config, run_id=None, start_date=None, end_date=None, base_dir=None):
        self.symbol    = config.strategy.ticker.upper()
        self.timeframe = config.data.timeframe
        self.feed      = config.data.feed
        self.root_dir  = Path(base_dir or ".").resolve()
        s_str = start_date.strftime("%Y%m%d") if start_date else "start"
        e_str = end_date.strftime("%Y%m%d") if end_date else "end"
        self.date_slug     = f"{s_str}_{e_str}"
        self.experiment_id = f"{run_id or 'run'}__{self.symbol}_{self.timeframe}_{self.date_slug}"

    # Shared directories
    @property def raw_data_dir(self) -> Path: ...        # data/raw/{symbol}/{feed}/{timeframe}/
    @property def processed_data_dir(self) -> Path: ... # data/processed/{symbol}/{timeframe}_{date_slug}/

    # Experiment directories
    @property def experiment_dir(self) -> Path: ...      # experiments/{experiment_id}/
    @property def results_dir(self) -> Path: ...
    @property def logs_dir(self) -> Path: ...
    @property def plots_dir(self) -> Path: ...
    @property def trade_plots_dir(self) -> Path: ...
    @property def candlestick_plots_dir(self) -> Path: ...
    @property def equity_curves_dir(self) -> Path: ...
    @property def drawdowns_dir(self) -> Path: ...
    @property def distributions_dir(self) -> Path: ...

    # Canonical file paths
    def raw_cache_file(self, start, end) -> Path: ...
    def raw_cache_glob_pattern(self) -> str: ...
    @property def processed_file(self) -> Path: ...
    @property def daily_ranges_file(self) -> Path: ...
    @property def trades_csv(self) -> Path: ...
    @property def equity_curve_csv(self) -> Path: ...
    @property def daily_summary_csv(self) -> Path: ...
    @property def metrics_json(self) -> Path: ...
    @property def config_snapshot_yaml(self) -> Path: ...
    @property def execution_log(self) -> Path: ...
    def candlestick_file(self, session_id: str) -> Path: ...
    def trade_chart_file(self, trade_id, date, direction) -> Path: ...
    def equity_curve_file(self, filename="equity_curve.png") -> Path: ...
    def drawdown_file(self, filename="drawdown_curve.png") -> Path: ...
    def distribution_file(self, filename) -> Path: ...
    def summary(self) -> str: ...
```

## Affected Files

| File | Change Required |
|:-----|:----------------|
| `src/common/paths.py` | **CREATE** — new file |
| `src/common/__init__.py` | Add `PathManager` to exports |

## Acceptance Criteria

- [ ] `PathManager` instantiates without error given a valid `AppConfig`
- [ ] All `@property` directories are auto-created via `mkdir(parents=True, exist_ok=True)`
- [ ] `PathManager.summary()` logs a readable path tree
- [ ] `raw_cache_glob_pattern()` matches filenames produced by `raw_cache_file()`
- [ ] `experiment_id` encodes symbol, timeframe, date slug, and run_id
- [ ] Two `PathManager` instances with different symbols produce non-overlapping paths

## Dependencies

- SFIX-002 (config changes) should be completed first so `DataConfig` has `is_paper` and no path fields
