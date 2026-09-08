"""
tests/unit/test_accounting_reconciliation.py
============================================
Focused accounting regression for U1 (P1-O1 / P1-5).

Hand-calculated fixture: one session, fixed 100-share sizing, $0.01 slippage,
$0.0035 commission, LONG breakout held into the end-of-session fallback close.

Independent expectations (no values derived from the implementation):
  OR: high 501.0 / low 499.0 (width 2.0)
  Entry bar close 501.5 -> LONG, stop 499.0, risk 2.5, tp 501.5 + 2*2.5 = 506.5
  Entry fill 501.51, EOD exit fill 501.49 (adverse $0.01 each side)
  gross = (501.49 - 501.51) * 100 = -2.00
  slippage total = 2.00, commission total = 0.70
  net pnl = -4.70, final capital = 99995.30
  Last in-loop equity mark = 100000 + (501.5 - 501.51) * 100 = 99999.00

The fallback close updates capital AFTER the last per-bar equity record, so
ending equity disagrees with final capital until the engine records it.
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
