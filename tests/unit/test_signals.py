"""
tests/unit/test_signals.py
==========================
Unit tests for ORB Breakout Signal Generation Engine.
"""

import pandas as pd

from src.strategy.opening_range import OpeningRange
from src.strategy.signals import Signal, SignalGenerator


def test_long_signal_generation(synthetic_rth_bars, mock_app_config):
    or_obj = OpeningRange(
        session_id="2024-01-02",
        start_time=pd.Timestamp("2024-01-02 09:30:00"),
        end_time=pd.Timestamp("2024-01-02 09:44:00"),
        or_high=501.0,
        or_low=499.0,
        or_width=2.0,
        total_volume=15000.0,
        bar_count=15,
        is_valid=True,
    )

    df = synthetic_rth_bars.copy()
    # Trigger breakout at bar 16 (09:46)
    df.loc[16, "close"] = 501.5

    gen = SignalGenerator(mock_app_config.strategy)
    sig = gen.evaluate_session_signals(df, or_obj)

    assert isinstance(sig, Signal)
    assert sig.direction == "LONG"
    assert sig.entry_price == 501.5
    assert sig.stop_loss == 499.0
    assert sig.take_profit == 501.5 + 2.0 * (501.5 - 499.0)  # 506.5


def test_no_signal_during_opening_range(synthetic_rth_bars, mock_app_config):
    or_obj = OpeningRange(
        session_id="2024-01-02",
        start_time=pd.Timestamp("2024-01-02 09:30:00"),
        end_time=pd.Timestamp("2024-01-02 09:44:00"),
        or_high=501.0,
        or_low=499.0,
        or_width=2.0,
        total_volume=15000.0,
        bar_count=15,
        is_valid=True,
    )

    df = synthetic_rth_bars.copy()
    # High close at 09:35 (inside OR)
    df.loc[5, "close"] = 505.0

    gen = SignalGenerator(mock_app_config.strategy)
    sig = gen.evaluate_session_signals(df[df["is_opening_range"]], or_obj)
    assert sig is None
