# SFIX-003: Migrate to Experiment-Scoped Directory Structure

## Objective

Replace the current flat top-level `data/`, `results/`, and `plots/` trees with an **experiment-scoped** layout where all run-specific artifacts are co-located under `experiments/{experiment_id}/` and shared data caches remain in a common `data/` tree.

## Problem

### Current Layout
```
orb/
├── data/raw/SPY/
├── data/processed/SPY/
├── results/backtest/{run_id}/
├── plots/trades/
├── plots/equity_curves/
├── plots/drawdowns/
└── plots/distributions/
```

### Issues
1. **Run collisions** — Two runs with different parameters but same `run_id` silently overwrite results
2. **Fragmented artifacts** — Understanding one experiment requires looking across 4 separate top-level trees
3. **Hardcoded symbol** — `data/raw/SPY/` fails for AAPL/NVDA without code changes
4. **No feed segregation** — IEX (15-min delayed) and SIP (real-time) data can accidentally mix
5. **Not archivable** — Cannot `tar` a single folder to capture a complete experiment

## Target Directory Structure

```
orb/
├── data/
│   ├── raw/
│   │   └── {SYMBOL}/
│   │       └── {FEED}/                    # iex or sip — prevents cross-feed cache pollution
│   │           └── {TIMEFRAME}/           # 15Min, 5Min, etc.
│   │               └── {SYMBOL}_{TIMEFRAME}_{START}_{END}.parquet
│   │
│   └── processed/
│       └── {SYMBOL}/
│           └── {TIMEFRAME}_{START}_{END}/ # Reusable across runs with same date range
│               ├── sessions.parquet
│               └── daily_ranges.parquet
│
└── experiments/
    └── {RUN_ID}__{SYMBOL}_{TIMEFRAME}_{START}_{END}/
        ├── config_snapshot.yaml           # Frozen config for reproducibility
        ├── logs/
        │   └── execution.log
        ├── results/
        │   ├── trades.csv
        │   ├── equity_curve.csv
        │   ├── daily_summary.csv
        │   └── metrics.json
        └── plots/
            ├── equity_curves/
            │   └── equity_curve.png
            ├── drawdowns/
            │   └── drawdown_curve.png
            ├── distributions/
            │   ├── r_multiples.png
            │   └── trade_durations.png
            ├── candlesticks/
            │   ├── {SYMBOL}_candlestick_2024-01-02.png
            │   └── ...
            └── trades/
                ├── trade_001_2024-01-02_LONG.png
                └── ...
```

## Key Design Decisions

### Raw Cache Separation by Feed (`{SYMBOL}/{FEED}/{TIMEFRAME}/`)
IEX data (free tier, 15-min delayed) and SIP data (real-time consolidated tape) have different prices for the same bar timestamps. Storing in separate subdirectories prevents accidental cache hits returning wrong-feed data.

### Processed Sessions Keyed by Date Range
`data/processed/SPY/15Min_20240102_20261231/sessions.parquet` is reused by any backtest run using the same symbol, timeframe, and date range — regardless of strategy parameters. This avoids re-running the expensive RTH filter + session-tagging step for parameter sweep runs.

### Self-Describing Experiment Directory
`experiments/baseline_v1__SPY_15Min_20240102_20261231/` is fully self-describing:
- `baseline_v1` — run/strategy variant ID
- `SPY` — symbol
- `15Min` — timeframe
- `20240102_20261231` — date range

Two runs with different parameters will never collide.

### Config Snapshot in Every Experiment
`config_snapshot.yaml` is saved at the start of every run, enabling perfect reproducibility even if `default_config.yaml` changes later. This is the key difference vs. the current system where there is no record of what parameters produced a given set of results.

## Migration Plan

1. Implement `PathManager` (SFIX-001) — generates new paths automatically
2. Update all modules to use `PathManager` (SFIX-004)
3. After validating new runs work, archive old data:
   ```bash
   mkdir -p archive/pre-sfix
   mv data/raw/SPY results/backtest plots/ archive/pre-sfix/
   ```
4. Update `.gitignore` to cover new `experiments/` directory

## `.gitignore` Updates

```gitignore
# Experiment artifacts (large binaries, auto-generated)
experiments/
data/raw/
data/processed/

# Keep config and code
!config/
!src/
!docs/
```

## Affected Files

| File | Change Required |
|:-----|:----------------|
| `src/common/paths.py` | Implements the target structure (created in SFIX-001) |
| `src/pipeline.py` | Construct `PathManager` in `run()`, save `config_snapshot.yaml` |
| `config/default_config.yaml` | Remove `raw_dir`, `processed_dir` path fields |
| `.gitignore` | Add `experiments/`, refine `data/` excludes |
| `README.md` / `RUNBOOK.md` | Update documented directory structure |

## Acceptance Criteria

- [ ] `python -m src.main --symbol SPY --timeframe 15Min` outputs to `experiments/baseline_v1__SPY_15Min_*/`
- [ ] Running same command twice reuses cached Parquet (no re-download, no re-processing)
- [ ] `python -m src.main --symbol AAPL --timeframe 5Min` produces a **separate** experiment directory
- [ ] `config_snapshot.yaml` exists in every experiment directory after a run
- [ ] `experiments/` is in `.gitignore`
- [ ] Raw cache segregated by feed: `data/raw/SPY/sip/15Min/` vs `data/raw/SPY/iex/15Min/`

## Dependencies

- SFIX-001 (PathManager implements the paths)
- SFIX-002 (Config: `is_paper` field, path fields removed from `DataConfig`)
