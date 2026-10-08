"""
tests/conftest.py
=================
Shared pytest fixtures and synthetic data generators for unit and integration testing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.common.config import (
    AppConfig,
    DataConfig,
    ExecutionConfig,
    FiltersConfig,
    OutputConfig,
    StrategyConfig,
)


@pytest.fixture
def mock_app_config() -> AppConfig:
    """Fixture providing a standard test AppConfig."""
    return AppConfig(
        schema_version="1.0",
        strategy=StrategyConfig(
            ticker="SPY",
            opening_range_minutes=15,
            target_r=2.0,
            breakout_buffer_pct=0.0,
            breakout_confirmation="close",
            max_trades_per_day=1,
            force_exit_time="15:59:00",
            stop_method="opposite_range",
            direction_mode="both",
        ),
        filters=FiltersConfig(
            rvol_filter_enabled=False,
            rvol_threshold=1.5,
            vwap_filter_enabled=False,
            market_regime_filter_enabled=False,
        ),
        data=DataConfig(
            symbol="SPY",
            feed="iex",
            timeframe="1Min",
            timezone="America/New_York",
            is_paper=True,
        ),
        execution=ExecutionConfig(
            initial_capital=100_000.0,
            position_sizing="fixed_risk",
            risk_per_trade_pct=0.01,
            fixed_shares=100,
            slippage_per_share=0.01,
            commission_per_share=0.0035,
        ),
        output=OutputConfig(
            results_dir="results/backtest",
            plots_dir="plots",
        ),
    )


@pytest.fixture
def synthetic_rth_bars() -> pd.DataFrame:
    """Generates 391 1-minute bars for a single clean trading day
    (09:30 to 16:00 ET).
    """
    date_str = "2024-01-02"
    open_et = pd.Timestamp(f"{date_str} 09:30:00", tz="America/New_York")
    ts = pd.date_range(start=open_et, periods=391, freq="1min")

    n = len(ts)
    opens = np.full(n, 500.0)
    highs = np.full(n, 500.5)
    lows = np.full(n, 499.5)
    closes = np.full(n, 500.0)
    volumes = np.full(n, 1000.0)

    # Set specific OR boundaries in first 15 bars (0..14)
    highs[5] = 501.0
    lows[8] = 499.0

    return pd.DataFrame(
        {
            "session_id": np.full(n, date_str),
            "timestamp": ts,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "minute_of_day": np.arange(n, dtype=np.int32),
            "is_opening_range": np.arange(n) < 15,
            "is_trading_window": (np.arange(n) >= 15) & (np.arange(n) < 389),
            "is_force_exit": np.arange(n) >= 389,
        }
    )


def pytest_addoption(parser):
    parser.addoption(
        "--real-provider",
        action="store_true",
        default=False,
        help="Explicitly enable tests marked real_provider",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--real-provider"):
        for item in items:
            if item.get_closest_marker("real_provider"):
                item.add_marker(pytest.mark.skip(reason="requires --real-provider"))


@pytest.fixture(autouse=True)
def offline_network_guard(request, monkeypatch):
    """Fail before DNS/connect, in this process AND all Python subprocesses."""
    import os
    import socket
    from pathlib import Path

    from tests.support.sitecustomize import deny_network

    if request.node.get_closest_marker("real_provider") and request.config.getoption(
        "--real-provider"
    ):
        return
    monkeypatch.setenv("ORB_TEST_OFFLINE", "1")
    support = str(Path(__file__).parent / "support")
    monkeypatch.setenv(
        "PYTHONPATH", support + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
