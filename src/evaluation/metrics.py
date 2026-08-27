"""
src.evaluation.metrics
======================
Quantitative performance, risk-adjusted metrics, and statistical engine for SPY ORB.

Calculates win/loss statistics, profit factors, R-multiples, expectancy, Sharpe,
Sortino, Calmar, and high-water mark drawdowns matching the Section 5.2 metrics schema.
"""

from __future__ import annotations

import math
from typing import Any, Dict
import numpy as np
import pandas as pd

from src.common.config import AppConfig


def calculate_trade_metrics(trades_df: pd.DataFrame) -> Dict[str, Any]:
    """Computes statistical trade metrics from closed trades log."""
    if trades_df.empty or len(trades_df) == 0:
        return {
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

    total_trades = len(trades_df)
    long_trades = int((trades_df["direction"] == "LONG").sum())
    short_trades = int((trades_df["direction"] == "SHORT").sum())

    pnls = trades_df["pnl_dollars"].values
    r_multiples = trades_df["r_multiple"].values

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    scratches = pnls[pnls == 0]

    win_count = len(wins)
    loss_count = len(losses)
    scratch_count = len(scratches)
    win_rate = (win_count / total_trades) if total_trades > 0 else 0.0

    gross_profits = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_losses = float(abs(losses.sum())) if len(losses) > 0 else 0.0

    if gross_losses > 0:
        profit_factor = gross_profits / gross_losses
    elif gross_profits > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    total_realized_r = float(np.sum(r_multiples))
    avg_r = float(np.mean(r_multiples)) if len(r_multiples) > 0 else 0.0
    median_r = float(np.median(r_multiples)) if len(r_multiples) > 0 else 0.0
    std_r = float(np.std(r_multiples, ddof=1)) if len(r_multiples) > 1 else 0.0

    # Mathematical Expectancy: E_R = W * avg_win_R - (1 - W) * avg_loss_R
    r_wins = r_multiples[r_multiples > 0]
    r_losses = r_multiples[r_multiples < 0]
    avg_r_win = float(np.mean(r_wins)) if len(r_wins) > 0 else 0.0
    avg_r_loss = float(abs(np.mean(r_losses))) if len(r_losses) > 0 else 0.0

    expectancy_r = (win_rate * avg_r_win) - ((1.0 - win_rate) * avg_r_loss)

    avg_win_dollars = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss_dollars = float(abs(np.mean(losses))) if len(losses) > 0 else 0.0
    payoff_ratio = (avg_win_dollars / avg_loss_dollars) if avg_loss_dollars > 0 else 0.0

    best_trade_dollars = float(np.max(pnls)) if len(pnls) > 0 else 0.0
    worst_trade_dollars = float(np.min(pnls)) if len(pnls) > 0 else 0.0
    best_trade_r = float(np.max(r_multiples)) if len(r_multiples) > 0 else 0.0
    worst_trade_r = float(np.min(r_multiples)) if len(r_multiples) > 0 else 0.0

    # Exit reason counts
    exit_counts = trades_df["exit_reason"].value_counts().to_dict()
    exit_reasons = {
        "TARGET": int(exit_counts.get("TARGET", 0)),
        "STOP": int(exit_counts.get("STOP", 0)),
        "EOD": int(exit_counts.get("EOD", 0)),
    }

    return {
        "total_trades": total_trades,
        "long_trades": long_trades,
        "short_trades": short_trades,
        "win_count": win_count,
        "loss_count": loss_count,
        "scratch_count": scratch_count,
        "win_rate": round(win_rate, 4),
        "profit_factor": (
            round(profit_factor, 4) if not math.isinf(profit_factor) else "inf"
        ),
        "total_realized_r": round(total_realized_r, 4),
        "avg_r": round(avg_r, 4),
        "median_r": round(median_r, 4),
        "std_r": round(std_r, 4),
        "expectancy_r": round(expectancy_r, 4),
        "avg_win_dollars": round(avg_win_dollars, 2),
        "avg_loss_dollars": round(avg_loss_dollars, 2),
        "payoff_ratio": round(payoff_ratio, 4),
        "best_trade_dollars": round(best_trade_dollars, 2),
        "worst_trade_dollars": round(worst_trade_dollars, 2),
        "best_trade_r": round(best_trade_r, 4),
        "worst_trade_r": round(worst_trade_r, 4),
        "exit_reasons": exit_reasons,
        "total_pnl_dollars": round(float(pnls.sum()), 2),
        "total_slippage_dollars": round(float(trades_df["slippage_paid"].sum()), 2),
        "total_commission_dollars": round(float(trades_df["commission_paid"].sum()), 2),
    }


def calculate_portfolio_metrics(
    equity_df: pd.DataFrame,
    initial_capital: float = 100_000.0,
    risk_free_rate: float = 0.0,
) -> Dict[str, Any]:
    """Computes time-series risk metrics (Sharpe, Sortino, MDD, Calmar, CAGR)."""
    if equity_df.empty or "equity" not in equity_df.columns:
        return {
            "initial_capital": initial_capital,
            "ending_equity": initial_capital,
            "total_return_pct": 0.0,
            "cagr_pct": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_dollars": 0.0,
            "max_drawdown_duration_bars": 0,
            "calmar_ratio": 0.0,
        }

    ending_equity = float(equity_df["equity"].iloc[-1])
    total_return_pct = ((ending_equity - initial_capital) / initial_capital) * 100.0

    # Calculate Drawdown series
    equity_series = equity_df["equity"].values
    hwm = np.maximum.accumulate(equity_series)
    drawdowns_dollars = hwm - equity_series
    drawdowns_pct = (drawdowns_dollars / hwm) * 100.0

    max_drawdown_pct = float(np.max(drawdowns_pct)) if len(drawdowns_pct) > 0 else 0.0
    max_drawdown_dollars = (
        float(np.max(drawdowns_dollars)) if len(drawdowns_dollars) > 0 else 0.0
    )

    # Drawdown duration calculation (in bars)
    mdd_duration_bars = 0
    current_duration = 0
    for dd in drawdowns_pct:
        if dd > 0:
            current_duration += 1
            if current_duration > mdd_duration_bars:
                mdd_duration_bars = current_duration
        else:
            current_duration = 0

    # Daily returns from session close equity
    if "session_id" in equity_df.columns:
        daily_equity = equity_df.groupby("session_id")["equity"].last()
    else:
        daily_equity = equity_df["equity"]

    daily_returns = daily_equity.pct_change().dropna().values
    n_days = len(daily_equity)

    # Annualization factor (252 trading days)
    trading_days_per_year = 252.0

    if n_days > 1:
        years = max(n_days / trading_days_per_year, 1.0 / trading_days_per_year)
        cagr_pct = (((ending_equity / initial_capital) ** (1.0 / years)) - 1.0) * 100.0
    else:
        cagr_pct = total_return_pct

    # Sharpe & Sortino ratios based on daily returns
    if len(daily_returns) > 1:
        daily_rf = risk_free_rate / trading_days_per_year
        excess_returns = daily_returns - daily_rf
        mean_excess = np.mean(excess_returns)
        std_returns = np.std(daily_returns, ddof=1)

        sharpe_ratio = (
            float((mean_excess / std_returns) * math.sqrt(trading_days_per_year))
            if std_returns > 0
            else 0.0
        )

        downside_returns = daily_returns[daily_returns < daily_rf]
        if len(downside_returns) > 0:
            downside_std = np.sqrt(np.mean((downside_returns - daily_rf) ** 2))
            sortino_ratio = (
                float((mean_excess / downside_std) * math.sqrt(trading_days_per_year))
                if downside_std > 0
                else 0.0
            )
        else:
            sortino_ratio = float("inf") if mean_excess > 0 else 0.0
    else:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0

    # Calmar Ratio: CAGR / Max Drawdown
    if max_drawdown_pct > 0:
        calmar_ratio = cagr_pct / max_drawdown_pct
    elif cagr_pct > 0:
        calmar_ratio = float("inf")
    else:
        calmar_ratio = 0.0

    return {
        "initial_capital": round(initial_capital, 2),
        "ending_equity": round(ending_equity, 2),
        "total_return_pct": round(total_return_pct, 4),
        "cagr_pct": round(cagr_pct, 4),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "sortino_ratio": (
            round(sortino_ratio, 4) if not math.isinf(sortino_ratio) else "inf"
        ),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "max_drawdown_dollars": round(max_drawdown_dollars, 2),
        "max_drawdown_duration_bars": int(mdd_duration_bars),
        "calmar_ratio": (
            round(calmar_ratio, 4) if not math.isinf(calmar_ratio) else "inf"
        ),
    }


def generate_performance_report(
    trades_df: pd.DataFrame,
    equity_df: pd.DataFrame,
    config: AppConfig,
) -> Dict[str, Any]:
    """Generates complete hierarchical performance dictionary matching metrics.json schema."""
    trade_metrics = calculate_trade_metrics(trades_df)
    portfolio_metrics = calculate_portfolio_metrics(
        equity_df=equity_df,
        initial_capital=config.execution.initial_capital,
    )

    return {
        "strategy": {
            "ticker": config.strategy.ticker,
            "opening_range_minutes": config.strategy.opening_range_minutes,
            "target_r": config.strategy.target_r,
            "max_trades_per_day": config.strategy.max_trades_per_day,
        },
        "trade_metrics": trade_metrics,
        "portfolio_metrics": portfolio_metrics,
    }
