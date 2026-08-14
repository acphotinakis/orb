# TASK-007: Data Integrity Validation & Anomaly Cleaning

## Objective

Implement a rigorous data validation and anomaly cleaning module in `src/data/validator.py` to guarantee timestamp monotonicity, eliminate duplicate or corrupt bars, verify OHLC geometric consistency, and audit intraday data gaps.

## Scope

### In Scope

- Validation of incoming bar DataFrames for required schema columns (`timestamp`, `open`, `high`, `low`, `close`, `volume`).
- Monotonicity verification (strict chronological ordering of timestamps).
- Duplicate timestamp detection and deterministic deduplication (keep last or highest volume).
- OHLC geometric consistency checks:
  $$\text{High} \ge \max(\text{Open}, \text{Close}, \text{Low})$$
  $$\text{Low} \le \min(\text{Open}, \text{Close}, \text{High})$$
  $$\text{Open} > 0, \quad \text{High} > 0, \quad \text{Low} > 0, \quad \text{Close} > 0, \quad \text{Volume} \ge 0$$
- NaN, Null, and Infinite value auditing.
- Intraday gap detection (identifying gaps $> 5$ consecutive missing 1-minute bars during market hours).
- Detailed validation report output summarizing cleaned anomalies.

### Out of Scope

- RTH filtering or session splitting (handled in TASK-008).
- Strategy signal calculation.

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-004

## Requirements

1. Provide `validate_and_clean_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]`.
2. Automatically drop or repair invalid rows (e.g. zero price bars or duplicate timestamps) while logging warnings.
3. If more than 1% of bars in a session violate OHLC sanity checks, raise `DataValidationError`.
4. Check for missing bars during regular trading hours ($09:30 - 16:00$ ET):
   - Minor gaps ($\le 5$ minutes): log notice.
   - Major gaps ($> 5$ minutes): flag session as incomplete in validation report.

## Implementation Details

1. Create `src/data/validator.py`.
2. Define `ValidationReport` dataclass:
   ```python
   @dataclass
   class ValidationReport:
       total_bars_input: int
       total_bars_output: int
       duplicates_removed: int
       invalid_ohlc_removed: int
       missing_timestamps_count: int
       flagged_sessions: list[str]
       is_valid: bool
   ```
3. Implement vectorized validation functions using NumPy/Pandas for fast execution over millions of rows.

## Interfaces / Contracts

```python
# src/data/validator.py

import pandas as pd
from dataclasses import dataclass
from typing import Tuple

@dataclass
class ValidationReport:
    total_bars_input: int
    total_bars_output: int
    duplicates_removed: int
    invalid_ohlc_removed: int
    missing_timestamps_count: int
    is_valid: bool

def validate_and_clean_bars(
    df: pd.DataFrame,
    strict: bool = False,
) -> Tuple[pd.DataFrame, ValidationReport]:
    """
    Validates and cleans 1-minute OHLCV bar DataFrame.
    Enforces monotonicity, removes duplicates, checks OHLC consistency.
    """
    ...
```

## Data / File Changes

- Create `src/data/validator.py`

## Validation

1. Create synthetic DataFrames with known anomalies:
   - Inverted High/Low (`High < Low`)
   - Negative prices / volumes
   - Duplicate timestamps
   - Out-of-order timestamps
   - Unhandled NaNs
2. Assert that `validate_and_clean_bars()` cleans all repairable anomalies and reports exact metrics in `ValidationReport`.

## Acceptance Criteria

- [ ] Out-of-order bars are sorted chronologically.
- [ ] Duplicate timestamps are detected and removed.
- [ ] Inconsistent bars ($\text{Low} > \text{High}$) are purged.
- [ ] `ValidationReport` accurately records all sanitization events.

## Notes

- Garbage in, garbage out: corrupt high/low values can trigger false breakouts or phantom stop-outs if not cleaned.
