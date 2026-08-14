"""
src.evaluation — Performance metrics, reporting, and artifact persistence.

Provides:
- calculate_trade_metrics: win rate, profit factor, R-multiple statistics (TASK-014)
- calculate_portfolio_metrics: Sharpe, Sortino, Calmar, CAGR, Max Drawdown (TASK-014)
- generate_performance_report: full metrics.json-conformant report builder (TASK-014)
- ResultsReporter: disk persistence of trades.csv, equity_curve.csv,
                   daily_summary.csv, metrics.json (TASK-015)
"""
