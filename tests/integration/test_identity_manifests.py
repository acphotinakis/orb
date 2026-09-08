"""
tests/integration/test_identity_manifests.py
=============================================
P1-T05/T08/T13 (pipeline level): identity-keyed dataset directories, UUID run
IDs, rerun reproducibility, old-run stability, and manifest content — all
offline on seeded raw cache.
"""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.common.config import load_config
from src.common.exceptions import ConfigurationError
from src.pipeline import ORBPipeline
from src.services.artifact_store import is_run_complete


def _seed_raw_cache(base_dir: Path, dates=("2024-01-02", "2024-01-03")) -> None:
    raw_dir = base_dir / "data" / "raw" / "SPY" / "sip" / "1Min"
    raw_dir.mkdir(parents=True, exist_ok=True)
    bars = []
    for d in dates:
        open_utc = pd.Timestamp(f"{d} 09:30:00", tz="America/New_York").tz_convert("UTC")
        ts = pd.date_range(start=open_utc, periods=391, freq="1min")
        n = len(ts)
        opens = np.full(n, 500.0)
        highs = np.full(n, 500.5)
        lows = np.full(n, 499.5)
        closes = np.full(n, 500.0)
        vols = np.full(n, 1000.0)
        highs[5] = 501.0
        lows[8] = 499.0
        if d == "2024-01-02":
            closes[16] = 501.5
            highs[16] = 501.6
            closes[25] = 506.6
            highs[25] = 507.0
        bars.append(pd.DataFrame({
            "timestamp": ts, "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": vols,
        }))
    combined = pd.concat(bars, ignore_index=True)
    combined.to_parquet(
        raw_dir / "SPY_1Min_20240102_20240103.parquet",
        engine="pyarrow", compression="zstd", index=False,
    )


def _config(timeframe="1Min", or_minutes=15):
    base = load_config("config/default_config.yaml")
    cfg = dataclasses.replace(
        base, data=dataclasses.replace(base.data, timeframe=timeframe)
    )
    if or_minutes != cfg.strategy.opening_range_minutes:
        cfg = dataclasses.replace(
            cfg,
            strategy=dataclasses.replace(
                cfg.strategy, opening_range_minutes=or_minutes
            ),
        )
    return cfg


def _run(base_dir: Path, run_id, cfg=None, label=None):
    pipeline = ORBPipeline(config=cfg or _config(), base_dir=base_dir)
    return pipeline.run(
        start_date="2024-01-02",
        end_date="2024-01-03",
        refresh_cache=False,
        generate_plots=False,
        run_id=run_id,
        base_dir=base_dir,
        log_level="WARNING",
        run_label=label,
    )


def _exp_dir(base_dir: Path, run_id: str) -> Path:
    return base_dir / "experiments" / f"{run_id}__SPY_1Min_20240102_20240103"


def test_same_label_twice_yields_distinct_uuid_runs(tmp_path):
    """T08: one label, two submissions → two immutable run identities."""
    _seed_raw_cache(tmp_path)
    r1 = _run(tmp_path, None, label="same")
    r2 = _run(tmp_path, None, label="same")
    assert r1.metrics["trade_metrics"]["total_trades"] == r2.metrics["trade_metrics"]["total_trades"]
    dirs = sorted((tmp_path / "experiments").iterdir())
    assert len(dirs) == 2
    assert dirs[0].name != dirs[1].name
    for d in dirs:
        assert is_run_complete(d) is True
        manifest = json.loads((d / "manifest.json").read_text())
        assert manifest["run_label"] == "same"


def test_traversal_run_id_rejected_before_side_effects(tmp_path):
    """T08: unsafe run IDs fail before storage creation or data fetch."""
    _seed_raw_cache(tmp_path)
    with pytest.raises(ConfigurationError):
        _run(tmp_path, "../evil")
    assert not (tmp_path / "experiments").exists()


def test_identical_rerun_reproduces_metrics_and_shares_dataset(tmp_path):
    """T13: same inputs rerun → equal metrics, same identity directory."""
    _seed_raw_cache(tmp_path)
    _run(tmp_path, "runA")
    proc_a = (_exp_dir(tmp_path, "runA") / "manifest.json")
    manifest_a = json.loads(proc_a.read_text())
    _run(tmp_path, "runB")
    manifest_b = json.loads((_exp_dir(tmp_path, "runB") / "manifest.json").read_text())

    for key in ("total_pnl_dollars", "total_trades"):
        assert (
            manifest_a["artifacts"] and manifest_b["artifacts"]
        )
    metrics_a = json.loads((_exp_dir(tmp_path, "runA") / "results" / "metrics.json").read_text())
    metrics_b = json.loads((_exp_dir(tmp_path, "runB") / "results" / "metrics.json").read_text())
    assert metrics_a["trade_metrics"] == metrics_b["trade_metrics"]
    assert metrics_a["portfolio_metrics"] == metrics_b["portfolio_metrics"]
    # Same identity → same immutable dataset directory (cache hit, not rebuild).
    assert (
        manifest_a["datasets"]["processed"]["identity_hash"]
        == manifest_b["datasets"]["processed"]["identity_hash"]
    )
    assert (
        manifest_a["datasets"]["processed"]["fingerprint"]
        == manifest_b["datasets"]["processed"]["fingerprint"]
    )


def test_old_run_stable_after_new_inputs(tmp_path):
    """T13: a later run with different OR settings never mutates runA's data."""
    _seed_raw_cache(tmp_path)
    _run(tmp_path, "runA")
    dir_a = _exp_dir(tmp_path, "runA")
    processed_rel = json.loads((dir_a / "manifest.json").read_text())["datasets"]["processed"]["path"]
    processed_a = tmp_path / processed_rel
    bytes_before = processed_a.read_bytes()
    metrics_before = (dir_a / "results" / "metrics.json").read_text()

    _run(tmp_path, "runB", cfg=_config(or_minutes=30))

    assert processed_a.read_bytes() == bytes_before
    assert (dir_a / "results" / "metrics.json").read_text() == metrics_before
    assert is_run_complete(dir_a) is True


def test_manifest_content_contract(tmp_path):
    """Manifest carries config, request, source, datasets, checksums, accounting."""
    _seed_raw_cache(tmp_path)
    _run(tmp_path, "runA", label="content-check")
    manifest = json.loads((_exp_dir(tmp_path, "runA") / "manifest.json").read_text())

    assert manifest["manifest_version"] == "1.0"
    assert manifest["status"] == "succeeded"
    assert manifest["run_label"] == "content-check"
    assert manifest["config"]["strategy"]["ticker"] == "SPY"
    assert manifest["request"]["start_date"] == "2024-01-02"
    assert set(manifest["source"]) >= {"available", "revision", "dirty"}
    assert manifest["datasets"]["processed"]["sessions"] >= 1
    assert manifest["datasets"]["raw"]["rows"] > 0
    assert manifest["artifacts"]["results/metrics.json"]["sha256"]
    gap = manifest["accounting"]["reconcile_gap"]
    assert abs(gap) <= 0.01
    assert is_run_complete(_exp_dir(tmp_path, "runA")) is True
