"""
Tests for src.evaluation.metrics
================================

Unit tests for the performance metrics calculation functions.
"""

import pandas as pd

from src.common.config import (
    AppConfig,
    DataConfig,
    ExecutionConfig,
    FiltersConfig,
    OutputConfig,
    StrategyConfig,
)
from src.evaluation.metrics import (
    calculate_portfolio_metrics,
    calculate_trade_metrics,
    generate_performance_report,
)


def test_calculate_trade_metrics_empty_dataframe():
    """Test trade metrics calculation with empty DataFrame.

    Tests the edge case where no trades exist, ensuring all metrics return
    appropriate default values and no exceptions are raised.
    """
    trades_df = pd.DataFrame()

    result = calculate_trade_metrics(trades_df)

    expected = {
        "total_trades": 0,
        "long_trades": 0,
        "short_trades": 0,
        "win_count": 0,
        "loss_count": 0,
        "scratch_count": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "total_realized_r": 0.0,
        "avg_r": 0.0,
        "median_r": 0.0,
        "std_r": 0.0,
        "expectancy_r": 0.0,
        "avg_win_dollars": 0.0,
        "avg_loss_dollars": 0.0,
        "payoff_ratio": 0.0,
        "best_trade_dollars": 0.0,
        "worst_trade_dollars": 0.0,
        "best_trade_r": 0.0,
        "worst_trade_r": 0.0,
        "exit_reasons": {"TARGET": 0, "STOP": 0, "EOD": 0},
        "total_pnl_dollars": 0.0,
        "total_slippage_dollars": 0.0,
        "total_commission_dollars": 0.0,
    }

    assert result == expected


def test_calculate_trade_metrics_all_wins():
    """Test trade metrics calculation with all winning trades."""
    trades_df = pd.DataFrame(
        {
            "direction": ["LONG", "SHORT", "LONG"],
            "pnl_dollars": [100.0, 200.0, 150.0],
            "r_multiple": [2.0, 3.0, 2.5],
            "exit_reason": ["TARGET", "TARGET", "STOP"],
            "slippage_paid": [0.0, 0.0, 0.0],
            "commission_paid": [0.0, 0.0, 0.0],
        }
    )

    result = calculate_trade_metrics(trades_df)

    assert result["total_trades"] == 3
    assert result["win_count"] == 3
    assert result["loss_count"] == 0
    assert result["scratch_count"] == 0
    assert result["win_rate"] == 1.0
    assert result["profit_factor"] == "inf"  # JSON-safe infinite (no losses)
    assert result["total_realized_r"] == 7.5
    assert result["avg_r"] == 2.5
    assert result["median_r"] == 2.5
    assert result["expectancy_r"] == 2.5
    assert result["avg_win_dollars"] == 150.0
    assert result["avg_loss_dollars"] == 0.0
    assert result["payoff_ratio"] == "inf"  # no losing trades
    assert result["best_trade_dollars"] == 200.0
    assert result["worst_trade_dollars"] == 100.0
    assert result["best_trade_r"] == 3.0
    assert result["worst_trade_r"] == 2.0
    assert result["exit_reasons"]["TARGET"] == 2
    assert result["exit_reasons"]["STOP"] == 1
    assert result["total_pnl_dollars"] == 450.0


def test_calculate_trade_metrics_all_losses():
    """Test trade metrics calculation with all losing trades."""
    trades_df = pd.DataFrame(
        {
            "direction": ["LONG", "SHORT", "LONG"],
            "pnl_dollars": [-100.0, -200.0, -150.0],
            "r_multiple": [-2.0, -3.0, -2.5],
            "exit_reason": ["STOP", "STOP", "EOD"],
            "slippage_paid": [0.0, 0.0, 0.0],
            "commission_paid": [0.0, 0.0, 0.0],
        }
    )

    result = calculate_trade_metrics(trades_df)

    assert result["total_trades"] == 3
    assert result["win_count"] == 0
    assert result["loss_count"] == 3
    assert result["scratch_count"] == 0
    assert result["win_rate"] == 0.0
    assert result["profit_factor"] == 0.0
    assert result["total_realized_r"] == -7.5
    assert result["avg_r"] == -2.5
    assert result["median_r"] == -2.5
    assert result["expectancy_r"] == -2.5
    assert result["avg_win_dollars"] == 0.0
    assert result["avg_loss_dollars"] == 150.0
    assert result["payoff_ratio"] == 0.0
    assert result["best_trade_dollars"] == -100.0
    assert result["worst_trade_dollars"] == -200.0
    assert result["best_trade_r"] == -2.0
    assert result["worst_trade_r"] == -3.0
    assert result["exit_reasons"]["STOP"] == 2
    assert result["exit_reasons"]["EOD"] == 1
    assert result["total_pnl_dollars"] == -450.0


