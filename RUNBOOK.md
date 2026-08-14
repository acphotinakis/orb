# Operational Runbook: SPY Opening Range Breakout (ORB) System

This runbook provides complete operational guidance for setup, configuration, CLI backtesting execution, performance evaluation, and troubleshooting.

---

## 1. System Overview & Trading Mechanics

The **SPY Opening Range Breakout (ORB)** system is an institutional-grade quantitative backtesting framework implementing a 15-minute intraday momentum breakout strategy on SPY 1-minute historical bars.

### Core Strategy Rules
1. **Regular Trading Hours (RTH):** All timestamps are strictly normalized to `America/New_York` ($09:30:00 - 16:00:00$ ET). Pre-market and post-market bars are discarded.
2. **Opening Range (OR) Construction ($09:30 - 09:44$ ET):**
   $$\text{OR High} = \max_{t \in [09:30, 09:45)} \text{High}_t, \quad \text{OR Low} = \min_{t \in [09:30, 09:45)} \text{Low}_t$$
3. **Range State Freezing:** Range boundaries are frozen at $09:45:00$ ET and remain immutable for the rest of the day.
4. **Breakout Signal Criteria ($09:45 - 15:58$ ET):**
   - **Long Breakout:** $\text{Close}_t > \text{OR High} + \text{Buffer}$ $\rightarrow$ $\text{Stop} = \text{OR Low}$, $\text{Target} = \text{Entry} + 2 \times (\text{Entry} - \text{Stop})$
   - **Short Breakout:** $\text{Close}_t < \text{OR Low} - \text{Buffer}$ $\rightarrow$ $\text{Stop} = \text{OR High}$, $\text{Target} = \text{Entry} - 2 \times (\text{Stop} - \text{Entry})$
5. **Conservative Dual-Touch Resolution:** If a 1-minute bar touches both Stop Loss and Take Profit levels, the engine **always assumes Stop Loss occurred first** (`ExitReason.STOP`).
6. **Execution Frictions & Sizing:**
   - Fixed-risk position sizing ($\text{Risk} = 1\%$ of equity) with no leverage.
   - Adverse slippage ($+\$0.01$/share entry, $-\$0.01$/share stop exit) and round-trip commissions ($\$0.0035$/share per fill).
7. **End-of-Day Flattening ($15:59:00$ ET):** All open positions are force-closed at the close price (`ExitReason.EOD`). Zero overnight risk.

---

## 2. Environment Setup

### Prerequisites
- Linux OS
- Python 3.9+ (or virtual environment)

### Installation
```bash
# 1. Clone repository and navigate to root
cd /path/to/orb

# 2. Install dependencies
pip install -r requirements.txt
```

### Alpaca Credentials Configuration
Create a `.env` file in the repository root:
```env
# Paper Trading Credentials (Default)
PAPER_APCA_API_KEY_ID=your_paper_key_here
PAPER_APCA_API_SECRET_KEY=your_paper_secret_here

# Live Trading Credentials (Optional)
APCA_API_KEY_ID=your_live_key_here
APCA_API_SECRET_KEY=your_live_secret_here
```

---

## 3. CLI Command Reference

The primary entry point is `src/main.py`:

```bash
# Run baseline backtest using default config (with auto-download & caching)
python -m src.main --config config/default_config.yaml

# Run backtest over a specific historical window
python -m src.main --start-date 2024-01-01 --end-date 2024-06-30 --symbol SPY

# Force re-download fresh historical data (bypassing disk cache)
python -m src.main --refresh-cache --start-date 2024-01-01 --end-date 2024-12-31

# Headless mode (skip PNG plot generation for faster batch runs)
python -m src.main --no-plots --log-level INFO

# Custom Run ID for output organization
python -m src.main --run-id Q1_2024_benchmark --start-date 2024-01-01 --end-date 2024-03-31
```

---

## 4. Configuration Schema (`config/default_config.yaml`)

