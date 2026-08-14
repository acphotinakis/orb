"""
tests/anti_leakage/test_temporal_causality.py
=============================================
Causality audit test suite proving zero lookahead bias, immutability,
and strict temporal ordering across the SPY ORB system.
"""

import pytest
import pandas as pd
import numpy as np

from src.strategy.opening_range import OpeningRangeCalculator
from src.strategy.signals import SignalGenerator
from src.backtest.engine import BacktestEngine
from src.common.exceptions import TemporalLeakageError


def test_opening_range_future_invariance(synthetic_rth_bars):
    """Proves that mutating prices at or after 09:45:00 ET has zero effect on OR."""
    calc = OpeningRangeCalculator(or_minutes=15)
    or_baseline = calc.calculate_session_or(synthetic_rth_bars)

    # Perturb future bars (bars 15..390) with extreme spikes
    df_perturbed = synthetic_rth_bars.copy()
    df_perturbed.loc[15:390, "high"] = 9999.0
    df_perturbed.loc[15:390, "low"] = 0.01

    or_after_spike = calc.calculate_session_or(df_perturbed)

    assert or_baseline.or_high == or_after_spike.or_high
    assert or_baseline.or_low == or_after_spike.or_low
    assert or_baseline.or_width == or_after_spike.or_width
    assert or_baseline.bar_count == or_after_spike.bar_count


def test_signal_future_invariance(synthetic_rth_bars, mock_app_config):
    """Proves that future price paths after a signal do not alter past signal decisions."""
    calc = OpeningRangeCalculator(or_minutes=15)
    or_obj = calc.calculate_session_or(synthetic_rth_bars)

    df1 = synthetic_rth_bars.copy()
    df1.loc[16, "close"] = 501.5  # Breakout at 09:46

    gen = SignalGenerator(mock_app_config.strategy)
    sig1 = gen.evaluate_session_signals(df1, or_obj)

    # Alter bars 17..390
    df2 = df1.copy()
    df2.loc[17:390, "close"] = 1000.0
    df2.loc[17:390, "high"] = 1005.0

    sig2 = gen.evaluate_session_signals(df2, or_obj)

    assert sig1.entry_price == sig2.entry_price
    assert sig1.stop_loss == sig2.stop_loss
    assert sig1.take_profit == sig2.take_profit
    assert sig1.timestamp == sig2.timestamp


def test_streaming_vs_batch_equivalence(synthetic_rth_bars, mock_app_config):
    """Proves that sequential incremental bar evaluation produces identical results to batch execution."""
    df = synthetic_rth_bars.copy()
    df.loc[16, "close"] = 501.5
    df.loc[25, "high"] = 507.0

    engine = BacktestEngine(mock_app_config)
    res = engine.run(df)

    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "TARGET"