def test_calculate_trade_metrics_mixed():
    """Test trade metrics calculation with mixed win/loss trades."""
    trades_df = pd.DataFrame(
        {
            "direction": ["LONG", "SHORT", "LONG", "SHORT"],
            "pnl_dollars": [100.0, -50.0, 200.0, -100.0],
            "r_multiple": [2.0, -1.0, 4.0, -2.0],
            "exit_reason": ["TARGET", "STOP", "TARGET", "EOD"],
            "slippage_paid": [0.0, 0.0, 0.0, 0.0],
            "commission_paid": [0.0, 0.0, 0.0, 0.0],
        }
    )

    result = calculate_trade_metrics(trades_df)

    assert result["total_trades"] == 4
    assert result["win_count"] == 2
    assert result["loss_count"] == 2
    assert result["scratch_count"] == 0
    assert result["win_rate"] == 0.5
    assert result["profit_factor"] == 2.0  # (100 + 200) / (50 + 100) = 300 / 150 = 2.0
    assert result["total_realized_r"] == 3.0
    assert result["avg_r"] == 0.75
    assert result["median_r"] == 0.5  # median(-2, -1, 2, 4) = (-1 + 2) / 2
    assert (
        result["expectancy_r"] == 0.75
    )  # (0.5 * 3.0) - (0.5 * 1.5) = 1.5 - 0.75 = 0.75
    assert result["avg_win_dollars"] == 150.0
    assert result["avg_loss_dollars"] == 75.0
    assert result["payoff_ratio"] == 2.0
    assert result["best_trade_dollars"] == 200.0
    assert result["worst_trade_dollars"] == -100.0
    assert result["best_trade_r"] == 4.0
    assert result["worst_trade_r"] == -2.0
    assert result["exit_reasons"]["TARGET"] == 2
    assert result["exit_reasons"]["STOP"] == 1
    assert result["exit_reasons"]["EOD"] == 1
    assert result["total_pnl_dollars"] == 150.0


def test_calculate_trade_metrics_scratch_trades():
    """Test trade metrics calculation with scratch trades."""
    trades_df = pd.DataFrame(
        {
            "direction": ["LONG", "SHORT", "LONG", "SHORT"],
            "pnl_dollars": [100.0, 0.0, 200.0, -100.0],
            "r_multiple": [2.0, 0.0, 4.0, -2.0],
            "exit_reason": ["TARGET", "STOP", "TARGET", "EOD"],
            "slippage_paid": [0.0, 0.0, 0.0, 0.0],
            "commission_paid": [0.0, 0.0, 0.0, 0.0],
        }
    )

    result = calculate_trade_metrics(trades_df)

    assert result["total_trades"] == 4
    assert result["win_count"] == 2
    assert result["loss_count"] == 1
    assert result["scratch_count"] == 1
    assert result["win_rate"] == 0.5
    assert result["profit_factor"] == 3.0  # (100 + 200) / (100) = 300 / 100 = 3.0
    assert result["total_realized_r"] == 4.0
    assert result["avg_r"] == 1.0
    assert result["median_r"] == 1.0  # median(-2, 0, 2, 4) = (0 + 2) / 2
    assert (
        result["expectancy_r"] == 0.5
    )  # E_R = W*avg_win - (1-W)*avg_loss = 0.5*3.0 - 0.5*2.0
    assert result["avg_win_dollars"] == 150.0
    assert result["avg_loss_dollars"] == 100.0
    assert result["payoff_ratio"] == 1.5
    assert result["best_trade_dollars"] == 200.0
    assert result["worst_trade_dollars"] == -100.0
    assert result["best_trade_r"] == 4.0
    assert result["worst_trade_r"] == -2.0
    assert result["exit_reasons"]["TARGET"] == 2
    assert result["exit_reasons"]["STOP"] == 1
    assert result["exit_reasons"]["EOD"] == 1
    assert result["total_pnl_dollars"] == 200.0


