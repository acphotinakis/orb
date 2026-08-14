"""
tests/unit/test_backtest_engine.py
==================================
Unit tests for event-driven backtesting engine and dual-touch resolver.
"""

import pandas as pd
from src.backtest.engine import BacktestEngine
from src.backtest.models import ExitReason


def test_dual_touch_resolution_stops_out_conservatively(synthetic_rth_bars, mock_app_config):
    df = synthetic_rth_bars.copy()
    # Trigger breakout at bar 16 (09:46): Close = 501.5 (> OR High 501.0)
    # Entry = 501.51, Stop = 499.0, Target = 506.5
    df.loc[16, "close"] = 501.5
    df.loc[16, "high"] = 501.6

    # Bar 25 touches BOTH stop (Low 498.0 <= 499.0) AND target (High 507.0 >= 506.5)
    df.loc[25, "low"] = 498.0
    df.loc[25, "high"] = 507.0

    engine = BacktestEngine(mock_app_config)
    res = engine.run(df)

    assert len(res.trades) == 1
    trade = res.trades[0]
    # DUAL-TOUCH MUST CONSERVATIVELY BE STOP
    assert trade.exit_reason == ExitReason.STOP.value
    assert trade.pnl_dollars < 0


def test_eod_force_exit_at_1559(synthetic_rth_bars, mock_app_config):
    df = synthetic_rth_bars.copy()
    # Breakout at 09:46
    df.loc[16, "close"] = 501.5
    # Price stays flat in [500.5, 502.0] never hitting SL or TP
    df.loc[17:390, "close"] = 501.5
    df.loc[17:390, "high"] = 502.0
    df.loc[17:390, "low"] = 501.0

    engine = BacktestEngine(mock_app_config)
    res = engine.run(df)

    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.exit_reason == ExitReason.EOD.value
