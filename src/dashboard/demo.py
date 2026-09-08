"""
src.dashboard.demo
==================
Deterministic offline demo generator (P2-O1).

Seeds synthetic raw bars into an isolated storage root and runs them through
the REAL :class:`~src.pipeline.ORBPipeline` — no network, no credentials.
Every demo run carries a ``synthetic demo`` label so the explorer badges it
and it can never be mistaken for market data.

Usage::

    .orb_venv/bin/python -m src.dashboard.demo --root demo_runs
    .orb_venv/bin/streamlit run src/dashboard/app.py
    # then enter "demo_runs" as the storage root in the sidebar
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.config import load_config
from src.pipeline import ORBPipeline

NORMAL_DAYS = ("2024-01-02", "2024-01-03", "2024-01-04")
FLAT_DAYS = ("2024-02-05", "2024-02-06")


def _day_frame(date_str: str, breaks: list | None = None) -> pd.DataFrame:
    """One 391-bar RTH day; *breaks* is [(index, field, value), ...]."""
    open_utc = pd.Timestamp(f"{date_str} 09:30:00", tz="America/New_York").tz_convert("UTC")
    ts = pd.date_range(start=open_utc, periods=391, freq="1min")
    n = len(ts)
    df = pd.DataFrame({
        "timestamp": ts,
        "open": np.full(n, 500.0),
        "high": np.full(n, 500.5),
        "low": np.full(n, 499.5),
        "close": np.full(n, 500.0),
        "volume": np.full(n, 1000.0),
    })
    df.loc[5, "high"] = 501.0
    df.loc[8, "low"] = 499.0
    for idx, field, value in breaks or []:
        df.loc[idx, field] = value
    return df


def _seed_raw_cache(root: Path) -> None:
    normal = pd.concat(
        [
            _day_frame(
                "2024-01-02",
                [(16, "close", 501.5), (16, "high", 501.6),
                 (25, "close", 506.6), (25, "high", 507.0)],
            ),
            _day_frame(
                "2024-01-03",
                [(16, "close", 501.5), (16, "high", 501.6),
                 (25, "close", 498.5), (25, "low", 498.0)],
            ),
            _day_frame("2024-01-04", [(16, "close", 501.5), (16, "high", 501.6)]),
        ],
        ignore_index=True,
    )
    flat = pd.concat([_day_frame(d) for d in FLAT_DAYS], ignore_index=True)
    raw_dir = root / "data" / "raw" / "SPY" / "sip" / "1Min"
    raw_dir.mkdir(parents=True, exist_ok=True)
    normal.to_parquet(
        raw_dir / "SPY_1Min_20240102_20240104.parquet",
        engine="pyarrow", compression="zstd", index=False,
    )
    flat.to_parquet(
        raw_dir / "SPY_1Min_20240205_20240206.parquet",
        engine="pyarrow", compression="zstd", index=False,
    )


def generate_demo(root: str | Path = "demo_runs") -> list[str]:
    """Seed + execute the demo datasets.  Returns experiment directory names."""
    root_path = Path(root)
    _seed_raw_cache(root_path)
    base = load_config("config/default_config.yaml")
    cfg = dataclasses.replace(
        base, data=dataclasses.replace(base.data, timeframe="1Min")
    )
    made = []
    for run_id, label, start, end in (
        ("demo_normal", "synthetic demo (normal: TP/STOP/EOD)", "2024-01-02", "2024-01-04"),
        ("demo_zero_trades", "synthetic demo (zero trades)", "2024-02-05", "2024-02-06"),
    ):
        result = ORBPipeline(config=cfg, base_dir=root_path).run(
            start_date=start,
            end_date=end,
            refresh_cache=False,
            generate_plots=False,
            run_id=run_id,
            base_dir=root_path,
            log_level="WARNING",
            run_label=label,
        )
        exp = next(
            (root_path / "experiments").glob(f"{run_id}__*")
        )
        made.append(exp.name)
        print(f"demo run {run_id}: {result.total_trades} trades -> {exp}")
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the offline ORB demo.")
    parser.add_argument("--root", default="demo_runs", help="Demo storage root.")
    args = parser.parse_args(argv)
    generate_demo(args.root)
    print(f"Launch: .orb_venv/bin/streamlit run src/dashboard/app.py (root: {args.root})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
