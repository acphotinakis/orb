# Quantitative System Architecture Plan: SPY Opening Range Breakout (ORB)

**System Name:** `orb-spy-alpaca`  
**Target Asset:** `SPY` (SPDR S&P 500 ETF Trust)  
**Data Provider:** Alpaca Markets Data API (`alpaca-py`)  
**Design Role:** Senior Quantitative Systems Architect  
**Document Status:** Approved Architectural Specification  
**Specification Version:** `v1.0.0`

---

## 1. Executive Summary & Strategy Thesis

The **Opening Range Breakout (ORB)** strategy exploits institutional price discovery and volatility expansion following the U.S. equity market open (**9:30 AM ET**).

```text
09:30 ET                      09:45 ET                                16:00 ET
   │                              │                                       │
   ├──────────────────────────────┼───────────────────────────────────────┤
   │  OPENING RANGE FORMATION     │       ACTIVE BREAKOUT TRADING         │ FORCE EOD
   │  Compute OR High & OR Low    │  Signal Long/Short, Manage SL/TP      │ FLATTEN
   └──────────────────────────────┴───────────────────────────────────────┘
```

### Core Strategy Rules (Version 1 Baseline)
1. **Trading Universe:** `SPY` (high liquidity, tight spreads, institutional benchmark).
2. **Session Window:** Regular Trading Hours (RTH) 09:30:00 – 16:00:00 US/Eastern.
3. **Opening Range (OR) Duration:** First 15 minutes (09:30:00 – 09:44:59 ET).
   - $\text{OR High} = \max(\text{High}_{09:30 \dots 09:44})$
   - $\text{OR Low} = \min(\text{Low}_{09:30 \dots 09:44})$
   - $\text{OR Width} = \text{OR High} - \text{OR Low}$
4. **Entry Signals (Bar-Close Confirmation):**
   - **Long Entry:** 1-minute $\text{Close}_t > \text{OR High}$
   - **Short Entry:** 1-minute $\text{Close}_t < \text{OR Low}$
5. **Trade Frequency Filter:** Maximum 1 trade per session (prevents whip-saw on choppy days).
6. **Risk Management & Position Sizing:**
   - **Long Stop Loss:** $\text{OR Low}$
   - **Long Profit Target:** $\text{Entry} + 2 \times (\text{Entry} - \text{Stop Loss})$ ($2R$ reward-to-risk)
   - **Short Stop Loss:** $\text{OR High}$
   - **Short Profit Target:** $\text{Entry} - 2 \times (\text{Stop Loss} - \text{Entry})$ ($2R$ reward-to-risk)
7. **Time Stop / Session Termination:** Hard exit at 15:59:00 / 16:00:00 ET. No overnight risk.

---

## 2. Directory & Storage Architecture

To ensure clean separation of concerns, reproducible research, and maintainable artifacts, the repository storage hierarchy is structured as follows:

```text
orb/
├── .env                              # Alpaca credentials & environment secrets
├── requirements.txt                  # Python dependencies
├── config/                           # Configuration schemas & YAML profiles
│   ├── default_config.yaml           # Master baseline configuration
│   └── grid_search.yaml              # Hyperparameter search configs
├── data/                             # Data persistence layer
│   ├── raw/                          # Raw historical bar data from Alpaca
│   │   └── SPY/                      # Partitioned by ticker
│   │       ├── SPY_1min_2024.parquet
│   │       └── SPY_1min_2025.parquet
│   └── processed/                    # Cleaned, timezone-aligned, session-split bars
│       └── SPY/
│           ├── sessions.parquet      # RTH-filtered bars with session IDs
│           └── daily_ranges.parquet  # Precomputed OR High, OR Low, ATR
├── results/                          # Structured output artifacts
│   ├── backtest/
│   │   └── SPY_baseline_v1/
│   │       ├── trades.csv            # Individual trade log with R-multiples
│   │       ├── equity_curve.csv      # Bar-by-bar portfolio equity series
│   │       ├── daily_summary.csv     # Session-level P&L and metrics
│   │       └── metrics.json          # Machine-readable performance metrics
│   └── parameter_studies/            # Results from duration/target sweeps
├── plots/                            # High-resolution visual artifacts
│   ├── equity_curves/                # Cumulative P&L and benchmark comparisons
│   ├── drawdowns/                    # Underwater drawdown charts
│   ├── distributions/                # R-multiple and trade duration histograms
│   └── trades/                       # Candlestick charts with OR, SL, TP markers
├── docs/                             # Documentation and specifications
│   ├── overview/                     # Conceptual strategy overviews
│   └── plans/                        # Architectural blueprints and plans
└── src/                              # Modular Python source code
    ├── common/                       # Utilities, logger, timezone helpers
    ├── data/                         # Ingestion, validation, caching
    ├── strategy/                     # Signal generation & OR calculation
    ├── backtest/                     # Simulation engine & order execution
    ├── evaluation/                   # Statistical & trading metrics
    └── visualization/                # Candlestick & performance plotters
```

