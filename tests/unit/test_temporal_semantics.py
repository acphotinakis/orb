"""
tests/unit/test_temporal_semantics.py
======================================
P1-T11: bar-start vs decision-availability timestamps, exact opening-range
boundary, missing opening/final bars, DST transitions, early closes, and
coarse timeframes.

Recorded semantics (see validation/phase-01.md P1-O4):
bar timestamps are bar-OPEN times; a close-based decision is available one
bar duration later (availability = bar start + timeframe duration).
"""

import dataclasses

import numpy as np
import pandas as pd

from src.backtest.engine import BacktestEngine
from src.common.time_utils import get_timeframe_minutes
from src.data.processor import DataProcessor
from tests.unit.test_accounting_reconciliation import (  # shared hand-calc fixtures
    _assert_reconciles,
    _eod_fixture_config,
    _session_frame,
    _set_or,
)


def _raw_day(date_str, periods=391, freq="1min"):
    open_et = pd.Timestamp(f"{date_str} 09:30:00", tz="America/New_York")
    ts = pd.date_range(start=open_et, periods=periods, freq=freq)
    n = len(ts)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": np.full(n, 500.0),
            "high": np.full(n, 500.5),
            "low": np.full(n, 499.5),
            "close": np.full(n, 500.0),
            "volume": np.full(n, 1000.0),
        }
    )


def test_or_boundary_bar_is_first_tradable():
    """T11: minute 14 is OR, minute 15 is not; a late extreme never enters OR."""
    cfg = _eod_fixture_config()
    raw = _raw_day("2024-01-02")
    raw.loc[5, "high"] = 501.0
    raw.loc[8, "low"] = 499.0
    raw.loc[100, "high"] = 510.0  # future extreme must not widen the range
    import tempfile
    from pathlib import Path

    proc = DataProcessor(config=cfg, output_dir=Path(tempfile.mkdtemp()))
    out = proc.process(raw, save_to_disk=False)
    assert bool(out.loc[14, "is_opening_range"]) is True
    assert bool(out.loc[15, "is_opening_range"]) is False

    res = BacktestEngine(config=cfg).run(out)
    assert len(res.trades) == 0  # flat day: no breakout, no trade


def test_close_exactly_at_boundary_is_not_a_breakout():
    """T11: strict inequality — close == or_high produces no signal."""
    cfg = _eod_fixture_config()
    df = _set_or(_session_frame())
    df.loc[15, ["high", "close"]] = [501.0, 501.0]  # touches but never exceeds
    res = BacktestEngine(config=cfg).run(df)
    assert len(res.trades) == 0


def test_missing_or_bar_prunes_session_without_guessing():
    """T11: 14 of 15 OR bars → session pruned; engine never trades it."""
    cfg = _eod_fixture_config()
    raw = _raw_day("2024-01-02")
    raw = raw.drop(index=5).reset_index(drop=True)  # remove one OR bar
    import tempfile
    from pathlib import Path

    proc = DataProcessor(config=cfg, output_dir=Path(tempfile.mkdtemp()))
    out = proc.process(raw, save_to_disk=False)
    assert out.empty or out["session_id"].nunique() == 0

    res = BacktestEngine(config=cfg).run(out)
    assert len(res.trades) == 0


def test_missing_final_bar_flattens_at_last_available_bar():
    """T11: truncated session (no force-exit region) still reconciles."""
    cfg = _eod_fixture_config()
    df = _set_or(_session_frame(n=200))
    df.loc[15, ["high", "close"]] = [501.6, 501.5]
    df.loc[16:, "close"] = 501.5
    df.loc[16:, "high"] = 502.0
    df.loc[16:, "low"] = 501.0

    res = BacktestEngine(config=cfg).run(df)
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "EOD"
    assert res.trades[0].exit_time == df["timestamp"].iloc[-1]
    _assert_reconciles(res, cfg)


