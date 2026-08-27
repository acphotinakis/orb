# SFIX-005: Additional Storage & Caching Optimizations

## Objective

Apply targeted bug fixes and optimizations to raw data caching, processed session storage, Parquet compression, and artifact quality that reduce disk usage, prevent data corruption, and improve pipeline speed beyond the structural changes in SFIX-001 through SFIX-004.

## Issues Identified

### Bug 1 — Broken Glob Pattern in `fetcher.py` (Critical)

**Location:** `src/data/fetcher.py` lines 420 and 476

```python
# Line 420 — cache merge step (BROKEN)
existing_files = sorted(cache_dir.glob(f"{symbol}_1min_*.parquet"))

# Line 476 — load_cached() (BROKEN)
files = sorted(self._cache_dir.glob(f"{symbol}_1min_*.parquet"))
```

For any timeframe other than `1Min`, **no existing cache files are matched**, so:
- Old partial cache files are never merged → data gaps accumulate
- Old stale files are never cleaned up → disk space wasted
- `load_cached()` always returns an empty DataFrame for non-1Min data

**Fix:** Replace hardcoded `"1min"` with the configured timeframe:
```python
pattern = f"{symbol}_{self._config.data.timeframe}_*.parquet"
existing_files = sorted(cache_dir.glob(pattern))
```

---

### Bug 2 — Processed Session Not Checked Before Re-Processing

**Location:** `src/data/processor.py`

`DataProcessor.process()` always re-runs the full RTH filter + session tagging + pruning pipeline even when `sessions.parquet` already exists on disk from a previous run with identical parameters. For 2+ year date ranges this wastes several seconds on every pipeline invocation.

**Fix:** Add an early-return cache check at the top of `process()`:
```python
def process(self, raw_df, save_to_disk=True, output_path=None, force_refresh=False):
    out = output_path or (self._processed_dir / "sessions.parquet")
    if not force_refresh and out.exists():
        logger.info("Processed session cache hit — loading from %s", out)
        return pd.read_parquet(out, engine="pyarrow")
    # ...continue with full processing
```

---

### Optimization 1 — Parquet Compression: Snappy → Zstandard

**Location:** `src/data/fetcher.py` `_write_parquet()`

`df.to_parquet()` without a `compression` argument defaults to `snappy`. For financial OHLCV data (float64 columns with high temporal correlation), **Zstandard (zstd) at level 3** achieves 25–40% better compression ratios than snappy with equal or faster decompression speeds.

**Fix:**
```python
df.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
```

Apply consistently to both `fetcher.py` and `processor.py` Parquet writes.

---

### Optimization 2 — CSV Price Column Precision Rounding

**Location:** `src/evaluation/reporter.py` `export_all()`

`trades_df.to_csv()` writes price columns at full float64 precision (15+ decimal digits). Rounding price/dollar columns to 4 decimal places (sub-cent precision) reduces CSV file size by ~30% with no meaningful loss.

**Fix:**
```python
price_cols = [
    c for c in trades_df.columns
    if any(k in c.lower() for k in ["price", "pnl", "capital", "equity", "r_multiple"])
]
trades_df = trades_df.copy()
trades_df[price_cols] = trades_df[price_cols].round(4)
trades_df.to_csv(trades_path, index=False)
```

---

### Optimization 3 — Deduplication Guard on Processed Sessions

**Location:** `src/data/processor.py`

If `process()` is called twice with overlapping raw DataFrames (e.g., a re-fetch that partially overlaps cached data), sessions can be duplicated in `sessions.parquet`. There is no deduplication step on the processed output.

**Fix:** Add an explicit deduplication step before the final write:
```python
# Deduplicate on (session_id, timestamp) before persisting
processed_df = processed_df.drop_duplicates(
    subset=["session_id", "timestamp"]
).reset_index(drop=True)
```

---

### Optimization 4 — Parquet Schema Validation on Load

**Location:** `src/data/fetcher.py` `_load_parquet()`, `src/data/processor.py`

