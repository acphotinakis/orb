"""
orb-spy-alpaca: SPY Opening Range Breakout quantitative trading system.

This package provides the full pipeline for data ingestion, strategy signal
generation, event-driven backtesting, performance evaluation, and
visualization for the SPY ORB strategy using Alpaca Markets data.

Subpackages
-----------
common
    Shared utilities: configuration, timezone helpers, logging, and exceptions.
data
    Alpaca data client, raw bar fetching/caching, validation, and RTH processing.
strategy
    Opening range calculation engine and breakout signal generation.
backtest
    Event-driven backtest engine, order/position models, and execution simulation.
evaluation
    Performance and risk-adjusted metrics, and results artifact reporting.
visualization
    Candlestick trade charts and portfolio performance curve plotters.
"""

__version__ = "1.0.0"
__author__ = "ORB Quantitative Research"
