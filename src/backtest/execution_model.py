"""
src.backtest.execution_model
============================
Execution Modeling, Slippage & Commission Simulation for SPY ORB.

Handles position sizing algorithms (fixed risk & fixed shares), adverse
slippage modeling on market entries and stop fills, and round-trip commission
accrual based on configurable parameters.
"""

from __future__ import annotations

import math
from typing import Tuple

from src.common.config import ExecutionConfig
from src.backtest.models import ExitReason, PositionSide
from src.common.logger import get_logger

logger = get_logger(__name__)


class ExecutionModel:
    """Simulates realistic fills, position sizing, slippage, and commissions."""

    def __init__(self, config: ExecutionConfig) -> None:
        self.config = config

    def calculate_position_size(
        self,
        capital: float,
        entry_price: float,
        stop_price: float,
    ) -> int:
        """Calculates share size based on portfolio equity and risk configuration.

        Ensures share count is non-negative and position value does not exceed capital.
        """
        if capital <= 0 or entry_price <= 0:
            return 0

        risk_per_share = abs(entry_price - stop_price)

        if self.config.position_sizing == "fixed_shares":
            shares = max(0, int(self.config.fixed_shares))
        elif self.config.position_sizing == "fixed_risk":
            if risk_per_share <= 0:
                logger.warning("Zero risk per share calculated; sizing returned 0.")
                return 0
            dollar_risk = capital * self.config.risk_per_trade_pct
            shares = math.floor(dollar_risk / risk_per_share)
        else:
            logger.warning(
                "Unknown position sizing mode '%s'; defaulting to 0 shares.",
                self.config.position_sizing,
            )
            return 0

        # Cap size to not exceed available portfolio cash (no leverage)
        max_affordable_shares = math.floor(capital / entry_price)
        final_shares = min(shares, max_affordable_shares)

        return max(0, int(final_shares))

    def calculate_entry_execution(
        self,
        direction: str,
        price: float,
        shares: int,
    ) -> Tuple[float, float, float]:
        """Calculates entry fill price with adverse slippage and commission.

        Returns:
            (fill_price, slippage_dollars, entry_commission_dollars)
        """
        slip_per_share = self.config.slippage_per_share
        comm_per_share = self.config.commission_per_share

        dir_upper = direction.upper()
        if dir_upper == PositionSide.LONG.value:
            fill_price = price + slip_per_share
        elif dir_upper == PositionSide.SHORT.value:
            fill_price = price - slip_per_share
        else:
            fill_price = price

        slippage_dollars = slip_per_share * shares
        commission_dollars = comm_per_share * shares

        return fill_price, slippage_dollars, commission_dollars

    def calculate_exit_execution(
        self,
        direction: str,
        price: float,
        shares: int,
        reason: ExitReason,
    ) -> Tuple[float, float, float]:
        """Calculates exit fill price with adverse slippage (for STOP and EOD) and commission.

        Limit target orders (TARGET) assume execution at exact price without adverse slippage.
        Market/Stop exits (STOP, EOD) incur adverse slippage.

        Returns:
            (fill_price, slippage_dollars, exit_commission_dollars)
        """
        slip_per_share = self.config.slippage_per_share
        comm_per_share = self.config.commission_per_share

        dir_upper = direction.upper()

        if reason == ExitReason.TARGET:
            # Limit order fill at target level
            fill_price = price
            slippage_dollars = 0.0
        elif reason in (ExitReason.STOP, ExitReason.EOD):
            # Market / Stop fill with adverse slippage
            if dir_upper == PositionSide.LONG.value:
                fill_price = price - slip_per_share
            elif dir_upper == PositionSide.SHORT.value:
                fill_price = price + slip_per_share
            else:
                fill_price = price
            slippage_dollars = slip_per_share * shares
        else:
            fill_price = price
            slippage_dollars = 0.0

        commission_dollars = comm_per_share * shares

        return fill_price, slippage_dollars, commission_dollars

    def calculate_total_commission(self, shares: int) -> float:
        """Returns total round-trip commission for trade."""
        return self.config.commission_per_share * shares * 2.0
