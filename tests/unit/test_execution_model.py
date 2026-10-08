"""
tests/unit/test_execution_model.py
==================================
Unit tests for execution sizing, slippage, and commissions.
"""

from src.backtest.execution_model import ExecutionModel
from src.backtest.models import ExitReason
from src.common.config import ExecutionConfig


def test_position_sizing_fixed_risk():
    cfg = ExecutionConfig(
        initial_capital=100_000.0,
        position_sizing="fixed_risk",
        risk_per_trade_pct=0.01,  # $1,000 risk
    )
    model = ExecutionModel(cfg)
    # Entry = 500, Stop = 495 -> Risk/sh = 5.0 -> 1000 / 5 = 200 shares
    shares = model.calculate_position_size(
        capital=100_000.0, entry_price=500.0, stop_price=495.0
    )
    assert shares == 200


def test_slippage_and_commission():
    cfg = ExecutionConfig(
        slippage_per_share=0.01,
        commission_per_share=0.0035,
    )
    model = ExecutionModel(cfg)

    # Long Entry: adverse fill + $0.01
    fill_p, slip_d, comm_d = model.calculate_entry_execution(
        "LONG", price=500.0, shares=100
    )
    assert fill_p == 500.01
    assert slip_d == 1.0
    assert round(comm_d, 4) == 0.35

    # Long Stop Exit: adverse fill - $0.01
    fill_p2, slip_d2, comm_d2 = model.calculate_exit_execution(
        "LONG", price=495.0, shares=100, reason=ExitReason.STOP
    )
    assert fill_p2 == 494.99
    assert slip_d2 == 1.0

    # Long Target Exit: limit order, zero slippage
    fill_tp, slip_tp, _ = model.calculate_exit_execution(
        "LONG", price=510.0, shares=100, reason=ExitReason.TARGET
    )
    assert fill_tp == 510.0
    assert slip_tp == 0.0
