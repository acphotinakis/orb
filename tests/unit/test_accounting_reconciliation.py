"""
tests/unit/test_accounting_reconciliation.py
============================================
Focused accounting regressions for U1 / P1-T09.

Hand-calculated fixtures (fixed 100-share sizing, $0.01 slippage, $0.0035
commission).  No expected value is derived from the implementation; all
figures below are worked out from the execution-model rules:

* LONG entry fill = signal close + $0.01; SHORT entry fill = close - $0.01.
* TARGET exits fill exactly (limit, no exit slippage).
* STOP/EOD exits take adverse $0.01.
* Commission is round-trip: 2 x $0.0035 x shares.
* Trade pnl = gross fill difference - total slippage - total commission.

Base geometry: OR high 501.0 / low 499.0 (width 2.0), target_r 2.0.
"""

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.common.config import (
    AppConfig,
    StrategyConfig,
    FiltersConfig,
    DataConfig,
    ExecutionConfig,
    OutputConfig,
)
from src.evaluation.metrics import calculate_trade_metrics


def _eod_fixture_config() -> AppConfig:
    return AppConfig(
        schema_version="1.0",
        strategy=StrategyConfig(
            ticker="SPY",
            opening_range_minutes=15,
            target_r=2.0,
            breakout_buffer_pct=0.0,
            breakout_confirmation="close",
            max_trades_per_day=1,
            force_exit_time="15:59:00",
            stop_method="opposite_range",
            direction_mode="both",
        ),
        filters=FiltersConfig(),
        data=DataConfig(timeframe="1Min"),
        execution=ExecutionConfig(
            initial_capital=100_000.0,
            position_sizing="fixed_shares",
            fixed_shares=100,
            slippage_per_share=0.01,
            commission_per_share=0.0035,
        ),
        output=OutputConfig(),
    )


def _eod_fixture_bars() -> pd.DataFrame:
    n = 30
    date_str = "2024-01-02"
    open_et = pd.Timestamp(f"{date_str} 09:30:00", tz="America/New_York")
    ts = pd.date_range(start=open_et, periods=n, freq="1min")

    opens = np.full(n, 500.0)
    highs = np.full(n, 500.5)
    lows = np.full(n, 499.5)
    closes = np.full(n, 500.0)
    volumes = np.full(n, 1000.0)

    # OR window idx 0..14: high 501.0, low 499.0
    highs[5] = 501.0
    lows[8] = 499.0

    # idx15: LONG breakout close, never touches stop (499) or target (506.5)
    closes[15] = 501.5
    highs[15] = 501.6

    # idx16..29: flat drift, no bracket touch -> position open at session end
    closes[16:] = 501.5
    highs[16:] = 502.0
    lows[16:] = 501.0

    idx = np.arange(n)
    return pd.DataFrame({
        "session_id": np.full(n, date_str),
        "timestamp": ts,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "minute_of_day": idx.astype(np.int32),
        "is_opening_range": idx < 15,
        "is_trading_window": (idx >= 15),
        "is_force_exit": np.zeros(n, dtype=bool),
    })


def test_eod_fallback_reconciles_capital_and_equity():
    cfg = _eod_fixture_config()
    result = BacktestEngine(config=cfg).run(_eod_fixture_bars())

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "EOD"
    # Full-precision internals compared with tight tolerance (P1-A4 rule);
    # currency-level reconciliation below uses the $0.01 budget.
    assert trade.pnl_dollars == pytest.approx(-4.70)
    assert trade.slippage_paid == pytest.approx(2.00)
    assert trade.commission_paid == pytest.approx(0.70)

    # Capital path: final = initial + sum(net trade pnl)
    assert result.final_capital == cfg.execution.initial_capital + sum(
        t.pnl_dollars for t in result.trades
    )

    # Equity path: last recorded equity must equal final capital (P1-A4, $0.01)
    last_equity = float(result.equity_curve["equity"].iloc[-1])
    assert abs(last_equity - result.final_capital) <= 0.01

    # Metrics path: reported total pnl must match ending equity minus initial
    trade_metrics = calculate_trade_metrics(result.trades_df)
    assert (
        abs(
            trade_metrics["total_pnl_dollars"]
            - (last_equity - cfg.execution.initial_capital)
        )
        <= 0.01
    )


