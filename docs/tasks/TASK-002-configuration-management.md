# TASK-002: Configuration Management & Schema Validation

## Objective

Implement a strongly-typed, validated configuration management module in `src/common/config.py` that loads, validates, and provides structured access to settings defined in `config/default_config.yaml`.

## Scope

### In Scope

- Python dataclasses or Pydantic models mirroring the YAML configuration schema.
- Support for hierarchical config sections: `strategy`, `filters`, `data`, `execution`, and `output`.
- Type checking, value-boundary validation (e.g., `opening_range_minutes > 0`, `target_r > 0`, `risk_per_trade_pct` in $(0, 1]$).
- Default value fallbacks and configuration file loader function (`load_config(path: str | Path)`).
- Serialization/deserialization to and from dictionary / JSON.

### Out of Scope

- Dynamic runtime configuration mutations during active backtest execution.
- Fetching data or executing trades.

## Dependencies

- TASK-001

## Requirements

1. Parse YAML files cleanly using `PyYAML`.
2. Map YAML structures into strongly-typed immutable dataclasses.
3. Validate required fields and enforce constraints:
   - `strategy.ticker`: Must equal `"SPY"` for baseline v1.
   - `strategy.opening_range_minutes`: Must be positive integer (default: 15).
   - `strategy.target_r`: Must be positive float (default: 2.0).
   - `strategy.breakout_confirmation`: Must be `"close"` or `"intrabar"`.
   - `strategy.max_trades_per_day`: Must be positive integer (default: 1).
   - `strategy.force_exit_time`: Must match `"HH:MM:SS"` format (default: `"15:59:00"`).
   - `strategy.stop_method`: Must be `"opposite_range"` or `"atr"`.
   - `data.timezone`: Must equal `"America/New_York"`.
   - `data.timeframe`: Must equal `"1Min"`.
   - `execution.initial_capital`: Must be positive float.
4. Raise descriptive `ValueError` or `ConfigurationError` upon schema or value violations.

## Implementation Details

1. Define config classes in `src/common/config.py`:
   - `StrategyConfig`
   - `FiltersConfig`
   - `DataConfig`
   - `ExecutionConfig`
   - `OutputConfig`
   - `AppConfig` (Master container containing all sub-configs and `schema_version`)
2. Implement `load_config(config_path: str | Path = "config/default_config.yaml") -> AppConfig`.
3. Provide helper `validate_config(config: AppConfig) -> None` ensuring all domain constraints are strictly satisfied.

## Interfaces / Contracts

```python
# src/common/config.py

@dataclass(frozen=True)
class StrategyConfig:
    ticker: str = "SPY"
    opening_range_minutes: int = 15
    target_r: float = 2.0
    breakout_buffer_pct: float = 0.0
    breakout_confirmation: str = "close"
    max_trades_per_day: int = 1
    force_exit_time: str = "15:59:00"
    stop_method: str = "opposite_range"
    direction_mode: str = "both"

@dataclass(frozen=True)
class FiltersConfig:
    rvol_filter_enabled: bool = False
    rvol_threshold: float = 1.5
    vwap_filter_enabled: bool = False
    market_regime_filter_enabled: bool = False

@dataclass(frozen=True)
class DataConfig:
    symbol: str = "SPY"
    feed: str = "iex"
    timeframe: str = "1Min"
    timezone: str = "America/New_York"
    raw_dir: str = "data/raw/SPY"
    processed_dir: str = "data/processed/SPY"

@dataclass(frozen=True)
class ExecutionConfig:
    initial_capital: float = 100000.0
    position_sizing: str = "fixed_risk"
    risk_per_trade_pct: float = 0.01
    fixed_shares: int = 100
    slippage_per_share: float = 0.01
    commission_per_share: float = 0.0035

@dataclass(frozen=True)
class OutputConfig:
    results_dir: str = "results/backtest"
    plots_dir: str = "plots"

@dataclass(frozen=True)
class AppConfig:
    schema_version: str
    strategy: StrategyConfig
    filters: FiltersConfig
    data: DataConfig
    execution: ExecutionConfig
    output: OutputConfig

def load_config(config_path: str | Path = "config/default_config.yaml") -> AppConfig:
    ...
```

## Data / File Changes

- Create `src/common/config.py`
- Reference `config/default_config.yaml`

## Validation

1. Write a test loading `config/default_config.yaml` and assert all fields match expected types and default values.
2. Test error handling when required fields are missing or invalid (e.g., negative `opening_range_minutes` or invalid `timezone`).

## Acceptance Criteria

- [ ] `load_config()` successfully parses `config/default_config.yaml` into an `AppConfig` instance.
- [ ] Dataclass instances are immutable (`frozen=True`) to prevent accidental modification during execution.
- [ ] Schema validation raises explicit errors for invalid parameters or unknown configuration fields.

## Notes

- Centralizing configuration prevents hardcoding constants across strategy and backtest modules.
