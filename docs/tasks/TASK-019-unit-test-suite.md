# TASK-019: Comprehensive Unit Test Suite

## Objective

Implement a modular, isolated unit test suite under `tests/unit/` using `pytest` to verify the mathematical correctness, boundary constraints, and error handling of every standalone component in the system.

## Scope

### In Scope

- Creation of `tests/` directory structure:
  - `tests/conftest.py` (shared fixtures, synthetic bar generators)
  - `tests/unit/test_config.py`
  - `tests/unit/test_time_utils.py`
  - `tests/unit/test_validator.py`
  - `tests/unit/test_opening_range.py`
  - `tests/unit/test_signals.py`
  - `tests/unit/test_execution_model.py`
  - `tests/unit/test_backtest_engine.py`
  - `tests/unit/test_metrics.py`
- Synthetic data fixtures generating deterministic 1-minute OHLCV bar sessions for tests.
- Comprehensive unit coverage across edge cases:
  - Timezone conversions during DST transitions.
  - Empty sessions and single-bar sessions.
  - Inverted or invalid OHLC bars.
  - Exactly matched breakout prices ($\text{Close} = \text{OR High}$).
  - Dual-touch bars (confirming Stop Loss executes first).
  - Trades entering on the final trading minute (15:58).
  - Zero-division scenarios in metric calculations.

### Out of Scope

- Live Alpaca API network requests (mocked or offline only).
- Visual chart pixel-level regression testing.

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004
- TASK-007
- TASK-008
- TASK-009
- TASK-010
- TASK-011
- TASK-012
- TASK-013
- TASK-014
- TASK-015

## Requirements

1. All tests must execute quickly and deterministically offline without network calls or external API dependencies.
2. Unit tests must achieve $>90\%$ code coverage across `src/common/`, `src/strategy/`, `src/backtest/`, and `src/evaluation/`.
3. Include explicit assertions for the conservative dual-touch rule, one-trade-per-day limit, and 15:59 ET force-flattening.

## Implementation Details

1. Create `tests/conftest.py`:
   - Define fixture `synthetic_rth_bars()`: generates a DataFrame with 390 1-minute bars representing a standard trading day from 09:30 to 16:00 ET.
   - Define fixture `mock_app_config()`: generates valid test `AppConfig`.
2. Implement unit test modules under `tests/unit/`.

## Interfaces / Contracts

```text
tests/
├── conftest.py
└── unit/
    ├── test_config.py
    ├── test_time_utils.py
    ├── test_validator.py
    ├── test_opening_range.py
    ├── test_signals.py
    ├── test_execution_model.py
    ├── test_backtest_engine.py
    └── test_metrics.py
```

## Data / File Changes

- Create `tests/conftest.py`
- Create `tests/unit/__init__.py`
- Create `tests/unit/test_config.py`
- Create `tests/unit/test_time_utils.py`
- Create `tests/unit/test_validator.py`
- Create `tests/unit/test_opening_range.py`
- Create `tests/unit/test_signals.py`
- Create `tests/unit/test_execution_model.py`
- Create `tests/unit/test_backtest_engine.py`
- Create `tests/unit/test_metrics.py`

## Validation

1. Run `pytest tests/unit/ -v` and verify all test cases pass without warnings or errors.
2. Check test execution speed: entire unit test suite must complete in $< 5$ seconds.

## Acceptance Criteria

- [ ] All unit test files are implemented and passing.
- [ ] Dual-touch resolution test verifies Stop Loss executes when both SL and TP are touched in the same bar.
- [ ] One-trade-per-day test verifies no subsequent trades are permitted after the first trade closes.
- [ ] Force-flatten test verifies open positions are liquidated at 15:59:00 ET.
- [ ] No unit tests make external network calls.

## Notes

- Fast, deterministic unit tests provide continuous confidence during future refactoring and feature additions.
