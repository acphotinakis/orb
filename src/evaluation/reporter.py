"""
src.evaluation.reporter
=======================
Trade reporting and results artifact exporter for the SPY ORB system.

Exports trades.csv, equity_curve.csv, daily_summary.csv, metrics.json,
and prints executive summary tables to the terminal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd

from src.backtest.engine import BacktestResult
from src.common.logger import get_logger

logger = get_logger(__name__)


class ResultsReporter:
    """Orchestrates formatting and disk persistence of backtest artifacts."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        base_results_dir: Optional[str] = None,
    ) -> None:
        """Initialise the ResultsReporter.

        Args:
            output_dir: Fully-resolved output directory (from PathManager).
                When provided, ``base_results_dir`` is ignored.
            base_results_dir: Legacy fallback base directory. Used only when
                ``output_dir`` is ``None``.
        """
        if output_dir is not None:
            self.output_dir = output_dir
            self._using_path_manager = True
        elif base_results_dir is not None:
            self.output_dir = Path(base_results_dir)
        else:
            self.output_dir = Path("results/backtest")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_all(
        self,
        backtest_result: BacktestResult,
        metrics: Dict[str, Any],
        run_id: Optional[str] = None,
    ) -> Dict[str, Path]:
        """Export trades.csv, equity_curve.csv, daily_summary.csv, and metrics.json.

        Args:
            backtest_result: Output container from BacktestEngine.run()
            metrics: Performance metrics dictionary from generate_performance_report()
            run_id: Deprecated. Ignored when output_dir was provided at init.

        Returns:
            Dict mapping artifact names to their written Path objects.
        """
        # If run_id provided but no PathManager was used, fall back to old behaviour
        if run_id is not None and not hasattr(self, "_using_path_manager"):
            run_dir = self.output_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
        else:
            run_dir = self.output_dir

        artifact_paths: Dict[str, Path] = {}

        # 1. Export trades.csv
        trades_path = run_dir / "trades.csv"
        trades_df = backtest_result.trades_df.copy()
        if trades_df.empty and backtest_result.trades:
            trades_df = pd.DataFrame([t.to_dict() for t in backtest_result.trades])

        # Round price and financial metrics to 4 decimal places for compact storage
        if not trades_df.empty:
            price_cols = [
                c
                for c in trades_df.columns
                if any(
                    k in c.lower()
                    for k in [
                        "price",
                        "pnl",
                        "capital",
                        "equity",
                        "r_multiple",
                        "slippage",
                        "commission",
                    ]
                )
                and pd.api.types.is_float_dtype(trades_df[c])
            ]
            trades_df[price_cols] = trades_df[price_cols].round(4)

        trades_df.to_csv(trades_path, index=False)
        artifact_paths["trades_csv"] = trades_path

        # 2. Export equity_curve.csv
        equity_path = run_dir / "equity_curve.csv"
        backtest_result.equity_curve.to_csv(equity_path, index=False)
        artifact_paths["equity_curve_csv"] = equity_path

        # 3. Export daily_summary.csv
        daily_path = run_dir / "daily_summary.csv"
        backtest_result.daily_summary.to_csv(daily_path, index=False)
        artifact_paths["daily_summary_csv"] = daily_path

        # 4. Export metrics.json
        metrics_path = run_dir / "metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, default=str)
        artifact_paths["metrics_json"] = metrics_path

        logger.info("Exported 4 backtest artifacts to %s", run_dir)
        return artifact_paths

    def display_console_summary(self, metrics: Dict[str, Any]) -> None:
        """Prints a structured ASCII summary table of performance metrics to stdout."""
        tm = metrics.get("trade_metrics", {})
        pm = metrics.get("portfolio_metrics", {})
        strat = metrics.get("strategy", {})

        print("\n" + "=" * 65)
        print(f"       SPY OPENING RANGE BREAKOUT (ORB) PERFORMANCE REPORT       ")
        print("=" * 65)

        if strat:
            print(
                f" Target Ticker: {strat.get('ticker', 'SPY')} | OR Window: {strat.get('opening_range_minutes', 15)}m | Target: {strat.get('target_r', 2.0)}R"
            )
            print("-" * 65)

        print(" [TRADE STATISTICS]")
        print(
            f"  Total Trades:           {tm.get('total_trades', 0):<8} | Win Rate:             {tm.get('win_rate', 0.0)*100:.1f}%"
        )
        print(
            f"  Long Trades:            {tm.get('long_trades', 0):<8} | Short Trades:         {tm.get('short_trades', 0)}"
        )
        print(
            f"  Win / Loss Count:       {tm.get('win_count', 0)} / {tm.get('loss_count', 0):<4} | Profit Factor:        {tm.get('profit_factor', 0.0)}"
        )
        print(
            f"  Total Realized R:       {tm.get('total_realized_r', 0.0):<8} | Average R / Trade:    {tm.get('avg_r', 0.0):.3f}R"
        )
        print(
            f"  Mathematical Expectancy:{tm.get('expectancy_r', 0.0):<8} | Payoff Ratio:         {tm.get('payoff_ratio', 0.0):.2f}"
        )
        print(
            f"  Total Net P&L:          ${tm.get('total_pnl_dollars', 0.0):<8,.2f} | Best / Worst ($):    ${tm.get('best_trade_dollars', 0.0):,.0f} / ${tm.get('worst_trade_dollars', 0.0):,.0f}"
        )
        print(
            f"  Exit Breakdown:         Target: {tm.get('exit_reasons', {}).get('TARGET', 0)} | Stop: {tm.get('exit_reasons', {}).get('STOP', 0)} | EOD: {tm.get('exit_reasons', {}).get('EOD', 0)}"
        )

        print("-" * 65)
        print(" [PORTFOLIO & RISK METRICS]")
        print(
            f"  Initial Capital:        ${pm.get('initial_capital', 0.0):<8,.2f} | Ending Equity:        ${pm.get('ending_equity', 0.0):,.2f}"
        )
        print(
            f"  Total Return:           {pm.get('total_return_pct', 0.0):<8.2f}% | Annualized (CAGR):    {pm.get('cagr_pct', 0.0):.2f}%"
        )
        print(
            f"  Sharpe Ratio:           {pm.get('sharpe_ratio', 0.0):<8.2f} | Sortino Ratio:        {pm.get('sortino_ratio', 0.0)}"
        )
        print(
            f"  Max Drawdown:           {pm.get('max_drawdown_pct', 0.0):<8.2f}% | Max DD ($):           ${pm.get('max_drawdown_dollars', 0.0):,.2f}"
        )
        print(f"  Calmar Ratio:           {pm.get('calmar_ratio', 0.0)}")
        print("=" * 65 + "\n")