def test_calculate_portfolio_metrics_empty_dataframe():
    """Test portfolio metrics calculation with empty DataFrame."""
    equity_df = pd.DataFrame()

    result = calculate_portfolio_metrics(equity_df)

    expected = {
        "initial_capital": 100000.0,
        "ending_equity": 100000.0,
        "total_return_pct": 0.0,
        "cagr_pct": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_dollars": 0.0,
        "max_drawdown_duration_bars": 0,
        "calmar_ratio": 0.0,
    }

    assert result == expected


def test_calculate_portfolio_metrics_single_equity():
    """Test portfolio metrics calculation with single equity value."""
    equity_df = pd.DataFrame({"equity": [100000.0]})

    result = calculate_portfolio_metrics(equity_df)

    expected = {
        "initial_capital": 100000.0,
        "ending_equity": 100000.0,
        "total_return_pct": 0.0,
        "cagr_pct": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_dollars": 0.0,
        "max_drawdown_duration_bars": 0,
        "calmar_ratio": 0.0,
    }

    assert result == expected


def test_calculate_portfolio_metrics_with_drawdown():
    """Test portfolio metrics calculation with drawdown."""
    equity_df = pd.DataFrame(
        {
            "equity": [
                100000.0,
                110000.0,
                120000.0,
                110000.0,
                100000.0,
                90000.0,
                100000.0,
            ]
        }
    )

    result = calculate_portfolio_metrics(equity_df)

    # Check some key values
    assert result["initial_capital"] == 100000.0
    assert result["ending_equity"] == 100000.0
    assert result["total_return_pct"] == 0.0
    # Running-peak definition: peak 120k (idx2) -> trough 90k (idx5).
    assert result["max_drawdown_pct"] == 25.0
    assert result["max_drawdown_dollars"] == 30000.0
    assert result["max_drawdown_duration_bars"] == 4  # dd > 0 on idx3..idx6


def test_calculate_portfolio_metrics_with_cagr():
    """Test portfolio metrics calculation with CAGR."""
    equity_df = pd.DataFrame(
        {
            "equity": [
                100000.0,
                110000.0,
                121000.0,
                133100.0,
                146410.0,
            ]  # 10% annual growth over 4 years
        }
    )

    result = calculate_portfolio_metrics(equity_df)

    # Check some key values
    assert result["initial_capital"] == 100000.0
    assert result["ending_equity"] == 146410.0
    assert result["total_return_pct"] == 46.41
    # CAGR should be approximately 10%


def test_generate_performance_report():
    """Test complete performance report generation."""
    trades_df = pd.DataFrame(
        {
            "direction": ["LONG", "SHORT", "LONG"],
            "pnl_dollars": [100.0, -50.0, 200.0],
            "r_multiple": [2.0, -1.0, 4.0],
            "exit_reason": ["TARGET", "STOP", "TARGET"],
            "slippage_paid": [0.0, 0.0, 0.0],
            "commission_paid": [0.0, 0.0, 0.0],
        }
    )

    equity_df = pd.DataFrame({"equity": [100000.0, 105000.0, 110000.0]})

    config = AppConfig(
        schema_version="1.0",
        strategy=StrategyConfig(
            ticker="SPY",
            opening_range_minutes=30,
            target_r=2.0,
            max_trades_per_day=5,
        ),
        filters=FiltersConfig(),
        data=DataConfig(),
        execution=ExecutionConfig(initial_capital=100000.0),
        output=OutputConfig(),
    )

    result = generate_performance_report(trades_df, equity_df, config)

    # Check structure
    assert "strategy" in result
    assert "trade_metrics" in result
    assert "portfolio_metrics" in result

    # Check strategy section
    assert result["strategy"]["ticker"] == "SPY"
    assert result["strategy"]["opening_range_minutes"] == 30
    assert result["strategy"]["target_r"] == 2.0
    assert result["strategy"]["max_trades_per_day"] == 5

    # Check trade metrics
    assert result["trade_metrics"]["total_trades"] == 3
    assert result["trade_metrics"]["win_count"] == 2
    assert result["trade_metrics"]["loss_count"] == 1

    # Check portfolio metrics
    assert result["portfolio_metrics"]["initial_capital"] == 100000.0
    assert result["portfolio_metrics"]["ending_equity"] == 110000.0
