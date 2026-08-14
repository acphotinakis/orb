"""
tests/unit/test_opening_range.py
================================
Unit tests for Opening Range calculation engine and causality guards.
"""

import pytest
import pandas as pd
from src.strategy.opening_range import OpeningRangeCalculator, OpeningRange
from src.common.exceptions import TemporalLeakageError


def test_opening_range_calculation(synthetic_rth_bars):
    calc = OpeningRangeCalculator(or_minutes=15)
    or_res = calc.calculate_session_or(synthetic_rth_bars)

    assert isinstance(or_res, OpeningRange)
    assert or_res.or_high == 501.0
    assert or_res.or_low == 499.0
    assert or_res.or_width == 2.0
    assert or_res.bar_count == 15
    assert or_res.is_valid is True


def test_opening_range_temporal_leakage_guard(synthetic_rth_bars):
    calc = OpeningRangeCalculator(or_minutes=15)
    df = synthetic_rth_bars.copy()
    # Mark a 09:50 bar as opening range
    df.loc[20, "is_opening_range"] = True

    with pytest.raises(TemporalLeakageError):
        calc.calculate_session_or(df)