```yaml
schema_version: "1.0"

strategy:
  ticker: "SPY"
  opening_range_minutes: 15     # [09:30, 09:45) ET
  target_r: 2.0                 # 2R Take-Profit Target
  breakout_buffer_pct: 0.0      # Buffer beyond range boundary
  breakout_confirmation: "close" # Bar close confirmation
  max_trades_per_day: 1         # Maximum 1 trade per session
  force_exit_time: "15:59:00"   # Hard EOD liquidation time
  stop_method: "opposite_range" # Far side of OR
  direction_mode: "both"        # "both", "long_only", "short_only"

filters:
  rvol_filter_enabled: false
  vwap_filter_enabled: false

data:
  symbol: "SPY"
  feed: "iex"                   # "iex" (free tier) or "sip" (paid)
  timeframe: "1Min"
  timezone: "America/New_York"
  raw_dir: "data/raw/SPY"
  processed_dir: "data/processed/SPY"

execution:
  initial_capital: 100000.0
  position_sizing: "fixed_risk" # "fixed_risk" or "fixed_shares"
  risk_per_trade_pct: 0.01      # 1% risk per trade
  slippage_per_share: 0.01      # $0.01/share adverse slippage
  commission_per_share: 0.0035  # $0.0035/share broker fee

output:
  results_dir: "results/backtest"
  plots_dir: "plots"
```

---

## 5. Generated Artifacts & Directory Structure

Upon completion, artifacts are exported to:

```text
results/backtest/{run_id}/
├── trades.csv         # Full trade execution log
├── equity_curve.csv   # Minute-by-minute portfolio equity series
├── daily_summary.csv  # Daily P&L and session outcomes
└── metrics.json       # Quantitative performance report

plots/
├── equity_curves/
│   └── equity_curve.png     # Cumulative equity trajectory vs benchmark
├── drawdowns/
│   └── drawdown_curve.png   # Underwater peak-to-trough drawdown profile
├── distributions/
│   ├── r_multiples.png      # Realized R-multiple distribution histogram
│   └── trade_durations.png  # Holding time distribution histogram
└── trades/
    └── trade_*.png          # Candlestick chart per executed trade
```

---

## 6. Metrics Glossary

| Metric | Formula / Definition | Purpose |
| :--- | :--- | :--- |
| **Win Rate ($W$)** | $\frac{\text{Win Count}}{\text{Total Trades}}$ | Percentage of profitable trades. |
| **Expectancy ($E_R$)** | $W \times \bar{R}_{\text{win}} - (1-W) \times |\bar{R}_{\text{loss}}|$ | Expected return in units of risk ($R$) per trade. |
| **Profit Factor** | $\frac{\sum \text{Gross Profits}}{\sum \|\text{Gross Losses}\|}$ | Ratio of gross profits to gross losses. |
| **Sharpe Ratio** | $\frac{\bar{R}_{\text{daily}} - r_f}{\sigma_{\text{daily}}} \times \sqrt{252}$ | Risk-adjusted return annualized across 252 trading days. |
| **Sortino Ratio** | $\frac{\bar{R}_{\text{daily}} - r_f}{\sigma_{\text{downside}}} \times \sqrt{252}$ | Return adjusted specifically for downside volatility. |
| **Max Drawdown (MDD)** | $\max_t \left( \frac{\text{HWM}_t - \text{Equity}_t}{\text{HWM}_t} \right)$ | Largest peak-to-trough portfolio equity drop. |
| **Calmar Ratio** | $\frac{\text{CAGR}}{\text{MDD}}$ | Annualized growth rate relative to maximum drawdown. |

---

## 7. Troubleshooting & Common Issues

- **`DataFetchError: Missing Alpaca API credentials`**: Ensure your `.env` contains `PAPER_APCA_API_KEY_ID` and `PAPER_APCA_API_SECRET_KEY`.
- **`TemporalLeakageError: Post-OR bar included in opening range`**: Ensure raw bars contain proper timestamps and timezone normalisation was executed.
- **`Empty DataFrame returned from Alpaca`**: Free Alpaca IEX data is delayed by 15 minutes. Querying current day intraday before market close may yield empty payloads unless `--end-date` is lagged.