def test_early_close_session_flattens_at_1300():
    """T11: shortened session (09:30-13:00) policy = fallback at last bar."""
    cfg = _eod_fixture_config()
    df = _set_or(_session_frame(n=211))  # 09:30 + 210 min = 13:00
    assert str(df["timestamp"].iloc[-1].time()) == "13:00:00"
    df.loc[15, ["high", "close"]] = [501.6, 501.5]
    df.loc[16:, "close"] = 501.5
    df.loc[16:, "high"] = 502.0
    df.loc[16:, "low"] = 501.0

    res = BacktestEngine(config=cfg).run(df)
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "EOD"
    _assert_reconciles(res, cfg)


def test_dst_spring_forward_session_geometry():
    """T11: 2024-03-11 (UTC-4) RTH wall-clock geometry is unaffected."""
    cfg = _eod_fixture_config()
    raw = _raw_day("2024-03-11")
    raw.loc[5, "high"] = 501.0
    raw.loc[8, "low"] = 499.0
    assert str(raw["timestamp"].iloc[0].utcoffset()) == "-1 day, 20:00:00"  # UTC-4
    import tempfile
    from pathlib import Path

    proc = DataProcessor(config=cfg, output_dir=Path(tempfile.mkdtemp()))
    out = proc.process(raw, save_to_disk=False)
    assert out["session_id"].unique().tolist() == ["2024-03-11"]
    assert out["minute_of_day"].iloc[0] == 0
    assert out["minute_of_day"].iloc[-1] == 390
    assert out["is_opening_range"].sum() == 15


def test_dst_fall_back_session_geometry():
    """T11: 2024-11-04 (UTC-5) RTH wall-clock geometry is unaffected."""
    cfg = _eod_fixture_config()
    raw = _raw_day("2024-11-04")
    assert str(raw["timestamp"].iloc[0].utcoffset()) == "-1 day, 19:00:00"  # UTC-5
    import tempfile
    from pathlib import Path

    proc = DataProcessor(config=cfg, output_dir=Path(tempfile.mkdtemp()))
    out = proc.process(raw, save_to_disk=False)
    assert out["session_id"].unique().tolist() == ["2024-11-04"]
    assert out["minute_of_day"].iloc[-1] == 390


def test_coarse_five_minute_timeframe_runs():
    """T11: 5-minute bars host a 15-minute OR (3 bars) with close decisions."""
    cfg = _eod_fixture_config()
    cfg = dataclasses.replace(cfg, data=dataclasses.replace(cfg.data, timeframe="5Min"))
    raw = _raw_day("2024-01-02", periods=79, freq="5min")
    assert str(raw["timestamp"].iloc[-1].time()) == "16:00:00"
    raw.loc[1, "high"] = 501.0
    raw.loc[1, "low"] = 499.0
    raw.loc[3, "close"] = 501.5  # 09:45 bar closes above range high
    raw.loc[3, "high"] = 501.6
    import tempfile
    from pathlib import Path

    proc = DataProcessor(config=cfg, output_dir=Path(tempfile.mkdtemp()))
    out = proc.process(raw, save_to_disk=False)
    assert out["session_id"].nunique() == 1  # OR complete, not pruned
    res = BacktestEngine(config=cfg).run(out)
    assert len(res.trades) == 1
    assert res.trades[0].direction == "LONG"


def test_decision_availability_is_bar_start_plus_duration():
    """T11: recorded entry_time is the signal bar start; the decision was
    available one bar duration later (close), never at bar open."""
    cfg = _eod_fixture_config()
    df = _set_or(_session_frame())
    df.loc[15, ["high", "close"]] = [501.6, 501.5]

    res = BacktestEngine(config=cfg).run(df)
    trade = res.trades[0]
    bar_start = pd.Timestamp("2024-01-02 09:45:00", tz="America/New_York")
    assert pd.Timestamp(trade.entry_time) == bar_start
    availability = bar_start + pd.Timedelta(
        minutes=get_timeframe_minutes(cfg.data.timeframe)
    )
    assert availability == pd.Timestamp("2024-01-02 09:46:00", tz="America/New_York")
    # The entry bar's own open (500.0) could not fill at the close price:
    # execution uses the close-derived fill, recorded against bar start.
    assert trade.entry_price == 501.51
