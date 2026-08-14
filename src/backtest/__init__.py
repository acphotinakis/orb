"""
src.backtest — Event-driven backtesting simulation layer.

Provides:
- Trade / Position / PositionSide / ExitReason: order and trade state models (TASK-011)
- ExecutionModel: position sizing, slippage, and commission simulation (TASK-012)
- BacktestEngine: sequential bar-by-bar replay with dual-touch resolver (TASK-013)
- BacktestResult: typed container for trades, equity curve, and daily summary (TASK-013)

Critical invariants enforced at this layer:
- Conservative dual-touch resolution (Stop Loss wins when both SL and TP touched).
- One-trade-per-session limit.
- Force-flatten at 15:59:00 America/New_York with ExitReason.EOD.
"""
