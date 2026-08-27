"""
src.visualization.performance_plotter
=====================================
Portfolio Performance Curves & Distribution Visualizer for SPY ORB.

Generates high-resolution PNG charts:
1. Cumulative Equity Curve vs. Benchmark
2. Underwater Peak-to-Trough Drawdown Profile
3. R-Multiple Distribution Histogram & Expectancy Breakdown
4. Trade Holding Duration Distribution
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Union
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from src.backtest.engine import BacktestResult
from src.common.logger import get_logger
from src.common.time_utils import to_eastern, ensure_eastern, EASTERN_TZ

logger = get_logger(__name__)


class PerformancePlotter:
    """Renders portfolio equity curves, drawdowns, and trade distribution charts."""

    def __init__(
        self,
        plots_base_dir: Optional[Union[str, Path]] = None,
        equity_dir: Optional[Path] = None,
        drawdown_dir: Optional[Path] = None,
        distributions_dir: Optional[Path] = None,
    ) -> None:
        """Initialise the PerformancePlotter.

        Accepts either individual pre-resolved directory paths (from
        PathManager) or a single base directory string (legacy).

        Args:
            plots_base_dir: Legacy base directory. Used when individual dirs
                are not provided.
            equity_dir: Pre-resolved equity curves directory.
            drawdown_dir: Pre-resolved drawdowns directory.
            distributions_dir: Pre-resolved distributions directory.
        """
        base = Path(plots_base_dir or "plots")
        self.equity_dir = (
            equity_dir if equity_dir is not None else base / "equity_curves"
        )
        self.dd_dir = drawdown_dir if drawdown_dir is not None else base / "drawdowns"
        self.dist_dir = (
            distributions_dir
            if distributions_dir is not None
            else base / "distributions"
        )

        for d in (self.equity_dir, self.dd_dir, self.dist_dir):
            d.mkdir(parents=True, exist_ok=True)

    def plot_equity_curve(
        self,
        equity_df: pd.DataFrame,
        metrics: Dict[str, Any],
        filename: str = "equity_curve.png",
    ) -> Path:
        """Plots cumulative equity curve with key metrics summary callout."""
        out_path = self.equity_dir / filename
        if equity_df.empty or "equity" not in equity_df.columns:
            logger.warning("Empty equity DataFrame passed to plot_equity_curve.")
            return out_path

        fig, ax = plt.subplots(figsize=(12, 6))

        df = (
            to_eastern(equity_df, time_col="timestamp")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        timestamps = df["timestamp"]
        equity = df["equity"].values
        init_cap = equity[0] if len(equity) > 0 else 100_000.0

        # Plot Strategy Equity
        ax.plot(
            timestamps,
            equity,
            label="ORB Strategy Equity",
            color="#1976d2",
            linewidth=2.0,
        )
        ax.axhline(
            init_cap,
            color="#9e9e9e",
            linestyle="--",
            alpha=0.7,
            label=f"Initial Capital (${init_cap:,.0f})",
        )

        # Fill positive / negative area from initial capital
        ax.fill_between(
            timestamps,
            equity,
            init_cap,
            where=(equity >= init_cap),
            color="#4caf50",
            alpha=0.15,
        )
        ax.fill_between(
            timestamps,
            equity,
            init_cap,
            where=(equity < init_cap),
            color="#f44336",
            alpha=0.15,
        )

        # Title and Labels
        ax.set_title(
            "SPY Opening Range Breakout (ORB) — Cumulative Equity Curve",
            fontsize=13,
            fontweight="bold",
            pad=12,
        )
        ax.set_ylabel("Portfolio Equity ($ USD)", fontsize=11)
        ax.grid(True, linestyle=":", alpha=0.5)

        # Summary Metrics Box
        tm = metrics.get("trade_metrics", {})
        pm = metrics.get("portfolio_metrics", {})

        metrics_text = (
            f"Total Return: {pm.get('total_return_pct', 0.0):+.2f}%\n"
            f"Sharpe Ratio: {pm.get('sharpe_ratio', 0.0):.2f}\n"
            f"Max Drawdown: {pm.get('max_drawdown_pct', 0.0):.2f}%\n"
            f"Win Rate:     {tm.get('win_rate', 0.0)*100:.1f}%\n"
            f"Total Realized R: {tm.get('total_realized_r', 0.0):+.2f}R\n"
            f"Expectancy:   {tm.get('expectancy_r', 0.0):+.2f}R"
        )

        props = dict(
            boxstyle="round,pad=0.6",
            facecolor="#ffffff",
            edgecolor="#bdbdbd",
            alpha=0.9,
        )
        ax.text(
            0.02,
            0.95,
            metrics_text,
            transform=ax.transAxes,
            fontsize=9.5,
            family="monospace",
            verticalalignment="top",
            bbox=props,
        )

        ax.legend(loc="lower right", fontsize=9, framealpha=0.9)
        plt.tight_layout()
        plt.savefig(out_path, dpi=150)
        plt.close(fig)

        logger.info("Saved equity curve chart to %s", out_path)
        return out_path

    def plot_drawdowns(
        self,
        equity_df: pd.DataFrame,
        filename: str = "drawdown_curve.png",
    ) -> Path:
        """Plots underwater percentage drawdown profile."""
        out_path = self.dd_dir / filename
        if equity_df.empty or "equity" not in equity_df.columns:
            return out_path

        df = (
            to_eastern(equity_df, time_col="timestamp")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        timestamps = df["timestamp"]
        equity = df["equity"].values

        hwm = np.maximum.accumulate(equity)
        drawdown_pct = -((hwm - equity) / hwm) * 100.0

        fig, ax = plt.subplots(figsize=(12, 5))

        ax.plot(
            timestamps, drawdown_pct, color="#d32f2f", linewidth=1.5, label="Drawdown %"
        )
        ax.fill_between(timestamps, drawdown_pct, 0, color="#ef5350", alpha=0.3)

        max_dd = float(np.min(drawdown_pct)) if len(drawdown_pct) > 0 else 0.0
        ax.axhline(
            max_dd,
            color="#b71c1c",
            linestyle="--",
            alpha=0.7,
            label=f"Max Drawdown ({max_dd:.2f}%)",
        )

        ax.set_title(
            "Portfolio Underwater Drawdown Profile",
            fontsize=13,
            fontweight="bold",
            pad=12,
        )
        ax.set_ylabel("Drawdown (%)", fontsize=11)
        ax.set_ylim([min(max_dd * 1.15, -1.0), 0.5])
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(loc="lower left", fontsize=9, framealpha=0.9)

        plt.tight_layout()
        plt.savefig(out_path, dpi=150)
        plt.close(fig)

        logger.info("Saved drawdown chart to %s", out_path)
        return out_path

    def plot_r_distribution(
        self,
        trades_df: pd.DataFrame,
        filename: str = "r_multiples.png",
    ) -> Path:
        """Plots histogram of realized trade R-multiples."""
        out_path = self.dist_dir / filename
        if trades_df.empty or "r_multiple" not in trades_df.columns:
            return out_path

        r_values = trades_df["r_multiple"].values

        fig, ax = plt.subplots(figsize=(10, 5))

        # Color bins: red for negative R, green for positive R
        bins = np.linspace(
            min(r_values.min() - 0.5, -2.0), max(r_values.max() + 0.5, 3.0), 25
        )
        n, bins, patches_list = ax.hist(
            r_values, bins=bins, edgecolor="#37474f", alpha=0.8
        )

        for patch, left_side in zip(patches_list, bins[:-1]):
            if left_side < 0:
                patch.set_facecolor("#ef5350")
            else:
                patch.set_facecolor("#66bb6a")

        avg_r = float(np.mean(r_values)) if len(r_values) > 0 else 0.0
        ax.axvline(0.0, color="#424242", linestyle="-", linewidth=1.2)
        ax.axvline(
            avg_r,
            color="#1976d2",
            linestyle="--",
            linewidth=1.8,
            label=f"Mean R ({avg_r:+.2f}R)",
        )

        ax.set_title(
            "Realized Trade R-Multiple Distribution",
            fontsize=13,
            fontweight="bold",
            pad=12,
        )
        ax.set_xlabel("Realized R-Multiple", fontsize=11)
        ax.set_ylabel("Trade Frequency", fontsize=11)
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(loc="upper right", fontsize=9, framealpha=0.9)

        plt.tight_layout()
        plt.savefig(out_path, dpi=150)
        plt.close(fig)

        logger.info("Saved R-distribution chart to %s", out_path)
        return out_path

    def plot_trade_durations(
        self,
        trades_df: pd.DataFrame,
        filename: str = "trade_durations.png",
    ) -> Path:
        """Plots distribution of trade holding durations in minutes."""
        out_path = self.dist_dir / filename
        if (
            trades_df.empty
            or "entry_time" not in trades_df.columns
            or "exit_time" not in trades_df.columns
        ):
            return out_path

        t_df = to_eastern(trades_df, time_col="entry_time")
        t_df = to_eastern(t_df, time_col="exit_time")
        entry_t = t_df["entry_time"]
        exit_t = t_df["exit_time"]
        durations_mins = (exit_t - entry_t).dt.total_seconds() / 60.0

        fig, ax = plt.subplots(figsize=(10, 5))

        ax.hist(
            durations_mins, bins=20, color="#7e57c2", edgecolor="#311b92", alpha=0.75
        )
        mean_dur = float(np.mean(durations_mins)) if len(durations_mins) > 0 else 0.0
        ax.axvline(
            mean_dur,
            color="#ff7043",
            linestyle="--",
            linewidth=1.8,
            label=f"Mean Duration ({mean_dur:.1f} min)",
        )

        ax.set_title(
            "Trade Holding Duration Distribution",
            fontsize=13,
            fontweight="bold",
            pad=12,
        )
        ax.set_xlabel("Holding Duration (Minutes)", fontsize=11)
        ax.set_ylabel("Number of Trades", fontsize=11)
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(loc="upper right", fontsize=9, framealpha=0.9)

        plt.tight_layout()
        plt.savefig(out_path, dpi=150)
        plt.close(fig)

        logger.info("Saved trade durations chart to %s", out_path)
        return out_path

    def generate_all_plots(
        self,
        backtest_result: BacktestResult,
        metrics: Dict[str, Any],
    ) -> Dict[str, Path]:
        """Generates all 4 portfolio performance visual charts."""
        paths: Dict[str, Path] = {}
        paths["equity_curve"] = self.plot_equity_curve(
            backtest_result.equity_curve, metrics
        )
        paths["drawdown_curve"] = self.plot_drawdowns(backtest_result.equity_curve)
        paths["r_multiples"] = self.plot_r_distribution(backtest_result.trades_df)
        paths["trade_durations"] = self.plot_trade_durations(backtest_result.trades_df)
        return paths