When loading cached Parquet files there is no validation that the schema (column names + dtypes) matches the expected output schema. A schema change between code versions can silently produce corrupt downstream data.

**Fix:** After `pd.read_parquet()`, validate required columns exist:
```python
def _load_parquet(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path, engine="pyarrow")
    missing = [c for c in _OHLCV_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise DataFetchError(
            f"Cached file '{path.name}' is missing columns: {missing}. "
            "Delete the cache file and re-fetch."
        )
    return df
```

---

### Optimization 5 — Per-Experiment Log File

**Location:** `src/pipeline.py`

`setup_logging()` currently logs only to stdout. Terminal output from one run is lost when the next run starts. There is no persistent record of what happened during a specific experiment.

**Fix:** After `PathManager` is constructed in `run()`, redirect logging to a file:
```python
setup_logging(level=log_level, log_file=paths.execution_log)
logger.info("Logging to experiment file: %s", paths.execution_log)
```

This writes a complete execution log to `experiments/{experiment_id}/logs/execution.log`.

---

### Optimization 6 — `daily_ranges.parquet` Moved to Processed Dir

**Location:** `src/strategy/opening_range.py`

`daily_ranges.parquet` is currently written to an arbitrary `output_dir` parameter and is not co-located with `sessions.parquet`. After SFIX-001, both should live in `PathManager.processed_data_dir` so they share the same date-range scope and are reusable together.

**Fix:** Use `PathManager.daily_ranges_file` as the output path:
```python
# In pipeline.py after processing:
calc.save_daily_ranges(ranges, output_path=paths.daily_ranges_file)
```

## Implementation Priority

| # | Issue | File | Severity | Effort |
|:--|:------|:-----|:---------|:-------|
| 1 | Fix `"1min"` glob patterns | `fetcher.py` (×2) | **Critical Bug** | XS |
| 2 | Processed session cache-hit early return | `processor.py` | Bug + Perf | S |
| 3 | `zstd` compression on Parquet writes | `fetcher.py`, `processor.py` | Optimization | XS |
| 4 | Round price columns in CSV | `reporter.py` | Optimization | XS |
| 5 | Dedup on `(session_id, timestamp)` | `processor.py` | Data integrity | XS |
| 6 | Schema validation on Parquet load | `fetcher.py`, `processor.py` | Defensive | S |
| 7 | Per-experiment log file | `pipeline.py` | Observability | XS |
| 8 | `daily_ranges.parquet` in processed dir | `opening_range.py`, `pipeline.py` | Structure | XS |

## Affected Files

| File | Changes |
|:-----|:--------|
| `src/data/fetcher.py` | Fix glob patterns (×2), add `zstd` compression, add schema validation on load |
| `src/data/processor.py` | Add cache-hit early return, add output deduplication, add `zstd` compression |
| `src/evaluation/reporter.py` | Round price columns before CSV write |
| `src/pipeline.py` | Pass `log_file=paths.execution_log` to `setup_logging()`, use `paths.daily_ranges_file` |
| `src/strategy/opening_range.py` | Accept output path parameter for `daily_ranges.parquet` |

## Acceptance Criteria

- [ ] `fetcher.py` glob patterns use `self._config.data.timeframe` — no hardcoded `"1min"` string remains
- [ ] Raw and processed Parquet files written with `compression="zstd"`
- [ ] Running pipeline twice with same date range skips RTH processing on second run (logs "cache hit")
- [ ] `trades.csv` price columns have ≤ 4 decimal places
- [ ] `sessions.parquet` contains no duplicate `(session_id, timestamp)` rows
- [ ] `experiments/{experiment_id}/logs/execution.log` exists after every pipeline run
- [ ] `daily_ranges.parquet` written to `data/processed/{symbol}/{timeframe}_{date_slug}/`

## Dependencies

- SFIX-001 (PathManager — needed for `paths.execution_log`, `paths.daily_ranges_file`, `paths.processed_file`)
- SFIX-004 (Module integration — modules must accept path injection before these fixes cleanly apply)
