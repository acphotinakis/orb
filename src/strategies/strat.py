#!/usr/bin/env python3
"""
strategy_backtest.py
=====================
Institutional-grade event-driven backtesting engine for the 9/21 EMA
Crossover Strategy ("9/21 EMA Institutional v5.0").

Data source: Alpaca Markets 1-minute OHLCV bars (via alpaca-py), resampled
to a configurable timeframe (default 5-minute) for signal generation and
bar-by-bar bracket-order simulation.

Author: Senior Quantitative Trading Engineer
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Optional Alpaca import. The script degrades gracefully to CSV-only mode
# if alpaca-py is not installed or credentials are not configured.
# --------------------------------------------------------------------------
try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False


# ==========================================================================
# Data structures
# ==========================================================================


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp | None = None
    direction: str = ""  # "LONG" or "SHORT"
    entry_price: float = 0.0
    exit_price: float | None = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    exit_reason: str = ""  # "TP", "SL", "REVERSAL", "EOD"
    pnl: float | None = None
    return_pct: float | None = None
    shares: int = 0

    def close(self, exit_time: pd.Timestamp, exit_price: float, reason: str) -> None:
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.exit_reason = reason
        if self.direction == "LONG":
            self.pnl = (exit_price - self.entry_price) * self.shares
        else:  # SHORT
            self.pnl = (self.entry_price - exit_price) * self.shares
        self.return_pct = (
            (self.pnl / (self.entry_price * self.shares)) * 100.0
            if self.shares
            else 0.0
        )


@dataclass
class Position:
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    shares: int
    trade: Trade = field(default=None)


# ==========================================================================
# Alpaca data fetcher
# ==========================================================================


class AlpacaDataFetcher:
    """Thin wrapper around alpaca-py to pull historical 1-minute bars."""

    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self._client = None
        if _ALPACA_AVAILABLE and api_key and secret_key:
            self._client = StockHistoricalDataClient(api_key, secret_key)

    def fetch_1min_bars(
        self, symbol: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        if self._client is None:
            raise RuntimeError(
                "Alpaca client not configured (missing alpaca-py or API keys). "
                "Use --csv to load data from a local file instead."
            )
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
        )
        bars = self._client.get_stock_bars(request)
        df = bars.df.reset_index()
        if "symbol" in df.columns:
            df = df.drop(columns=["symbol"])
        df = df.rename(columns={"timestamp": "timestamp"})
        return df[["timestamp", "open", "high", "low", "close", "volume"]]


# ==========================================================================
# Core backtesting engine
# ==========================================================================


class EmaStrategyBacktester:
    """
    Event-driven backtester for the 9/21 EMA crossover strategy with
    ATR-based bracket orders, trade reversals, and a consecutive-loss
    circuit breaker.
    """

    def __init__(
        self,
        raw_1min_df: pd.DataFrame,
        symbol: str = "AAPL",
        initial_capital: float = 100_000.0,
        timeframe: str = "5min",
        fast_ema: int = 9,
        slow_ema: int = 21,
        atr_period: int = 14,
        min_atr: float = 0.15,
        sl_atr_mult: float = 2.5,
        tp_atr_mult: float = 3.0,
        enable_shorts: bool = True,
        risk_per_trade_pct: float = 1.0,
        max_consecutive_losses: int = 5,
        commission_per_share: float = 0.0,
    ):
        self.symbol = symbol
        self.initial_capital = initial_capital
        self.timeframe = timeframe
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.atr_period = atr_period
        self.min_atr = min_atr
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.enable_shorts = enable_shorts
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.commission_per_share = commission_per_share

        self.raw_1min_df = raw_1min_df.copy()
        self.bars: pd.DataFrame = pd.DataFrame()
        self.trades: list[Trade] = []
        self.equity_curve: pd.DataFrame = pd.DataFrame()

        # Runtime state
        self.equity: float = initial_capital
        self.consecutive_losses: int = 0
        self.max_consecutive_loss_streak: int = 0
        self.circuit_breaker_tripped: bool = False
        self.position: Position | None = None

    # ----------------------------------------------------------------
    # Data preparation
    # ----------------------------------------------------------------
    def resample(self) -> pd.DataFrame:
        """Resample 1-minute OHLCV bars into the target timeframe."""
        df = self.raw_1min_df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()

        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
        resampled = df.resample(self.timeframe, label="right", closed="right").agg(agg)
        resampled = resampled.dropna(subset=["open", "high", "low", "close"])
        resampled = resampled.reset_index()
        self.bars = resampled
        return resampled

    def compute_indicators(self) -> pd.DataFrame:
        """Compute 9/21 EMA and 14-period ATR on the resampled bars."""
        df = self.bars.copy()

        df["ema_fast"] = df["close"].ewm(span=self.fast_ema, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.slow_ema, adjust=False).mean()

        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df["tr"] = tr
        df["atr"] = df["tr"].ewm(alpha=1.0 / self.atr_period, adjust=False).mean()

        # Crossover detection
        df["ema_diff"] = df["ema_fast"] - df["ema_slow"]
        df["ema_diff_prev"] = df["ema_diff"].shift(1)
        df["bullish_cross"] = (df["ema_diff_prev"] <= 0) & (df["ema_diff"] > 0)
        df["bearish_cross"] = (df["ema_diff_prev"] >= 0) & (df["ema_diff"] < 0)

        self.bars = df
        return df

    # ----------------------------------------------------------------
    # Position sizing
    # ----------------------------------------------------------------
    def _position_size(self, entry_price: float, stop_loss: float) -> int:
        """Risk a fixed percentage of current equity per trade."""
        risk_amount = self.equity * (self.risk_per_trade_pct / 100.0)
        per_share_risk = abs(entry_price - stop_loss)
        if per_share_risk <= 0:
            return 0
        shares = int(risk_amount / per_share_risk)
        # Cap by available buying power (no margin assumed)
        max_affordable = int(self.equity / entry_price) if entry_price > 0 else 0
        return max(0, min(shares, max_affordable))

    # ----------------------------------------------------------------
    # Core bar-by-bar simulation loop
    # ----------------------------------------------------------------
    def run(self) -> None:
        if self.bars.empty:
            self.resample()
        if "ema_fast" not in self.bars.columns:
            self.compute_indicators()

        df = self.bars.dropna(subset=["ema_fast", "ema_slow", "atr"]).reset_index(
            drop=True
        )

        equity_records = []
        self.equity = self.initial_capital
        self.consecutive_losses = 0
        self.max_consecutive_loss_streak = 0
        self.circuit_breaker_tripped = False
        self.position = None
        self.trades = []

        for _i, row in df.iterrows():
            ts = row["timestamp"]
            c = row["close"]

            # ---- 1. Manage an existing open position first (intrabar) ----
            if self.position is not None:
                self._evaluate_open_position(row)

            # ---- 2. Handle reversal / new signal on this bar ----
            if row["bullish_cross"] or row["bearish_cross"]:
                self._handle_signal(row)

            # ---- 3. Mark-to-market equity ----
            mtm_equity = self._mark_to_market(c)
            equity_records.append({"timestamp": ts, "equity": mtm_equity})

        # Close any position still open at the end of the backtest
        if self.position is not None:
            last_row = df.iloc[-1]
            self._close_position(last_row["timestamp"], last_row["close"], "EOD")
            equity_records[-1]["equity"] = self.equity

        self.equity_curve = pd.DataFrame(equity_records)

    # ----------------------------------------------------------------
    def _mark_to_market(self, current_close: float) -> float:
        if self.position is None:
            return self.equity
        pos = self.position
        if pos.direction == "LONG":
            unrealized = (current_close - pos.entry_price) * pos.shares
        else:
            unrealized = (pos.entry_price - current_close) * pos.shares
        return self.equity + unrealized

    # ----------------------------------------------------------------
    def _evaluate_open_position(self, row: pd.Series) -> None:
        """Check SL/TP against the current bar's High/Low for intrabar fills."""
        pos = self.position
        bar_high, bar_low, ts = row["high"], row["low"], row["timestamp"]

        if pos.direction == "LONG":
            hit_sl = bar_low <= pos.stop_loss
            hit_tp = bar_high >= pos.take_profit
            if hit_sl and hit_tp:
                # Conservative assumption: stop-loss triggers first intrabar
                self._close_position(ts, pos.stop_loss, "SL")
            elif hit_sl:
                self._close_position(ts, pos.stop_loss, "SL")
            elif hit_tp:
                self._close_position(ts, pos.take_profit, "TP")
        else:  # SHORT
            hit_sl = bar_high >= pos.stop_loss
            hit_tp = bar_low <= pos.take_profit
            if hit_sl and hit_tp or hit_sl:
                self._close_position(ts, pos.stop_loss, "SL")
            elif hit_tp:
                self._close_position(ts, pos.take_profit, "TP")

    # ----------------------------------------------------------------
    def _handle_signal(self, row: pd.Series) -> None:
        atr = row["atr"]
        close = row["close"]
        ts = row["timestamp"]

        if atr < self.min_atr or pd.isna(atr):
            return  # ATR filter not satisfied

        bullish = row["bullish_cross"]
        bearish = row["bearish_cross"]

        # --- Reversal: opposing crossover while a position is open ---
        if self.position is not None:
            if (self.position.direction == "LONG" and bearish) or (
                self.position.direction == "SHORT" and bullish
            ):
                self._close_position(ts, close, "REVERSAL")

        # --- Circuit breaker check before opening a new trade ---
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.circuit_breaker_tripped = True

        if self.circuit_breaker_tripped:
            return  # halt new entries until manual reset / regime change

        if self.position is not None:
            # already in a flat state check
            return

        # --- New entry ---
        if bullish:
            self._open_position("LONG", ts, close, atr)
        elif bearish and self.enable_shorts:
            self._open_position("SHORT", ts, close, atr)

    # ----------------------------------------------------------------
    def _open_position(
        self, direction: str, ts: pd.Timestamp, entry_price: float, atr: float
    ) -> None:
        if direction == "LONG":
            sl = entry_price - self.sl_atr_mult * atr
            tp = entry_price + self.tp_atr_mult * atr
        else:
            sl = entry_price + self.sl_atr_mult * atr
            tp = entry_price - self.tp_atr_mult * atr

        shares = self._position_size(entry_price, sl)
        if shares <= 0:
            return

        trade = Trade(
            entry_time=ts,
            direction=direction,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            shares=shares,
        )
        self.position = Position(
            direction=direction,
            entry_time=ts,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            shares=shares,
            trade=trade,
        )

    # ----------------------------------------------------------------
    def _close_position(self, ts: pd.Timestamp, exit_price: float, reason: str) -> None:
        pos = self.position
        trade = pos.trade
        trade.close(ts, exit_price, reason)

        commission = self.commission_per_share * trade.shares * 2  # entry + exit
        realized_pnl = trade.pnl - commission
        trade.pnl = realized_pnl

        self.equity += realized_pnl

        # Circuit breaker bookkeeping
        if realized_pnl < 0:
            self.consecutive_losses += 1
            self.max_consecutive_loss_streak = max(
                self.max_consecutive_loss_streak, self.consecutive_losses
            )
        else:
            self.consecutive_losses = 0
            self.circuit_breaker_tripped = (
                False  # winning trade resets breaker eligibility
            )

        self.trades.append(trade)
        self.position = None


