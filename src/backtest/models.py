"""
src.backtest.models
===================
Order, Position & Trade State Models for the SPY ORB backtest engine.

Defines the object-oriented data structures for order tracking, active position
management, and closed trade recording, ensuring full accounting of fills,
fees, slippage, and R-multiples.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd


class PositionSide(str, Enum):
    """Position side / market orientation."""

    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    """Reason for closing an active position."""

    TARGET = "TARGET"
    STOP = "STOP"
    EOD = "EOD"


@dataclass
class Trade:
    """Finalized closed trade record matching Section 5.1 Trade Log Schema."""

    trade_id: int
    date: str
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    or_high: float
    or_low: float
    or_width: float
    shares: int
    pnl_dollars: float
    return_pct: float
    r_multiple: float
    exit_reason: str
    slippage_paid: float
    commission_paid: float

    def to_dict(self) -> dict[str, Any]:
        """Convert trade record to clean dictionary for DataFrame/CSV export."""
        return {
            "trade_id": self.trade_id,
            "date": self.date,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": (
                self.entry_time.isoformat()
                if hasattr(self.entry_time, "isoformat")
                else str(self.entry_time)
            ),
            "exit_time": (
                self.exit_time.isoformat()
                if hasattr(self.exit_time, "isoformat")
                else str(self.exit_time)
            ),
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "stop_price": round(self.stop_price, 4),
            "target_price": round(self.target_price, 4),
            "or_high": round(self.or_high, 4),
            "or_low": round(self.or_low, 4),
            "or_width": round(self.or_width, 4),
            "shares": int(self.shares),
            "pnl_dollars": round(self.pnl_dollars, 4),
            "return_pct": round(self.return_pct, 6),
            "r_multiple": round(self.r_multiple, 4),
            "exit_reason": str(self.exit_reason),
            "slippage_paid": round(self.slippage_paid, 4),
            "commission_paid": round(self.commission_paid, 4),
        }


@dataclass
class Position:
    """Active in-market holding state."""

    trade_id: int
    date: str
    symbol: str
    side: PositionSide
    entry_time: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    shares: int
    initial_risk_per_share: float
    or_high: float
    or_low: float
    or_width: float

    def close(
        self,
        exit_time: pd.Timestamp,
        exit_price: float,
        reason: ExitReason,
        slippage_paid: float = 0.0,
        commission_paid: float = 0.0,
    ) -> Trade:
        """Close active position and create a finalized Trade object."""
        # Calculate raw gross P&L per share and in dollars
        if self.side == PositionSide.LONG:
            gross_pnl_per_share = exit_price - self.entry_price
        elif self.side == PositionSide.SHORT:
            gross_pnl_per_share = self.entry_price - exit_price
        else:
            gross_pnl_per_share = 0.0

        gross_pnl = gross_pnl_per_share * self.shares
        total_frictions = slippage_paid + commission_paid
        net_pnl_dollars = gross_pnl - total_frictions

        # Calculate return percentage on capital committed
        invested_capital = self.entry_price * self.shares
        return_pct = (
            (net_pnl_dollars / invested_capital) if invested_capital > 0 else 0.0
        )

        # Calculate realized R-multiple
        if self.initial_risk_per_share > 0:
            r_multiple = gross_pnl_per_share / self.initial_risk_per_share
        else:
            r_multiple = 0.0

        return Trade(
            trade_id=self.trade_id,
            date=self.date,
            symbol=self.symbol,
            direction=self.side.value,
            entry_time=self.entry_time,
            exit_time=exit_time,
            entry_price=self.entry_price,
            exit_price=exit_price,
            stop_price=self.stop_loss,
            target_price=self.take_profit,
            or_high=self.or_high,
            or_low=self.or_low,
            or_width=self.or_width,
            shares=self.shares,
            pnl_dollars=net_pnl_dollars,
            return_pct=return_pct,
            r_multiple=r_multiple,
            exit_reason=reason.value if hasattr(reason, "value") else str(reason),
            slippage_paid=slippage_paid,
            commission_paid=commission_paid,
        )
