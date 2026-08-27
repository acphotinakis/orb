"""
src.strategy.signals
====================
ORB Breakout Signal Generation Engine for the SPY ORB system.

Evaluates trading bars in the active trading window [09:45:00, 15:59:00) ET,
detects bar-close breakouts beyond frozen opening range boundaries, calculates
dynamic stop-loss and 2R profit-target levels, and enforces the
single-trade-per-session constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict
import pandas as pd

from src.common.config import StrategyConfig
from src.common.logger import get_logger
from src.strategy.opening_range import OpeningRange

logger = get_logger(__name__)


@dataclass(frozen=True)
class Signal:
    """Immutable breakout trading signal.

    Attributes:
        session_id: Date string (YYYY-MM-DD)
        timestamp: Bar close timestamp when breakout confirmed
        symbol: Ticker symbol (e.g. SPY)
        direction: 'LONG' or 'SHORT'
        entry_price: Bar close price confirming breakout
        stop_loss: Associated OR boundary
        take_profit: Target price at 2R
        risk_amount: 1R dollar risk (|entry - stop_loss|)
        or_high: Session frozen OR High
        or_low: Session frozen OR Low
        or_width: Session frozen OR Width
    """

    session_id: str
    timestamp: pd.Timestamp
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    or_high: float
    or_low: float
    or_width: float

    def __str__(self) -> str:
        return (
            f"Signal({self.symbol} {self.direction} @ {self.entry_price:.2f} "
            f"SL={self.stop_loss:.2f} TP={self.take_profit:.2f} 1R={self.risk_amount:.2f} "
            f"time={self.timestamp.strftime('%Y-%m-%d %H:%M:%S%z')})"
        )


class SignalGenerator:
    """Evaluates trading bars sequentially against frozen OpeningRange."""

    def __init__(self, config: StrategyConfig, buffer: Optional[float] = None) -> None:
        self._config = config
        # If buffer is not explicitly passed, compute or default using config
        self._buffer = buffer

    def evaluate_session_signals(
        self,
        session_bars: pd.DataFrame,
        opening_range: OpeningRange,
    ) -> Optional[Signal]:
        """Evaluates session trading bars sequentially.

        Returns first valid Signal or None if no breakout occurs.
        Enforces max 1 trade per session and ensures no signal is generated
        during the opening range window.
        """
        if not opening_range.is_valid:
            logger.warning(
                "Session %s: Opening range is invalid; skipping signal evaluation.",
                opening_range.session_id,
            )
            return None

        # Filter strictly to trading window bars
        # Note: TASK-008 tagged `is_trading_window` True for [09:45, 15:59)
        if "is_trading_window" in session_bars.columns:
            trading_bars = session_bars[
                session_bars["is_trading_window"] == True
            ].copy()
        else:
            # Fallback based on minute_of_day if is_trading_window not present
            or_mins = self._config.opening_range_minutes
            trading_bars = session_bars[session_bars["minute_of_day"] >= or_mins].copy()

        if trading_bars.empty:
            return None

        # Sort chronologically to maintain causality
        trading_bars = trading_bars.sort_values("timestamp").reset_index(drop=True)

        symbol = self._config.ticker
        or_high = opening_range.or_high
        or_low = opening_range.or_low
        or_width = opening_range.or_width

        # Determine buffer
        buffer_val = (
            self._buffer
            if self._buffer is not None
            else (or_width * self._config.breakout_buffer_pct)
        )
        target_r = self._config.target_r

        for _, row in trading_bars.iterrows():
            close_price = float(row["close"])
            ts = pd.Timestamp(row["timestamp"])

            # Long breakout check
            if self._config.direction_mode in ("both", "long_only") and close_price > (
                or_high + buffer_val
            ):
                stop_loss = or_low
                risk_amount = close_price - stop_loss
                if risk_amount <= 0:
                    continue  # Invalid geometry
                take_profit = close_price + (target_r * risk_amount)

                signal = Signal(
                    session_id=opening_range.session_id,
                    timestamp=ts,
                    symbol=symbol,
                    direction="LONG",
                    entry_price=close_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_amount=risk_amount,
                    or_high=or_high,
                    or_low=or_low,
                    or_width=or_width,
                )
                logger.info("Long breakout signal generated: %s", signal)
                return signal

            # Short breakout check
            elif self._config.direction_mode in (
                "both",
                "short_only",
            ) and close_price < (or_low - buffer_val):
                stop_loss = or_high
                risk_amount = stop_loss - close_price
                if risk_amount <= 0:
                    continue  # Invalid geometry
                take_profit = close_price - (target_r * risk_amount)

                signal = Signal(
                    session_id=opening_range.session_id,
                    timestamp=ts,
                    symbol=symbol,
                    direction="SHORT",
                    entry_price=close_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_amount=risk_amount,
                    or_high=or_high,
                    or_low=or_low,
                    or_width=or_width,
                )
                logger.info("Short breakout signal generated: %s", signal)
                return signal

        return None

    def generate_all_signals(
        self,
        df: pd.DataFrame,
        opening_ranges: Dict[str, OpeningRange],
    ) -> Dict[str, Signal]:
        """Generates signals for all sessions in the dataset."""
        signals: Dict[str, Signal] = {}
        grouped = df.groupby("session_id")

        for session_id, session_bars in grouped:
            session_id_str = str(session_id)
            if session_id_str not in opening_ranges:
                continue

            or_obj = opening_ranges[session_id_str]
            sig = self.evaluate_session_signals(session_bars, or_obj)
            if sig is not None:
                signals[session_id_str] = sig

        return signals
