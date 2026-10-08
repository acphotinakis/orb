"""
tests/unit/test_metrics.py
==========================
Unit tests for performance & risk metrics calculations.
"""

import pandas as pd

from src.evaluation.metrics import calculate_portfolio_metrics, calculate_trade_metrics


def test_trade_metrics_known_distribution():
    # 6 wins (+2.0R, +$200), 4 losses (-1.0R, -$100)
    trades_df = pd.DataFrame(
        [
            {
                "trade_id": i,
                "direction": "LONG",
                "pnl_dollars": 200.0 if i < 6 else -100.0,
                "r_multiple": 2.0 if i < 6 else -1.0,
                "exit_reason": "TARGET" if i < 6 else "STOP",
                "slippage_paid": 1.0,
                "commission_paid": 0.5,
            }
            for i in range(10)
        ]
    )

    m = calculate_trade_metrics(trades_df)
    assert m["win_rate"] == 0.60
    assert m["total_realized_r"] == 8.0
    assert m["avg_r"] == 0.8
    assert m["profit_factor"] == 3.0
    assert m["expectancy_r"] == 0.8


def test_portfolio_drawdown_calculation():
    # 10% drawdown from peak 120 -> 108
    equity_df = pd.DataFrame({"equity": [100.0, 110.0, 120.0, 108.0, 115.0]})
    pm = calculate_portfolio_metrics(equity_df, initial_capital=100.0)
    assert round(pm["max_drawdown_pct"], 1) == 10.0