def _session_frame(n=30, date_str="2024-01-02"):
    """Processed-shaped single session with neutral flat bars."""
    open_et = pd.Timestamp(f"{date_str} 09:30:00", tz="America/New_York")
    ts = pd.date_range(start=open_et, periods=n, freq="1min")
    idx = np.arange(n)
    return pd.DataFrame({
        "session_id": np.full(n, date_str),
        "timestamp": ts,
        "open": np.full(n, 500.0),
        "high": np.full(n, 500.5),
        "low": np.full(n, 499.5),
        "close": np.full(n, 500.0),
        "volume": np.full(n, 1000.0),
        "minute_of_day": idx.astype(np.int32),
        "is_opening_range": idx < 15,
        "is_trading_window": idx >= 15,
        "is_force_exit": np.zeros(n, dtype=bool),
    })


def _set_or(df):
    df.loc[5, "high"] = 501.0
    df.loc[8, "low"] = 499.0
    return df


def _assert_reconciles(result, cfg):
    last_equity = float(result.equity_curve["equity"].iloc[-1])
    assert result.final_capital == pytest.approx(
        cfg.execution.initial_capital
        + sum(t.pnl_dollars for t in result.trades)
    )
    assert abs(last_equity - result.final_capital) <= 0.01
    return last_equity


def test_short_target_hand_calculated():
    """T09 SHORT + TARGET: entry 498.49, exit exactly 493.5, net +497.30."""
    cfg = _eod_fixture_config()
    df = _set_or(_session_frame())
    df.loc[15, ["high", "low", "close"]] = [499.0, 498.0, 498.5]
    df.loc[16, ["high", "low"]] = [498.0, 493.0]  # touches tp 493.5
    df.loc[17:, "close"] = 495.0
    df.loc[17:, "high"] = 496.0
    df.loc[17:, "low"] = 494.0

    result = BacktestEngine(config=cfg).run(df)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "SHORT"
    assert trade.exit_reason == "TARGET"
    assert trade.exit_price == pytest.approx(493.5)
    assert trade.slippage_paid == pytest.approx(1.00)  # entry only
    assert trade.commission_paid == pytest.approx(0.70)
    assert trade.pnl_dollars == pytest.approx(497.30)
    assert result.final_capital == pytest.approx(100497.30)
    _assert_reconciles(result, cfg)


def test_long_stop_hand_calculated():
    """T09 LONG + STOP: entry 501.51, stop fill 498.99, net -254.70."""
    cfg = _eod_fixture_config()
    df = _set_or(_session_frame())
    df.loc[15, ["high", "close"]] = [501.6, 501.5]
    df.loc[16, ["high", "low"]] = [502.0, 498.0]  # stop, below target
    df.loc[17:, "close"] = 500.0

    result = BacktestEngine(config=cfg).run(df)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "STOP"
    assert trade.exit_price == pytest.approx(498.99)
    assert trade.slippage_paid == pytest.approx(2.00)
    assert trade.pnl_dollars == pytest.approx(-254.70)
    assert result.final_capital == pytest.approx(99745.30)
    _assert_reconciles(result, cfg)


def test_same_bar_dual_touch_resolves_to_stop_exact():
    """T09 dual touch: STOP wins with the same -254.70 fills as a pure stop."""
    cfg = _eod_fixture_config()
    df = _set_or(_session_frame())
    df.loc[15, ["high", "close"]] = [501.6, 501.5]
    df.loc[16, ["high", "low"]] = [507.0, 498.0]  # both brackets touched
    df.loc[17:, "close"] = 500.0

    result = BacktestEngine(config=cfg).run(df)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "STOP"
    assert trade.pnl_dollars == pytest.approx(-254.70)
    _assert_reconciles(result, cfg)


def test_zero_shares_takes_no_trade():
    """T09: unaffordable sizing yields zero shares — no trade, flat capital."""
    cfg = _eod_fixture_config()
    import dataclasses

    cfg = dataclasses.replace(
        cfg,
        execution=dataclasses.replace(cfg.execution, initial_capital=10.0),
    )
    df = _set_or(_session_frame())
    df.loc[15, ["high", "close"]] = [501.6, 501.5]

    result = BacktestEngine(config=cfg).run(df)
    assert len(result.trades) == 0
    assert result.final_capital == pytest.approx(10.0)
    assert (result.equity_curve["equity"] == 10.0).all()
