"""
tests/unit/test_validator.py
============================
Unit tests for data validation and anomaly cleaning.
"""

import pandas as pd
import numpy as np
import pytest

from src.data.validator import validate_and_clean_bars, ValidationReport
from src.common.exceptions import DataValidationError


def test_validator_clean_data(synthetic_rth_bars):
    df_clean, report = validate_and_clean_bars(synthetic_rth_bars, timeframe="1Min")
    assert len(df_clean) == len(synthetic_rth_bars)
    assert report.is_valid is True
    assert report.duplicates_removed == 0
    assert report.invalid_ohlc_removed == 0


def test_validator_removes_duplicates(synthetic_rth_bars):
    df = synthetic_rth_bars.copy()
    dup_row = df.iloc[10:11].copy()
    dup_row["volume"] = 10.0  # Lower volume duplicate
    df_with_dup = pd.concat([df, dup_row], ignore_index=True)

    df_clean, report = validate_and_clean_bars(df_with_dup, timeframe="1Min")
    assert report.duplicates_removed == 1
    assert len(df_clean) == len(synthetic_rth_bars)


def test_validator_removes_inverted_ohlc(synthetic_rth_bars):
    df = synthetic_rth_bars.copy()
    df.loc[5, "high"] = 490.0  # High lower than Low (499.5)
    df_clean, report = validate_and_clean_bars(df, timeframe="1Min")
    assert report.invalid_ohlc_removed == 1
    assert len(df_clean) == len(synthetic_rth_bars) - 1


def test_validator_strict_mode_raises(synthetic_rth_bars):
    df = synthetic_rth_bars.copy()
    # Corrupt 10% of rows
    df.loc[:40, "high"] = 0.0
    with pytest.raises(DataValidationError):
        validate_and_clean_bars(df, timeframe="1Min", strict=True)
