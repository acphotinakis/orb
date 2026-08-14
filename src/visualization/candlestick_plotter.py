"""
src.visualization.candlestick_plotter
=====================================
Intraday Candlestick Trade Visualizer for SPY ORB.

Generates high-resolution session charts highlighting the Opening Range box,
entry/exit points, Stop Loss, Take Profit lines, and trade outcome annotations.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import pandas as pd
import numpy as np

from src.backtest.models import Trade
from src.common.logger import get_logger

logger = get_logger(__name__)


class CandlestickTradePlotter:
    """Renders session candlestick charts with OR boxes and execution markers."""

    def __init__(self, output_dir: str = "plots/trades") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def plot_trade_session(
        self,
        trade: Trade,
        session_df: pd.DataFrame,
        filename: Optional[str] = None,
    ) -> Path:
        """Plots single session candlestick chart with complete trade annotations.

        Args:
            trade: Executed Trade object
            session_df: 1-minute bars for that session
            filename: Optional custom filename

        Returns:
            Path to saved PNG file.
        """
        # Ensure session data is sorted and indexed cleanly
        df = session_df.copy().sort_values("timestamp").reset_index(drop=True)
        if df.empty:
            raise ValueError(f"Empty session DataFrame provided for trade {trade.trade_id}")

        fig, (ax, ax_vol) = plt.subplots(
            2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]}, sharex=True
        )

        n_bars = len(df)
        indices = np.arange(n_bars)

        # Plot Candlesticks
        width = 0.6
        width2 = 0.1

        up = df[df.close >= df.open]
        down = df[df.close < df.open]

        # Up candles
        ax.bar(up.index, up.close - up.open, width, bottom=up.open, color="#26a69a", edgecolor="#26a69a")
        ax.bar(up.index, up.high - up.close, width2, bottom=up.close, color="#26a69a")
        ax.bar(up.index, up.low - up.open, width2, bottom=up.open, color="#26a69a")

        # Down candles
        ax.bar(down.index, down.close - down.open, width, bottom=down.open, color="#ef5350", edgecolor="#ef5350")
        ax.bar(down.index, down.high - down.open, width2, bottom=down.open, color="#ef5350")
        ax.bar(down.index, down.low - down.close, width2, bottom=down.close, color="#ef5350")

        # Volume bars
        ax_vol.bar(up.index, up.volume, width, color="#26a69a", alpha=0.6)
        ax_vol.bar(down.index, down.volume, width, color="#ef5350", alpha=0.6)
        ax_vol.set_ylabel("Volume", fontsize=10)

        # 1. Opening Range High/Low lines & shaded box (first 15 bars: 0..14)
        or_mask = df["is_opening_range"] == True if "is_opening_range" in df.columns else (df.index < 15)
        or_indices = df[or_mask].index
        if len(or_indices) > 0:
            or_start_idx = or_indices[0]
            or_end_idx = or_indices[-1]
            rect = patches.Rectangle(
                (or_start_idx - 0.5, trade.or_low),
                (or_end_idx - or_start_idx + 1),
                trade.or_width,
                linewidth=1,
                edgecolor="#1976d2",
                facecolor="#bbdefb",
                alpha=0.3,
                label=f"Opening Range ({trade.or_low:.2f} - {trade.or_high:.2f})",
            )
            ax.add_patch(rect)

        # Horizontal level lines across the session
        ax.axhline(trade.or_high, color="#1976d2", linestyle="--", alpha=0.6, label="OR High")
        ax.axhline(trade.or_low, color="#1976d2", linestyle="--", alpha=0.6, label="OR Low")
        ax.axhline(trade.entry_price, color="#212121", linestyle="-", alpha=0.8, label=f"Entry (${trade.entry_price:.2f})")
        ax.axhline(trade.stop_price, color="#d32f2f", linestyle="--", alpha=0.8, label=f"Stop Loss (${trade.stop_price:.2f})")
        ax.axhline(trade.target_price, color="#388e3c", linestyle="--", alpha=0.8, label=f"Target (${trade.target_price:.2f})")

        # 2. Annotate Entry Point
        entry_matches = df[df["timestamp"] == trade.entry_time].index
        entry_idx = entry_matches[0] if len(entry_matches) > 0 else 15
        entry_color = "#2e7d32" if trade.direction == "LONG" else "#c62828"
        entry_marker = "^" if trade.direction == "LONG" else "v"

        ax.scatter(
            [entry_idx],
            [trade.entry_price],
            color=entry_color,
            marker=entry_marker,
            s=120,
            zorder=5,
            label=f"{trade.direction} Entry",
        )

        # 3. Annotate Exit Point
        exit_matches = df[df["timestamp"] == trade.exit_time].index
        exit_idx = exit_matches[0] if len(exit_matches) > 0 else (n_bars - 1)
        ax.scatter(
            [exit_idx],
            [trade.exit_price],
            color="#f57c00",
            marker="X",
            s=120,
            zorder=5,
            label=f"Exit ({trade.exit_reason})",
        )

        # Connect entry to exit
        ax.plot(
            [entry_idx, exit_idx],
            [trade.entry_price, trade.exit_price],
            color="#424242",
            linestyle=":",
            alpha=0.6,
        )

        # Title & Outcome banner
        is_win = trade.pnl_dollars > 0
        outcome_color = "#1b5e20" if is_win else "#b71c1c"
        outcome_text = f"{'WIN' if is_win else 'LOSS'} ({trade.r_multiple:+.2f}R | ${trade.pnl_dollars:+,.2f})"

        ax.set_title(
            f"Trade #{trade.trade_id} [{trade.date}] {trade.symbol} {trade.direction} — {outcome_text}",
            fontsize=13,
            fontweight="bold",
            color=outcome_color,
            pad=12,
        )

        # Format X-ticks with intraday ET times
        step = max(1, n_bars // 8)
        tick_indices = list(range(0, n_bars, step))
        if (n_bars - 1) not in tick_indices:
            tick_indices.append(n_bars - 1)

        tick_labels = [
            df.iloc[i]["timestamp"].strftime("%H:%M")
            for i in tick_indices
        ]
        ax_vol.set_xticks(tick_indices)
        ax_vol.set_xticklabels(tick_labels, rotation=0, fontsize=9)

        ax.grid(True, linestyle=":", alpha=0.4)
        ax_vol.grid(True, linestyle=":", alpha=0.4)
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)

        plt.tight_layout()

        # Save plot
        if filename is None:
            filename = f"trade_{trade.trade_id:03d}_{trade.date}_{trade.direction}.png"
        out_path = self.output_dir / filename
        plt.savefig(out_path, dpi=150)
        plt.close(fig)

        logger.info("Saved trade chart to %s", out_path)
        return out_path

    def plot_all_trades(
        self,
        trades: List[Trade],
        processed_bars: pd.DataFrame,
        max_plots: Optional[int] = None,
    ) -> List[Path]:
        """Generates trade charts for all executed trades."""
        output_paths: List[Path] = []
        trades_to_plot = trades[:max_plots] if max_plots else trades

        grouped = processed_bars.groupby("session_id")

        for trade in trades_to_plot:
            if trade.date in grouped.groups:
                session_df = grouped.get_group(trade.date)
                p = self.plot_trade_session(trade, session_df)
                output_paths.append(p)
            else:
                logger.warning("Session data for trade date %s not found.", trade.date)

        return output_paths
