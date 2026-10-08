"""
tests/integration/test_pipeline_e2e.py
======================================
End-to-End integration tests for the full SPY ORB backtesting system.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.config import load_config
from src.pipeline import ORBPipeline, PipelineRunResult


def test_pipeline_e2e_execution(tmp_path: Path):
    """Runs full ORBPipeline on multi-day synthetic cached data and
    asserts all outputs.
    """
    # 1. Setup isolated directories in tmp_path following PathManager layout
    # data/raw/{symbol}/{feed}/{timeframe}/
    raw_dir = tmp_path / "data" / "raw" / "SPY" / "sip" / "1Min"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 2. Seed 3 days of synthetic 1-minute bars in raw cache
    # 14:30 to 21:00 UTC (09:30 to 16:00 ET) = 391 bars per day
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    all_bars = []
    for d in dates:
        open_utc = pd.Timestamp(f"{d} 09:30:00", tz="America/New_York").tz_convert(
            "UTC"
        )
        ts = pd.date_range(start=open_utc, periods=391, freq="1min")
        n = len(ts)
        opens = np.full(n, 500.0)
        highs = np.full(n, 500.5)
        lows = np.full(n, 499.5)
        closes = np.full(n, 500.0)
        volumes = np.full(n, 1000.0)

        # Set specific OR extremes (09:30 - 09:44)
        highs[5] = 501.0
        lows[8] = 499.0

        # Day 1: Long Breakout -> TP hit
        if d == "2024-01-02":
            closes[16] = 501.5
            highs[16] = 501.6
            closes[25] = 506.6
            highs[25] = 507.0
        # Day 2: Long Breakout -> Stop hit
        elif d == "2024-01-03":
            closes[16] = 501.5
            highs[16] = 501.6
            lows[25] = 498.0
            closes[25] = 498.5
        # Day 3: Long Breakout -> EOD exit
        elif d == "2024-01-04":
            closes[16] = 501.5
            highs[16] = 501.6
            closes[17:391] = 501.5
            highs[17:391] = 502.0
            lows[17:391] = 501.0

        df_day = pd.DataFrame(
            {
                "timestamp": ts,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": volumes,
            }
        )
        all_bars.append(df_day)

    raw_combined = pd.concat(all_bars, ignore_index=True)
    cache_file = raw_dir / "SPY_1Min_20240102_20240104.parquet"
    raw_combined.to_parquet(
        cache_file, engine="pyarrow", compression="zstd", index=False
    )

    # 3. Configure and run pipeline using tmp_path as root base_dir
    base_cfg = load_config("config/default_config.yaml")
    cfg = dataclasses.replace(
        base_cfg,
        data=dataclasses.replace(base_cfg.data, timeframe="1Min"),
    )

    pipeline = ORBPipeline(config=cfg, base_dir=tmp_path)
    run_id = "test_e2e_run"
    result = pipeline.run(
        start_date="2024-01-02",
        end_date="2024-01-04",
        refresh_cache=False,
        generate_plots=True,
        run_id=run_id,
        base_dir=tmp_path,
    )

    # 4. Assertions on result objects
    assert isinstance(result, PipelineRunResult)
    assert result.total_trades == 3
    assert result.execution_time_seconds > 0

    # 5. Assert all files written to disk in experiment directory
    exp_dir = tmp_path / "experiments" / f"{run_id}__SPY_1Min_20240102_20240104"
    run_res_dir = exp_dir / "results"
    assert (run_res_dir / "trades.csv").exists()
    assert (run_res_dir / "equity_curve.csv").exists()
    assert (run_res_dir / "daily_summary.csv").exists()
    assert (run_res_dir / "metrics.json").exists()
    assert (exp_dir / "config_snapshot.yaml").exists()

    # Verify plots
    plots_out = exp_dir / "plots"
    assert (plots_out / "equity_curves" / "equity_curve.png").exists()
    assert (plots_out / "drawdowns" / "drawdown_curve.png").exists()
    assert (plots_out / "distributions" / "r_multiples.png").exists()
    assert (plots_out / "distributions" / "trade_durations.png").exists()

    trade_plots = list((plots_out / "trades").glob("trade_*.png"))
    assert len(trade_plots) == 3


def test_cli_offline_success_with_injected_source(tmp_path: Path, monkeypatch):
    """CLI completes offline (exit 0) using an injected bar source; no network.

    Replaces the network-backed fetcher, runs fully in-process under an
    isolated cwd, and asserts the expected artifacts are written.
    """
    import shutil

    import src.pipeline as pipeline_module
    from src.main import main

    dates = ["2024-01-02", "2024-01-03"]
    all_bars = []
    for d in dates:
        open_utc = pd.Timestamp(f"{d} 09:30:00", tz="America/New_York").tz_convert(
            "UTC"
        )
        ts = pd.date_range(start=open_utc, periods=391, freq="1min")
        n = len(ts)
        opens = np.full(n, 500.0)
        highs = np.full(n, 500.5)
        lows = np.full(n, 499.5)
        closes = np.full(n, 500.0)
        volumes = np.full(n, 1000.0)
        highs[5] = 501.0
        lows[8] = 499.0
        if d == "2024-01-02":
            closes[16] = 501.5
            highs[16] = 501.6
            closes[25] = 506.6
            highs[25] = 507.0
        all_bars.append(
            pd.DataFrame(
                {
                    "timestamp": ts,
                    "open": opens,
                    "high": highs,
                    "low": lows,
                    "close": closes,
                    "volume": volumes,
                }
            )
        )
    injected = pd.concat(all_bars, ignore_index=True)

    class _InjectedFetcher:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_and_cache(self, **kwargs):
            return injected

    monkeypatch.setattr(pipeline_module, "DataFetcher", _InjectedFetcher)
    monkeypatch.chdir(tmp_path)
    # Resolve repo config before chdir takes effect
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy(
        repo_root / "config" / "default_config.yaml", tmp_path / "test_config.yaml"
    )

    rc = main(
        [
            "--config",
            str(tmp_path / "test_config.yaml"),
            "--run-id",
            "cli_offline_run",
            "--timeframe",
            "1Min",
            "--start-date",
            "2024-01-02",
            "--end-date",
            "2024-01-03",
            "--no-plots",
            "--log-level",
            "WARNING",
        ]
    )
    assert rc == 0

    exp_dir = tmp_path / "experiments" / "cli_offline_run__SPY_1Min_20240102_20240103"
    run_res_dir = exp_dir / "results"
    assert (run_res_dir / "trades.csv").exists()
    assert (run_res_dir / "equity_curve.csv").exists()
    assert (run_res_dir / "metrics.json").exists()
    assert (exp_dir / "config_snapshot.yaml").exists()


def test_cli_missing_config_returns_error(tmp_path: Path, monkeypatch):
    """Explicit failure: nonexistent config file exits nonzero without
    traceback leak.
    """
    from src.main import main

    monkeypatch.chdir(tmp_path)
    rc = main(["--config", str(tmp_path / "does_not_exist.yaml"), "--no-plots"])
    assert rc == 1


def test_cli_invalid_timeframe_returns_error(tmp_path: Path, monkeypatch):
    """Explicit failure: unsupported timeframe is rejected with a field error."""
    import shutil

    from src.main import main

    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy(
        repo_root / "config" / "default_config.yaml", tmp_path / "test_config.yaml"
    )
    rc = main(
        [
            "--config",
            str(tmp_path / "test_config.yaml"),
            "--timeframe",
            "BogusTF",
            "--no-plots",
            "--log-level",
            "WARNING",
        ]
    )
    assert rc == 1
