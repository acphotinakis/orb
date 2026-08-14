# TASK-004: Logging & Error Handling Infrastructure

## Objective

Establish a structured, level-controlled logging system and domain-specific exception hierarchy in `src/common/logger.py` and `src/common/exceptions.py` to facilitate debugging, auditability, and temporal-leakage protection.

## Scope

### In Scope

- Structured console and file logging utility with configurable log levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
- Custom exception classes capturing domain errors:
  - `ConfigurationError`: Invalid or missing configuration schema.
  - `DataFetchError`: Network or API failure when querying Alpaca.
  - `DataValidationError`: Data integrity violations (NaNs, corrupt OHLC, invalid timestamps).
  - `TemporalLeakageError`: Critical error raised when future data access or causality violation is detected.
  - `BacktestExecutionError`: Runtime simulation and order tracking errors.
- Formatting with standardized UTC/Eastern timestamping and execution context.

### Out of Scope

- Implementing strategy signals or data pipelines.

## Dependencies

- TASK-001
- TASK-002

## Requirements

1. Provide a central `get_logger(name: str)` factory function.
2. Ensure log outputs can stream to both `sys.stdout` and an optional log file (e.g. `results/backtest/execution.log`).
3. Domain exceptions must include contextual metadata (e.g., date, bar index, ticker symbol).
4. `TemporalLeakageError` must immediately halt execution when triggered, preventing corrupt backtest results.

## Implementation Details

1. Create `src/common/exceptions.py`:
   - Define custom exception classes inheriting from Python's standard `Exception`.
2. Create `src/common/logger.py`:
   - Implement `setup_logging(log_level: str = "INFO", log_file: Optional[Path] = None) -> logging.Logger`.
   - Implement `get_logger(name: str) -> logging.Logger`.
   - Configure clean, structured formatting: `%(asctime)s [%(levelname)s] %(name)s - %(message)s`.

## Interfaces / Contracts

```python
# src/common/exceptions.py

class ORBBaseException(Exception):
    """Base exception for ORB quantitative trading system."""
    pass

class ConfigurationError(ORBBaseException):
    pass

class DataFetchError(ORBBaseException):
    pass

class DataValidationError(ORBBaseException):
    pass

class TemporalLeakageError(ORBBaseException):
    """Raised when lookahead bias or future information leakage is detected."""
    pass

class BacktestExecutionError(ORBBaseException):
    pass
```

```python
# src/common/logger.py

import logging
from typing import Optional
from pathlib import Path

def setup_logger(
    name: str = "orb",
    level: str = "INFO",
    log_file: Optional[Path] = None,
) -> logging.Logger:
    ...
```

## Data / File Changes

- Create `src/common/exceptions.py`
- Create `src/common/logger.py`

## Validation

1. Verify that `get_logger("test")` emits logs formatted with timestamp and level.
2. Verify that raising each custom exception delivers clear, readable error messages and preserves traceback information.

## Acceptance Criteria

- [ ] All custom exceptions are organized under `src/common/exceptions.py`.
- [ ] Centralized logging is configurable without duplicating log handlers.
- [ ] `TemporalLeakageError` is available across all modules for immediate assertion failure on causality violations.

## Notes

- Keep logging lightweight to avoid slowing down high-frequency bar iteration in backtesting.
