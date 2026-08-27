import pandas as pd
from src.common.config import load_config
from src.evaluation.metrics import generate_performance_report
from src.visualization.performance_plotter import PerformancePlotter
from src.visualization.candlestick_plotter import CandlestickTradePlotter
from src.backtest.engine import BacktestResult

# 1. Load configuration and saved outputs
cfg = load_config("config/default_config.yaml")
run_dir = "results/backtest/SPY_baseline_v1"

trades_df = pd.read_csv(f"{run_dir}/trades.csv")
equity_df = pd.read_csv(f"{run_dir}/equity_curve.csv")
processed_df = pd.read_parquet("data/processed/SPY/sessions.parquet")

# 2. Compute performance metrics
metrics = generate_performance_report(trades_df, equity_df, cfg)

# 3. Generate all portfolio performance charts
perf_plotter = PerformancePlotter(plots_base_dir="plots")
perf_plots = perf_plotter.generate_all_plots(
    backtest_result=BacktestResult(
        trades=[],
        trades_df=trades_df,
        equity_curve=equity_df,
        daily_summary=pd.DataFrame(),
        initial_capital=cfg.execution.initial_capital,
        final_capital=(
            equity_df["equity"].iloc[-1]
            if not equity_df.empty
            else cfg.execution.initial_capital
        ),
    ),
    metrics=metrics,
)
print("Portfolio plots saved:", perf_plots)

# 4. Generate all individual trade candlestick charts
candle_plotter = CandlestickTradePlotter(output_dir="plots/trades")
# Plot all trades (or pass max_plots=None to plot every trade)
trade_plots = candle_plotter.plot_all_trades(
    trades=[],  # Can iterate over trades_df rows or reconstructed Trade objects
    processed_bars=processed_df,
)
print(f"Candlestick trade charts saved to plots/trades/")
