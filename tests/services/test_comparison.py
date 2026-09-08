"""
tests/services/test_comparison.py
===================================
P4-T09..T11: compatibility-aware comparison and read-only code trace.
"""

import pandas as pd
import pytest

from src.services import replay
from src.services.replay import (
    align_equity,
    compatibility_notes,
    diff_configs,
    metric_deltas,
    normalized_returns,
    read_source_excerpt,
)


def _manifest(**overrides):
    base = {
        "run_id": "a",
        "config": {"execution": {"initial_capital": 100000.0}},
        "request": {"start_date": "2024-01-02", "end_date": "2024-01-03"},
        "datasets": {"processed": {
            "symbol": "SPY", "feed": "sip", "timeframe": "1Min",
            "identity_hash": "h1",
        }},
    }
    base.update(overrides)
    return base


def test_identical_runs_have_zero_deltas():
    config = {"strategy": {"target_r": 2.0}, "execution": {"initial_capital": 100000.0}}
    assert diff_configs(config, dict(config)) == []
    metrics = {"trade_metrics": {"total_pnl_dollars": 10.0},
               "portfolio_metrics": {"ending_equity": 100010.0}}
    deltas = metric_deltas(metrics, metrics)
    assert all(d is None or d == 0 for _, _, _, _, d in deltas)
    assert compatibility_notes(_manifest(), _manifest(run_id="b")) == []


def test_capital_and_input_differences_are_labeled():
    notes = compatibility_notes(_manifest(), _manifest(
        run_id="b",
        config={"execution": {"initial_capital": 50000.0}},
        request={"start_date": "2024-02-01", "end_date": "2024-02-02"},
        datasets={"processed": {
            "symbol": "QQQ", "feed": "iex", "timeframe": "1Min",
            "identity_hash": "h2",
        }},
    ))
    text = " ".join(notes)
    assert "initial capital" in text
    assert "symbol" in text
    assert "feed" in text
    assert "data version" in text
    assert "date ranges" in text

    diffs = diff_configs(
        {"execution": {"initial_capital": 100000.0}},
        {"execution": {"initial_capital": 50000.0}},
    )
    assert ("execution.initial_capital", 100000.0, 50000.0) in diffs


def test_normalized_views_carry_explicit_baselines():
    aligned = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-02", periods=3, freq="D", tz="UTC"),
        "equity_a": [100000.0, 101000.0, 102000.0],
        "equity_b": [50000.0, 50500.0, 51000.0],
    })
    out = normalized_returns(aligned, 100000.0, 50000.0)
    assert out["return_a"].tolist() == pytest.approx([0.0, 0.01, 0.02])
    assert out["return_b"].tolist() == pytest.approx([0.0, 0.01, 0.02])
    with pytest.raises(ValueError):
        normalized_returns(aligned, 0.0, 50000.0)


def test_no_overlap_has_no_misleading_overlay():
    left = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-02", periods=2, freq="D", tz="UTC"),
        "equity": [100000.0, 101000.0],
    })
    right = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-04", periods=2, freq="D", tz="UTC"),
        "equity": [100000.0, 99000.0],
    })
    common = align_equity(left, right, mode="common")
    assert common.empty  # no shared timestamps: no overlay to draw
    full = align_equity(left, right, mode="full")
    assert len(full) == 4
    assert full["overlap"].tolist() == [False, False, False, False]
    assert full["equity_a"].isna().sum() == 2  # NaN, never zero-filled
    with pytest.raises(ValueError):
        align_equity(left, right, mode="sideways")


def test_single_point_overlap_aligns():
    left = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-02", periods=2, freq="D", tz="UTC"),
        "equity": [100000.0, 101000.0],
    })
    right = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-03", periods=2, freq="D", tz="UTC"),
        "equity": [101000.0, 102000.0],
    })
    common = align_equity(left, right, mode="common")
    assert len(common) == 1


def test_source_excerpt_obeys_allowlist(tmp_path):
    snapshot = {}
    with pytest.raises(ValueError, match="allowlist"):
        read_source_excerpt(snapshot, "src/main.py")
    with pytest.raises(ValueError, match="allowlist"):
        read_source_excerpt(snapshot, "../outside.py")
    with pytest.raises(ValueError, match="unavailable"):
        read_source_excerpt(snapshot, "src/backtest/engine.py")


def test_source_excerpt_returns_function_block():
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    from src.services.source_snapshot import capture_source_snapshot
    snapshot = capture_source_snapshot(repo)
    excerpt = read_source_excerpt(snapshot, "src/strategy/signals.py",
                                  function="evaluate_bar_signal")
    assert excerpt.startswith("def evaluate_bar_signal(")
    assert len(excerpt.splitlines()) <= 80
    head = read_source_excerpt(snapshot, "src/strategy/signals.py", max_lines=5)
    assert head == "\n".join((repo / "src/strategy/signals.py").read_text().splitlines()[:5])