---

## 3. Strict Temporal Causality & Anti-Leakage Protocols

As a quantitative trading system, zero tolerance is enforced for look-ahead bias and data leakage.

### Non-Negotiable Causality Guardrails:

```
                                BAR t
           ┌──────────────────────────────────────────┐
           │ Open       High       Low       Close    │
           │  │          │          │          │      │
           └──┼──────────┼──────────┼──────────┼──────┘
              ▼          ▼          ▼          ▼
         (During bar: not locked)      (Bar Close: Signal finalized)
                                               │
                                               ▼
                                      EXECUTE AT BAR t+1 OPEN
                                      (or simulate conservative close)
```

1. **Opening Range Finality:**
   - The Opening Range is calculated strictly on bars timestamped $[09:30:00, 09:45:00)$.
   - Under no circumstances can bars $\ge 09:45:00$ influence the $\text{OR High}$ or $\text{OR Low}$.
2. **Breakout Execution Realism:**
   - Signal evaluation occurs on bar close $t$.
   - Entry order fills at bar $t$ close (idealized) or bar $t+1$ open (execution with latency).
3. **Simultaneous SL & TP Touch Resolution (Conservative Fill Assumption):**
   - If a 1-minute bar's $\text{High} \ge \text{Take Profit}$ **AND** $\text{Low} \le \text{Stop Loss}$ in the same bar, the backtester **MUST assume Stop Loss was hit first**.
   - This eliminates optimistic bias inherent in coarse bar data.
4. **Timezone Standardization:**
   - All historical data fetched from Alpaca is timestamped in UTC and must be explicitly converted to `America/New_York` to prevent daylight savings shifts from skewing market open/close boundaries.

---

## 4. Module & Component Specifications

### 4.1 Data Pipeline (`src/data/`)
* **`alpaca_client.py`**: Robust wrapper over `alpaca.data.historical.StockHistoricalDataClient` with automatic pagination, rate-limit retries, and credential loading from `.env`.
* **`fetcher.py`**: High-level ingestion pipeline that downloads 1-minute historical bars for any requested date range, saves to `data/raw/{ticker}/`, and validates schema.
* **`processor.py`**:
  - Filters data to Regular Trading Hours (09:30 – 16:00 ET).
  - Handles exchange holidays and half-days (early close at 13:00 ET).
  - Flags gaps $> 5$ consecutive missing minutes.
  - Outputs partitioned parquet files to `data/processed/{ticker}/`.

### 4.2 Strategy Engine (`src/strategy/`)
* **`config.py`**: Dataclass defining all strategy hyperparameters:
  ```python
  @dataclass
  class ORBStrategyConfig:
      ticker: str = "SPY"
      opening_range_minutes: int = 15
      target_r: float = 2.0
      breakout_buffer_pct: float = 0.0
      breakout_confirmation: str = "close"  # "close" or "intrabar"
      max_trades_per_day: int = 1
      force_exit_time: str = "15:59:00"
      stop_method: str = "opposite_range"   # "opposite_range" or "atr"
      rvol_filter_enabled: bool = False
      rvol_threshold: float = 1.5
      vwap_filter_enabled: bool = False
  ```
* **`opening_range.py`**: Computes session-by-session opening range bands and summary statistics.
* **`signals.py`**: Generates Long/Short entry signals, stop loss levels, and profit targets with zero forward-looking references.

### 4.3 Simulation & Backtesting (`src/backtest/`)
* **`engine.py`**: Event-driven / stateful bar iterator executing orders across trading sessions.
* **`execution_model.py`**: Simulates slippage ($0.01/share standard on SPY) and commissions ($0.0035/share or zero for retail tier).
* **`order.py` & `position.py`**: Explicit position state tracking (`FLAT`, `LONG`, `SHORT`), trailing stop logic, and exit logging (`TP`, `SL`, `EOD`).

### 4.4 Evaluation & Performance Metrics (`src/evaluation/`)
* **`metrics.py`**: Computes quantitative statistics and exports `metrics.json`:
  - **Statistical Metrics:** Win Rate, Loss Rate, Total Trades, Trade Frequency, Average R-Multiple, Expectancy ($E = W \times R_{win} - L \times R_{loss}$).
  - **Portfolio Metrics:** Total Return %, CAGR %, Sharpe Ratio, Sortino Ratio, Calmar Ratio, Maximum Drawdown (MDD %), Max Drawdown Duration.
  - **Execution Metrics:** Profit Factor, Average Win / Loss, Maximum Adverse Excursion (MAE), Maximum Favorable Excursion (MFE).

