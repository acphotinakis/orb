"""
src.dashboard.charts
====================
Pure Plotly figure builders for the results explorer (P2).

No Streamlit, no simulation, no I/O: every builder takes in-memory frames
and returns a ``plotly.graph_objects.Figure``.  Empty inputs produce an
annotated empty figure instead of an exception.  Display-only downsampling
preserves global extrema; calculations and downloads always use the original
full-resolution series held by the caller.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

#: Upper bound for points handed to the browser per trace.
MAX_DISPLAY_POINTS = 5000


def downsample_for_display(df: pd.DataFrame, max_points: int = MAX_DISPLAY_POINTS) -> pd.DataFrame:
    """Stride a frame for display, always keeping first/last/global extrema.

    Numeric extrema are computed over float columns only; non-numeric frames
    fall back to plain striding.
    """
    if len(df) <= max_points or len(df) == 0:
        return df
    protected = {0, len(df) - 1}
    numeric = df.select_dtypes(include="number")
    for col in numeric.columns:
        try:
            protected.add(int(numeric[col].idxmax()))
            protected.add(int(numeric[col].idxmin()))
        except Exception:
            continue
    # Widen the stride until first/last + extrema + strided fit the budget.
    step = max(1, len(df) // max_points)
    while True:
        keep = set(range(0, len(df), step)) | protected
        if len(keep) <= max_points:
            return df.iloc[sorted(keep)].reset_index(drop=True)
        step += 1


def _empty_figure(message: str, title: str = "") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False
    )
    fig.update_layout(title=title, xaxis={"visible": False}, yaxis={"visible": False})
    return fig


def equity_figure(equity_df: pd.DataFrame) -> go.Figure:
    """Equity line over drawdown area (drawdown recomputed from equity)."""
    if equity_df.empty or "equity" not in equity_df.columns:
        return _empty_figure("No equity data — run produced no bars.", title="Equity")
    equity = pd.to_numeric(equity_df["equity"], errors="coerce")
    hwm = equity.cummax()
    drawdown = equity - hwm
    x = equity_df["timestamp"] if "timestamp" in equity_df.columns else equity_df.index

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
        subplot_titles=("Equity", "Drawdown ($)"),
    )
    fig.add_trace(go.Scatter(x=x, y=equity, mode="lines", name="Equity"), row=1, col=1)
    fig.add_trace(
        go.Scatter(x=x, y=drawdown, mode="lines", name="Drawdown", fill="tozeroy"),
        row=2, col=1,
    )
    fig.update_layout(title="Portfolio equity", showlegend=False)
    return fig


def session_candlestick(
    session_df: pd.DataFrame,
    *,
    or_high: Optional[float] = None,
    or_low: Optional[float] = None,
    session_trades: Optional[pd.DataFrame] = None,
) -> go.Figure:
    """OHLC candlesticks with frozen-range lines and entry/exit markers."""
    required = {"timestamp", "open", "high", "low", "close"}
    if session_df.empty or not required.issubset(session_df.columns):
        return _empty_figure("No session bars selected.", title="Session")
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=session_df["timestamp"],
                open=session_df["open"],
                high=session_df["high"],
                low=session_df["low"],
                close=session_df["close"],
                name="OHLC",
            )
        ]
    )
    if or_high is not None:
        fig.add_hline(y=or_high, line_dash="dash", annotation_text="OR high")
    if or_low is not None:
        fig.add_hline(y=or_low, line_dash="dash", annotation_text="OR low")
    if session_trades is not None and not session_trades.empty:
        for _, trade in session_trades.iterrows():
            direction = str(trade.get("direction", ""))
            marker = "triangle-up" if direction == "LONG" else "triangle-down"
            for col, label in (("entry_time", "Entry"), ("exit_time", "Exit")):
                if col in session_trades.columns and pd.notna(trade.get(col)):
                    price_col = "entry_price" if label == "Entry" else "exit_price"
                    fig.add_trace(
                        go.Scatter(
                            x=[trade[col]],
                            y=[trade.get(price_col)],
                            mode="markers",
                            marker={"symbol": marker, "size": 12},
                            name=f"{label} {trade.get('trade_id', '')} ({direction})",
                        )
                    )
            for level, label in (
                ("stop_price", "Stop"),
                ("target_price", "Target"),
            ):
                if level in session_trades.columns and pd.notna(trade.get(level)):
                    fig.add_hline(
                        y=trade[level], line_dash="dot",
                        annotation_text=f"{label} {trade.get('trade_id', '')}",
                    )
    fig.update_layout(title="Session bars", xaxis_rangeslider_visible=False)
    return fig


def r_distribution_figure(trades_df: pd.DataFrame) -> go.Figure:
    """Histogram of per-trade R multiples (empty-aware)."""
    if trades_df.empty or "r_multiple" not in trades_df.columns:
        return _empty_figure("No trades — nothing to distribute.", title="R multiples")
    fig = go.Figure(
        data=[go.Histogram(x=pd.to_numeric(trades_df["r_multiple"], errors="coerce"))]
    )
    fig.update_layout(title="R-multiple distribution", showlegend=False)
    return fig