# ==========================================================================
# Performance reporting
# ==========================================================================


class PerformanceReport:
    def __init__(
        self, backtester: EmaStrategyBacktester, bars_per_year: int = 252 * 78
    ):
        """
        bars_per_year: approximate number of 5-min bars in a trading year
        (78 five-minute bars per 6.5h session * 252 sessions) used for
        Sharpe annualization. Adjust if using a different timeframe.
        """
        self.bt = backtester
        self.bars_per_year = bars_per_year

    def trades_dataframe(self) -> pd.DataFrame:
        rows = []
        for t in self.bt.trades:
            rows.append(
                {
                    "Entry Time": t.entry_time,
                    "Exit Time": t.exit_time,
                    "Direction": t.direction,
                    "Entry Price": round(t.entry_price, 4),
                    "Exit Price": (
                        round(t.exit_price, 4) if t.exit_price is not None else None
                    ),
                    "Exit Reason": t.exit_reason,
                    "PnL": round(t.pnl, 2) if t.pnl is not None else None,
                    "Return %": (
                        round(t.return_pct, 3) if t.return_pct is not None else None
                    ),
                }
            )
        return pd.DataFrame(rows)

    def _max_drawdown(self) -> tuple[float, float]:
        eq = self.bt.equity_curve["equity"]
        if eq.empty:
            return 0.0, 0.0
        running_max = eq.cummax()
        drawdown = eq - running_max
        drawdown_pct = drawdown / running_max
        max_dd_dollar = drawdown.min()
        max_dd_pct = drawdown_pct.min() * 100.0
        return max_dd_dollar, max_dd_pct

    def _sharpe_ratio(self) -> float:
        eq = self.bt.equity_curve["equity"]
        if len(eq) < 2:
            return 0.0
        returns = eq.pct_change().dropna()
        if returns.std() == 0 or returns.empty:
            return 0.0
        sharpe = (returns.mean() / returns.std()) * math.sqrt(self.bars_per_year)
        return sharpe

    def generate(self) -> dict:
        trades = self.bt.trades
        total_trades = len(trades)
        wins = [t for t in trades if t.pnl is not None and t.pnl > 0]
        losses = [t for t in trades if t.pnl is not None and t.pnl <= 0]

        gross_profit = sum(t.pnl for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0.0

        win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0
        profit_factor = (
            (gross_profit / gross_loss)
            if gross_loss > 0
            else float("inf") if gross_profit > 0 else 0.0
        )

        avg_win = np.mean([t.pnl for t in wins]) if wins else 0.0
        avg_loss = np.mean([t.pnl for t in losses]) if losses else 0.0
        win_loss_ratio = (
            (abs(avg_win / avg_loss))
            if avg_loss != 0
            else float("inf") if avg_win > 0 else 0.0
        )
        avg_pnl_per_trade = np.mean([t.pnl for t in trades]) if trades else 0.0

        final_equity = self.bt.equity
        net_pnl = final_equity - self.bt.initial_capital
        net_pnl_pct = (net_pnl / self.bt.initial_capital) * 100.0

        max_dd_dollar, max_dd_pct = self._max_drawdown()
        sharpe = self._sharpe_ratio()

        return {
            "initial_capital": self.bt.initial_capital,
            "final_equity": final_equity,
            "net_pnl": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "win_loss_ratio": win_loss_ratio,
            "avg_pnl_per_trade": avg_pnl_per_trade,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "max_drawdown_dollar": max_dd_dollar,
            "max_drawdown_pct": max_dd_pct,
            "sharpe_ratio": sharpe,
            "max_consecutive_loss_streak": self.bt.max_consecutive_loss_streak,
        }

    def print_report(self) -> None:
        stats = self.generate()
        symbol = self.bt.symbol

        def money(x):
            sign = "-" if x < 0 else ""
            return f"{sign}${abs(x):,.2f}"

        line = "=" * 62
        print(line)
        print(
            f" 9/21 EMA INSTITUTIONAL BACKTEST REPORT | {symbol} | "
            f"{self.bt.timeframe}"
        )
        print(line)
        print(f" Initial Capital ......... {money(stats['initial_capital'])}")
        print(f" Final Equity ............ {money(stats['final_equity'])}")
        print(
            f" Total Net P&L ........... {money(stats['net_pnl'])} "
            f"({stats['net_pnl_pct']:.2f}%)"
        )
        print("-" * 62)
        print(f" Total Trades ............ {stats['total_trades']}")
        print(f" Winning Trades .......... {stats['winning_trades']}")
        print(f" Losing Trades ........... {stats['losing_trades']}")
        print(f" Win Rate ................ {stats['win_rate']:.2f}%")
        pf = stats["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f" Profit Factor ........... {pf_str}")
        wlr = stats["win_loss_ratio"]
        wlr_str = "inf" if wlr == float("inf") else f"{wlr:.2f}"
        print(f" Win/Loss Ratio .......... {wlr_str}")
        print(f" Avg P&L per Trade ....... {money(stats['avg_pnl_per_trade'])}")
        print("-" * 62)
        print(
            f" Max Drawdown ............ {money(stats['max_drawdown_dollar'])} "
            f"({stats['max_drawdown_pct']:.2f}%)"
        )
        print(f" Annualized Sharpe Ratio . {stats['sharpe_ratio']:.2f}")
        print(f" Max Consecutive Losses .. {stats['max_consecutive_loss_streak']}")
        print(line)

        trades_df = self.trades_dataframe()
        if not trades_df.empty:
            print("\nExecuted Trades:")
            with pd.option_context("display.max_rows", None, "display.width", 160):
                print(trades_df.to_string(index=False))
        else:
            print("\nNo trades were executed during the backtest period.")


# ==========================================================================
# CSV / synthetic data loading helpers
# ==========================================================================


def load_csv_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")
    return df[["timestamp", "open", "high", "low", "close", "volume"]].sort_values(
        "timestamp"
    )


def generate_synthetic_data(
    symbol: str, days: int = 90, seed: int = 42
) -> pd.DataFrame:
    """Generate a synthetic 1-minute OHLCV dataset for demo/testing when no
    Alpaca credentials or CSV file are available."""
    rng = np.random.default_rng(seed)
    bars_per_day = 390  # 6.5-hour regular session
    n = days * bars_per_day

    start = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days)
    timestamps = pd.bdate_range(start=start, periods=n, freq="1min")

    price = 180.0
    closes = []
    for _ in range(n):
        price += rng.normal(0, 0.05)
        price = max(price, 1.0)
        closes.append(price)
    closes = np.array(closes)

    opens = closes + rng.normal(0, 0.02, n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0, 0.03, n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0, 0.03, n))
    volumes = rng.integers(100, 5000, n)

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )
    return df


