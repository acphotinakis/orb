"""
src.visualization.candlestick_data_plotter
==========================================
Candlestick Data Visualizer for raw/processed market data.

Renders high-resolution OHLCV candlestick charts for individual trading sessions
or multi-day date ranges without requiring executed Trade objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union
import matplotlib

matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import pandas as pd
import numpy as np

from src.common.logger import get_logger

logger = get_logger(__name__)


class CandlestickDataPlotter:
    """Renders OHLCV candlestick charts with volume bars for market data."""

    def __init__(
        self,
        output_dir: Optional[Union[str, Path]] = None,
        filename: Optional[str] = None,
    ) -> None:
        """Initialise the CandlestickDataPlotter.

        Args:
            output_dir: Directory for candlestick chart PNGs. When injected
                from PathManager, this should be a pre-resolved :class:`Path`.
                Defaults to ``"plots/candlesticks"``.
            filename: Optional default filename stem for saved charts.
        """
        self.output_dir = (
            Path(output_dir) if output_dir is not None else Path("plots/candlesticks")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.filename = filename

    def plot_session(
        self,
        df: pd.DataFrame,
        symbol: str = "SPY",
        title: Optional[str] = None,
        filename: Optional[str] = None,
        highlight_opening_range: bool = True,
    ) -> Path:
        """Plots a single session candlestick chart with volume and saves to PNG file.

        Args:
            df: DataFrame containing at least ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
            symbol: Ticker symbol. Defaults to "SPY".
            title: Optional custom plot title.
            filename: Optional custom output filename. If None, uses instance default or auto-generated name.
            highlight_opening_range: If True and 'is_opening_range' column exists, highlights the OR window.

        Returns:
            Path to saved PNG file.
        """
        data = df.copy().sort_values("timestamp").reset_index(drop=True)
        if data.empty:
            raise ValueError(f"Empty DataFrame provided for {symbol} candlestick plot.")

        required_cols = ["timestamp", "open", "high", "low", "close"]
        for col in required_cols:
            if col not in data.columns:
                raise ValueError(
                    f"Missing required column '{col}' for candlestick plot."
                )

        fig, (ax, ax_vol) = plt.subplots(
            2,
            1,
            figsize=(14, 8),
            gridspec_kw={"height_ratios": [3, 1]},
            sharex=True,
            dpi=150,
        )

        n_bars = len(data)
        indices = np.arange(n_bars)

        # Candlestick bar widths
        width = 0.6
        wick_width = 0.1

        up = data[data["close"] >= data["open"]]
        down = data[data["close"] < data["open"]]

        # Up candles (Bullish - Green)
        ax.bar(
            up.index,
            up["close"] - up["open"],
            width,
            bottom=up["open"],
            color="#26a69a",
            edgecolor="#26a69a",
        )
        ax.bar(
            up.index,
            up["high"] - up["close"],
            wick_width,
            bottom=up["close"],
            color="#26a69a",
        )
        ax.bar(
            up.index,
            up["low"] - up["open"],
            wick_width,
            bottom=up["open"],
            color="#26a69a",
        )

        # Down candles (Bearish - Red)
        ax.bar(
            down.index,
            down["close"] - down["open"],
            width,
            bottom=down["open"],
            color="#ef5350",
            edgecolor="#ef5350",
        )
        ax.bar(
            down.index,
            down["high"] - down["open"],
            wick_width,
            bottom=down["open"],
            color="#ef5350",
        )
        ax.bar(
            down.index,
            down["low"] - down["close"],
            wick_width,
            bottom=down["close"],
            color="#ef5350",
        )

        # Volume bars
        if "volume" in data.columns:
            ax_vol.bar(up.index, up["volume"], width, color="#26a69a", alpha=0.6)
            ax_vol.bar(down.index, down["volume"], width, color="#ef5350", alpha=0.6)
            ax_vol.set_ylabel("Volume", fontsize=10)

        # Optional: Highlight opening range if metadata present
        if highlight_opening_range and "is_opening_range" in data.columns:
            or_bars = data[data["is_opening_range"] == True]
            if not or_bars.empty:
                or_start_idx = or_bars.index[0]
                or_end_idx = or_bars.index[-1]
                or_high = or_bars["high"].max()
                or_low = or_bars["low"].min()
                rect = patches.Rectangle(
                    (or_start_idx - 0.5, or_low),
                    (or_end_idx - or_start_idx + 1),
                    or_high - or_low,
                    linewidth=1,
                    edgecolor="#1976d2",
                    facecolor="#bbdefb",
                    alpha=0.25,
                    label=f"Opening Range ({or_low:.2f} - {or_high:.2f})",
                )
                ax.add_patch(rect)
                ax.axhline(
                    or_high, color="#1976d2", linestyle="--", alpha=0.5, linewidth=0.8
                )
                ax.axhline(
                    or_low, color="#1976d2", linestyle="--", alpha=0.5, linewidth=0.8
                )

        # Chart Title & Labels
        first_ts = data.iloc[0]["timestamp"]
        session_label = (
            first_ts.strftime("%Y-%m-%d")
            if hasattr(first_ts, "strftime")
            else str(first_ts)[:10]
        )
        chart_title = title or f"{symbol} Candlesticks — {session_label}"
        ax.set_title(chart_title, fontsize=13, fontweight="bold", pad=12)
        ax.set_ylabel("Price ($)", fontsize=11)
        ax.grid(True, linestyle=":", alpha=0.4)
        ax_vol.grid(True, linestyle=":", alpha=0.4)

        if (
            highlight_opening_range
            and "is_opening_range" in data.columns
            and not data[data["is_opening_range"] == True].empty
        ):
            ax.legend(loc="upper left", fontsize=8, framealpha=0.9)

        # Format X-ticks with timestamp labels
        step = max(1, n_bars // 8)
        tick_indices = list(range(0, n_bars, step))
        if (n_bars - 1) not in tick_indices:
            tick_indices.append(n_bars - 1)

        def _fmt_ts(val: object) -> str:
            if hasattr(val, "strftime"):
                # If intraday, display HH:MM; if multi-day, display YYYY-MM-DD
                return (
                    val.strftime("%H:%M") if n_bars <= 390 else val.strftime("%Y-%m-%d")
                )
            return str(val)

        tick_labels = [_fmt_ts(data.iloc[i]["timestamp"]) for i in tick_indices]
        ax_vol.set_xticks(tick_indices)
        ax_vol.set_xticklabels(tick_labels, rotation=0, fontsize=9)

        plt.tight_layout()

        # Save plot
        chosen_filename = filename or self.filename
        if chosen_filename is None:
            chosen_filename = f"{symbol}_candlestick_{session_label}.png"
        out_path = self.output_dir / chosen_filename
        plt.savefig(out_path, dpi=150)
        plt.close(fig)

        logger.info("Saved candlestick chart to %s", out_path)
        return out_path

    def plot_all_sessions(
        self,
        df: pd.DataFrame,
        stage: str,  # e.g., "raw", "cleaned", or "processed"
        symbol: str = "SPY",
        max_plots: Optional[int] = None,
    ) -> List[Path]:
        """Plots candlestick charts grouped by session_id.

        Args:
            df: DataFrame containing session_id and OHLCV bars.
            symbol: Ticker symbol.
            max_plots: Maximum number of session charts to generate.

        Returns:
            List of Paths to saved PNG figures.
        """
        output_paths: List[Path] = []
        if "session_id" not in df.columns:
            # For raw bars where session_id hasn't been tagged yet
            p = self.plot_session(
                df,
                symbol=symbol,
                title=f"{symbol} [{stage.upper()}] Candlesticks",
                filename=f"{symbol}_{stage}_candlesticks.png",
            )
            return [p]

        grouped = df.groupby("session_id")
        session_keys = list(grouped.groups.keys())
        if max_plots:
            session_keys = session_keys[:max_plots]

        for s_id in session_keys:
            session_df = grouped.get_group(s_id)
            p = self.plot_session(
                session_df,
                symbol=symbol,
                title=f"{symbol} [{stage.upper()}] — {s_id}",
                filename=f"{symbol}_{stage}_candlestick_{s_id}.png",
            )
            output_paths.append(p)

        return output_paths