### 4.5 Visualization Pipeline (`src/visualization/`)
* **`candlestick_plotter.py`**: Uses `mplfinance` / `matplotlib` to render individual trade sessions showing:
  - 1-minute OHLC candlesticks.
  - Shaded 15-minute Opening Range box.
  - Horizontal lines for $\text{OR High}$, $\text{OR Low}$, $\text{Entry}$, $\text{Stop Loss}$, and $\text{Take Profit}$.
  - Marker indicators at entry timestamp and exit timestamp.
* **`performance_plotter.py`**: Generates cumulative equity curves, underwater drawdown charts, and R-multiple distribution histograms.

---

## 5. Storage Formats & Artifact Schemas

### 5.1 Trade Log Schema (`results/backtest/{run_id}/trades.csv`)
| Column | Type | Description |
| :--- | :--- | :--- |
| `trade_id` | `int` | Sequential trade index |
| `date` | `string` | ISO Date `YYYY-MM-DD` |
| `symbol` | `string` | Stock symbol (`SPY`) |
| `direction` | `string` | `LONG` or `SHORT` |
| `entry_time` | `datetime` | Localized timestamp (US/Eastern) |
| `exit_time` | `datetime` | Localized timestamp (US/Eastern) |
| `entry_price` | `float` | Fill price at entry |
| `exit_price` | `float` | Fill price at exit |
| `stop_price` | `float` | Initial stop loss level |
| `target_price` | `float` | Target price ($2R$) |
| `or_high` | `float` | 09:30–09:45 High |
| `or_low` | `float` | 09:30–09:45 Low |
| `or_width` | `float` | Range width in dollars |
| `shares` | `int` | Position size |
| `pnl_dollars` | `float` | Gross P&L in USD |
| `return_pct` | `float` | Percentage return on trade |
| `r_multiple` | `float` | Realized R-multiple |
| `exit_reason` | `string` | `TARGET`, `STOP`, or `EOD` |
| `slippage_paid` | `float` | Total slippage incurred |
| `commission_paid`| `float` | Total commission incurred |

### 5.2 Metrics JSON Schema (`results/backtest/{run_id}/metrics.json`)
```json
{
  "strategy_name": "SPY_ORB_15M_2R",
  "version": "1.0.0",
  "symbol": "SPY",
  "period": {
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "trading_days": 252
  },
  "summary": {
    "total_trades": 184,
    "win_rate": 0.445,
    "profit_factor": 1.62,
    "expectancy_r": 0.335,
    "total_r": 61.64
  },
  "risk_adjusted_returns": {
    "sharpe_ratio": 1.78,
    "sortino_ratio": 2.45,
    "max_drawdown_pct": 0.084,
    "calmar_ratio": 2.12
  },
  "trade_breakdown": {
    "long_trades": 102,
    "short_trades": 82,
    "target_exits": 82,
    "stop_exits": 94,
    "eod_exits": 8
  }
}
```

---

## 6. Phased Implementation & Validation Roadmap

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: Infrastructure & Data Pipeline (Current Baseline)                 │
│ • Directory initialization: data/{raw,processed}, results/, plots/         │
│ • Alpaca 1-min bar fetcher with caching and validation                     │
│ • RTH session splitter and timezone alignment                              │
├────────────────────────────────────────────────────────────────────────────┤
│ PHASE 2: Core Strategy & Backtest Engine                                   │
│ • Config-driven ORB strategy implementation                                │
│ • Conservative bar-level order execution simulator                        │
│ • Trade logging and statistical metric calculation                         │
├────────────────────────────────────────────────────────────────────────────┤
│ PHASE 3: Visualization & Artifact Automation                               │
│ • Candlestick trade chart generator with OR overlays                       │
│ • Equity curve & drawdown visualizers                                      │
│ • Output validation pipeline                                               │
├────────────────────────────────────────────────────────────────────────────┤
│ PHASE 4: Research Extensions & Quantitative Enhancements                   │
│ • Parameter sweeps (5m vs 15m vs 30m OR; 1R vs 1.5R vs 2R vs 3R)           │
│ • RVOL & VWAP confluence filters                                          │
│ • ML Breakout Probability Classifier (Supervised Predictor)                │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Operational Readiness Checklist

- [x] Alpaca API credentials validated in `.env` (Paper and Live endpoints)
- [x] Target asset defined (`SPY`) with full 1-minute historical data requirement
- [x] Data storage directories isolated (`data/raw`, `data/processed`)
- [x] Output artifact directories established (`results/backtest`, `plots/trades`, `plots/equity_curves`)
- [x] Temporal causality protocols specified (strict bar-close confirmation, no future look-ahead)
- [x] Conservative dual-touch SL/TP resolution policy locked
- [x] Reproducible, machine-readable metrics output contract documented
