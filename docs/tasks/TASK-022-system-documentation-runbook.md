# TASK-022: System Documentation & Operational Runbook

## Objective

Author comprehensive system documentation and an operational runbook in `README.md` and `docs/RUNBOOK.md` to provide clear setup instructions, CLI execution guides, configuration references, metrics definitions, and troubleshooting protocols for researchers and engineers.

## Scope

### In Scope

- Creation of `docs/RUNBOOK.md` covering:
  - System architecture summary and trading strategy rules.
  - Step-by-step installation and environment configuration (`setup.sh`, `requirements.txt`, `.env`).
  - Alpaca API key configuration guide (Paper vs Live keys).
  - CLI command reference with example invocations:
    - Ingesting raw historical SPY bars.
    - Running full backtests over specific date ranges.
    - Exporting trade logs, equity curves, and metrics.
    - Generating trade candlestick charts and performance plots.
  - Configuration schema documentation (`config/default_config.yaml` parameters).
  - Comprehensive metrics glossary (Expectancy $E_R$, Sharpe, Sortino, Calmar, Profit Factor, Max Drawdown).
  - Troubleshooting guide for common errors (Alpaca connection issues, missing bars, timezone anomalies).
- Updating root `README.md` with executive overview, quickstart instructions, and architecture links.

### Out of Scope

- Modifying core Python source code.

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-005
- TASK-006
- TASK-007
- TASK-008
- TASK-009
- TASK-010
- TASK-011
- TASK-012
- TASK-013
- TASK-014
- TASK-015
- TASK-016
- TASK-017
- TASK-018
- TASK-019
- TASK-020
- TASK-021

## Requirements

1. Provide clear, copy-pasteable shell commands for all common operational workflows.
2. Link directly to `/docs/plans/ORB_SYSTEM_PLAN.md` and related task files.
3. Keep documentation strictly aligned with the implemented system code and configuration schema.

## Implementation Details

1. Create `docs/RUNBOOK.md`.
2. Update root `README.md`:
   - System Overview & Mathematical Basis
   - Quickstart (3 commands to install and run backtest)
   - Directory Hierarchy Map
   - CLI Command Cheat Sheet
   - Running Tests (`pytest tests/`)
   - Links to Architecture Plan and Task Backlog

## Interfaces / Contracts

```text
orb/
├── README.md
└── docs/
    ├── RUNBOOK.md
    ├── plans/
    │   └── ORB_SYSTEM_PLAN.md
    └── tasks/
        └── TASK-*.md
```

## Data / File Changes

- Create `docs/RUNBOOK.md`
- Update `README.md`

## Validation

1. Follow the quickstart instructions in `README.md` on a fresh environment or container; verify that each command executes successfully without missing prerequisites.
2. Validate markdown syntax and link integrity across all documentation files.

## Acceptance Criteria

- [ ] `README.md` provides a complete, polished quickstart guide.
- [ ] `docs/RUNBOOK.md` documents all CLI options, configuration keys, metrics formulas, and troubleshooting steps.
- [ ] All file links and command examples are accurate and functional.

## Notes

- Clear operational documentation ensures seamless onboarding and reproducible quantitative research across team members.
