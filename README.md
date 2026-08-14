# SPY Opening Range Breakout (ORB) Quantitative Trading System

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/pytest-passing-brightgreen.svg)](tests/)
[![Architecture](https://img.shields.io/badge/architecture-TRD%20compliant-orange.svg)](docs/plans/ORB_SYSTEM_PLAN.md)

An institutional-grade, event-driven quantitative backtesting engine and execution research framework implementing the 15-minute Opening Range Breakout strategy on SPY.

---

## Key Features

- **Strict Temporal Causality & Anti-Leakage Guards:** State freezing at $09:45:00$ ET with automated unit and mathematical perturbation testing.
- **Conservative Dual-Touch Resolver:** Guarantees that intra-bar dual-touch scenarios (hitting both Stop Loss and Take Profit in the same 1-minute bar) **always resolve to Stop Loss first**.
- **Real-World Execution Modeling:** Adverse slippage simulation, per-share round-trip commission accounting, and fixed-risk position sizing.
- **Publication-Grade Visualizations:** Intraday candlestick trade charts, underwater drawdown profiles, R-multiple distributions, and equity curves.
- **Modular 6-Stage Architecture:** Ingestion $\rightarrow$ Validation $\rightarrow$ RTH Processing $\rightarrow$ Simulation $\rightarrow$ Evaluation $\rightarrow$ Reporting.

---

## Quickstart (3 Commands)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run test suite (unit + anti-leakage audit + E2E integration)
pytest -o pythonpath=. tests/ -v

# 3. Run backtest pipeline
python -m src.main --config config/default_config.yaml
```

---

## System Architecture

```text
orb/
├── config/
│   └── default_config.yaml    # Frozen schema single source of truth
├── data/
│   ├── raw/SPY/               # Raw 1-minute historical Parquet cache
│   └── processed/SPY/         # Cleaned RTH session Parquet datasets
├── docs/
│   ├── RUNBOOK.md             # Operational guide, CLI & metrics reference
│   ├── plans/
│   │   └── ORB_SYSTEM_PLAN.md # Master architecture blueprint
│   └── tasks/                 # Granular TASK-001 through TASK-022
├── plots/
│   ├── distributions/         # R-multiple & duration histograms
│   ├── drawdowns/             # Underwater drawdown profiles
│   ├── equity_curves/         # Cumulative equity vs benchmark
│   └── trades/                # Individual candlestick trade charts
├── results/backtest/
│   └── {run_id}/              # trades.csv, equity_curve.csv, metrics.json
├── src/
│   ├── common/                # config, time_utils, logger, exceptions
│   ├── data/                  # alpaca_client, fetcher, validator, processor
│   ├── strategy/              # opening_range, signals
│   ├── backtest/              # models, execution_model, engine
│   ├── evaluation/            # metrics, reporter
│   ├── visualization/         # candlestick_plotter, performance_plotter
│   ├── pipeline.py            # End-to-End orchestrator
│   └── main.py                # CLI entry point
└── tests/
    ├── conftest.py            # Test fixtures & synthetic bar generators
    ├── unit/                  # Modular unit tests (100% offline)
    ├── anti_leakage/          # Temporal causality perturbation audit
    └── integration/           # End-to-End pipeline integration tests
```

---

## Operational Documentation

- For complete CLI arguments, configuration parameters, and performance formulas, see the **[Operational Runbook](docs/RUNBOOK.md)**.
- For complete system architecture and mathematical foundations, see the **[Master Architecture Plan](docs/plans/ORB_SYSTEM_PLAN.md)**.
