"""
src.visualization — Chart generation and visual analytics.

Provides:
- CandlestickTradePlotter: mplfinance-based session charts with OR box,
                           SL/TP levels, and entry/exit annotations (TASK-016)
- PerformancePlotter: equity curves, underwater drawdown charts,
                      R-multiple distributions, trade duration histograms (TASK-017)

All charts are rendered headlessly using the matplotlib Agg backend and
saved as high-DPI PNG files to their designated plots/ subdirectories.
"""