# ==========================================================================
# CLI
# ==========================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="9/21 EMA Institutional Crossover Backtester (v5.0)"
    )
    p.add_argument("--symbol", type=str, default="AAPL", help="Ticker symbol")
    p.add_argument(
        "--csv",
        type=str,
        default="aapl_1min_90d.csv",
        help="Path to 1-min OHLCV CSV file",
    )
    p.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    p.add_argument(
        "--timeframe",
        type=str,
        default="5min",
        help="Resample timeframe (pandas offset alias, e.g. 5min, 15min)",
    )
    p.add_argument("--fast-ema", type=int, default=9, help="Fast EMA period")
    p.add_argument("--slow-ema", type=int, default=21, help="Slow EMA period")
    p.add_argument("--atr-period", type=int, default=14, help="ATR lookback period")
    p.add_argument(
        "--min-atr", type=float, default=0.15, help="Minimum ATR required to trade"
    )
    p.add_argument(
        "--sl-atr-mult", type=float, default=2.5, help="Stop-loss ATR multiple"
    )
    p.add_argument(
        "--tp-atr-mult", type=float, default=3.0, help="Take-profit ATR multiple"
    )
    p.add_argument(
        "--enable-shorts", action="store_true", default=True, help="Allow short entries"
    )
    p.add_argument(
        "--disable-shorts",
        dest="enable_shorts",
        action="store_false",
        help="Disable short entries",
    )
    p.add_argument(
        "--risk-pct", type=float, default=1.0, help="Percent of equity risked per trade"
    )
    p.add_argument(
        "--max-consecutive-losses",
        type=int,
        default=5,
        help="Circuit breaker threshold",
    )
    p.add_argument(
        "--commission", type=float, default=0.0, help="Commission per share (each side)"
    )
    p.add_argument(
        "--use-alpaca",
        action="store_true",
        help="Fetch data live from Alpaca instead of CSV",
    )
    p.add_argument("--api-key", type=str, default=None, help="Alpaca API key")
    p.add_argument("--secret-key", type=str, default=None, help="Alpaca API secret key")
    p.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback window in days (Alpaca fetch / synthetic data)",
    )
    p.add_argument(
        "--generate-demo-data",
        action="store_true",
        help="Generate synthetic data and save to --csv path if no source is found",
    )
    return p


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    # ---- 1. Acquire data ----
    if args.use_alpaca:
        if not _ALPACA_AVAILABLE:
            print(
                "alpaca-py is not installed. Run: pip install alpaca-py",
                file=sys.stderr,
            )
            sys.exit(1)
        fetcher = AlpacaDataFetcher(api_key=args.api_key, secret_key=args.secret_key)
        end = datetime.utcnow()
        start = end - timedelta(days=args.days)
        print(
            f"Fetching {args.symbol} 1-min bars from Alpaca "
            f"({start.date()} -> {end.date()})..."
        )
        raw_df = fetcher.fetch_1min_bars(args.symbol, start, end)
    else:
        csv_path = Path(args.csv)
        if csv_path.exists():
            print(f"Loading 1-min data from {csv_path} ...")
            raw_df = load_csv_data(str(csv_path))
        elif args.generate_demo_data:
            print(
                f"No CSV found at {csv_path}. "
                f"Generating demo data ({args.days} days)..."
            )
            raw_df = generate_synthetic_data(args.symbol, days=args.days)
            raw_df.to_csv(csv_path, index=False)
            print(f"Synthetic data saved to {csv_path}")
        else:
            print(
                f"CSV file not found: {csv_path}\n"
                f"Provide a valid --csv path, use --use-alpaca with credentials, "
                f"or pass --generate-demo-data to create a synthetic dataset.",
                file=sys.stderr,
            )
            sys.exit(1)

    if raw_df.empty:
        print("No data available to backtest.", file=sys.stderr)
        sys.exit(1)

    # ---- 2. Run backtest ----
    bt = EmaStrategyBacktester(
        raw_1min_df=raw_df,
        symbol=args.symbol,
        initial_capital=args.capital,
        timeframe=args.timeframe,
        fast_ema=args.fast_ema,
        slow_ema=args.slow_ema,
        atr_period=args.atr_period,
        min_atr=args.min_atr,
        sl_atr_mult=args.sl_atr_mult,
        tp_atr_mult=args.tp_atr_mult,
        enable_shorts=args.enable_shorts,
        risk_per_trade_pct=args.risk_pct,
        max_consecutive_losses=args.max_consecutive_losses,
        commission_per_share=args.commission,
    )

    print("Resampling 1-min data ->", args.timeframe, "...")
    bt.resample()
    print("Computing indicators (EMA 9/21, ATR 14)...")
    bt.compute_indicators()
    print("Running event-driven simulation...\n")
    bt.run()

    # ---- 3. Report ----
    report = PerformanceReport(bt)
    report.print_report()


if __name__ == "__main__":
    main()
