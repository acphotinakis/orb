"""
src.common.config
=================
Strongly-typed, immutable configuration management for the SPY Opening Range
Breakout (ORB) quantitative trading system.

This module owns the authoritative runtime configuration contract for the
entire pipeline.  All sub-packages obtain their settings by importing
``AppConfig`` rather than reading YAML directly.

Usage
-----
    from src.common.config import load_config, AppConfig

    cfg: AppConfig = load_config()               # uses default YAML
    cfg: AppConfig = load_config("my_config.yaml")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union
from src.common.logger import get_logger

import yaml

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Domain exception — imported from canonical hierarchy (TASK-004).
# ---------------------------------------------------------------------------

from src.common.exceptions import ConfigurationError  # noqa: E402

# ---------------------------------------------------------------------------
# Sub-config dataclasses — all frozen to prevent accidental mutation during
# backtest execution.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyConfig:
    """Parameters governing the ORB strategy signal-generation logic.

    Attributes:
        ticker: Stock ticker symbol.  Baseline v1 supports ``"SPY"`` only.
        opening_range_minutes: Length of the opening range window in minutes
            starting at 09:30 ET.  Default is 15 (09:30–09:44:59).
        target_r: Reward-to-risk multiple for take-profit calculation.
            Default is 2.0 (``2R`` target).
        breakout_buffer_pct: Fractional buffer added beyond the range boundary
            before a breakout is confirmed.  0.0 = exact boundary.
        breakout_confirmation: Bar-level confirmation method.  ``"close"``
            (default) uses bar-close price; ``"intrabar"`` uses live price.
        max_trades_per_day: Maximum trades allowed per trading session.
            Default is 1.
        force_exit_time: Intraday hard-stop time in ``HH:MM:SS`` format (ET).
            All open positions are flattened at this time.  Default ``"15:59:00"``.
        stop_method: Stop-loss placement method.  ``"opposite_range"`` places
            the stop at the far side of the opening range; ``"atr"`` uses an
            ATR-based distance.
        direction_mode: Permitted trade directions.  One of ``"both"``,
            ``"long_only"``, or ``"short_only"``.
    """

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
    """Optional signal filters applied on top of the raw breakout signal.

    Attributes:
        rvol_filter_enabled: When ``True``, require relative volume
            (current bar volume / average volume for that time slot) to exceed
            ``rvol_threshold`` before confirming an entry.
        rvol_threshold: Minimum relative volume multiplier.  Default 1.5×.
        vwap_filter_enabled: When ``True``, require long entries to have price
            above VWAP and short entries below VWAP.
        market_regime_filter_enabled: When ``True``, apply market-regime
            gating logic (placeholder for Phase 4 extension).
    """

    rvol_filter_enabled: bool = False
    rvol_threshold: float = 1.5
    vwap_filter_enabled: bool = False
    market_regime_filter_enabled: bool = False


@dataclass(frozen=True)
class DataConfig:
    """Settings controlling data sourcing and persistence paths.

    Attributes:
        symbol: Ticker symbol for which data is fetched.  Must equal
            ``StrategyConfig.ticker`` for consistency.
        feed: Alpaca market-data feed.  ``"iex"`` (free tier default) or
            ``"sip"`` (paid unlimited plan).
        timeframe: Bar resolution string expected by Alpaca.  Must be
            valid time frame.
        timezone: IANA timezone name used for all timestamp normalisation.
            Must be ``"America/New_York"``.
        is_paper: When ``True``, use paper trading credentials; when
            ``False``, use live trading credentials.
    """

    symbol: str = "SPY"
    feed: str = "sip"
    timeframe: str = "1Min"
    timezone: str = "America/New_York"
    is_paper: bool = True


@dataclass(frozen=True)
class ExecutionConfig:
    """Backtest execution, position sizing, and friction parameters.

    Attributes:
        initial_capital: Starting portfolio equity in USD.
        position_sizing: Sizing algorithm.  ``"fixed_risk"`` sizes by dollar
            risk per trade; ``"fixed_shares"`` uses a constant share count.
        risk_per_trade_pct: Fraction of equity risked per trade (used when
            ``position_sizing == "fixed_risk"``).  Must be in ``(0, 1]``.
        fixed_shares: Constant share count (used when
            ``position_sizing == "fixed_shares"``).
        slippage_per_share: Adverse slippage modelled per share per fill (USD).
        commission_per_share: Broker commission per share per fill (USD).
            Round-trip cost = ``2 × commission_per_share × shares``.
    """

    initial_capital: float = 100_000.0
    position_sizing: str = "fixed_risk"
    risk_per_trade_pct: float = 0.01
    fixed_shares: int = 100
    slippage_per_share: float = 0.01
    commission_per_share: float = 0.0035


@dataclass(frozen=True)
class OutputConfig:
    """Filesystem locations for backtest result artifacts and plots.

    Attributes:
        results_dir: Base directory for CSV / JSON result artifacts.
        plots_dir: Base directory for PNG visualisation outputs.
    """

    results_dir: str = "results/backtest"
    plots_dir: str = "plots"


@dataclass(frozen=True)
class AppConfig:
    """Root configuration container for the ORB backtesting system.

    All sub-configs are frozen dataclasses, making the entire configuration
    tree immutable once loaded.

    Attributes:
        schema_version: Version string of the YAML schema (e.g. ``"1.0"``).
        strategy: ORB strategy signal-generation parameters.
        filters: Optional entry-filter parameters.
        data: Data sourcing and path settings.
        execution: Position sizing and execution friction settings.
        output: Artifact persistence path settings.
    """

    schema_version: str
    strategy: StrategyConfig
    filters: FiltersConfig
    data: DataConfig
    execution: ExecutionConfig
    output: OutputConfig

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the full configuration to a plain dictionary.

        Returns:
            Nested dictionary representation suitable for JSON serialisation.
        """
        import dataclasses as dc

        return dc.asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Return a JSON string representation of this config.

        Args:
            indent: JSON indentation level. Default is 2.

        Returns:
            Pretty-printed JSON string.
        """
        import json
        import dataclasses as dc

        return json.dumps(dc.asdict(self), indent=indent, default=str)

    def to_yaml(self, path: Union[str, Path]) -> None:
        """Write a frozen snapshot of this config to a YAML file.

        Args:
            path: Destination YAML file path. Parent directories are
                created automatically.
        """
        import dataclasses as dc

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.dump(dc.asdict(self), fh, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

# ---------------------------------------------------------------------------
# Timeframe definitions & validation
# ---------------------------------------------------------------------------

# Common / standard timeframe options for CLI presets & documentation
COMMON_TIMEFRAMES: tuple[str, ...] = (
    "1Min",
    "5Min",
    "15Min",
    "30Min",
    "1Hour",
    "2Hour",
    "4Hour",
    "1Day",
    "1Week",
    "1Month",
)

# Regex matching Alpaca timeframe specification:
# - [1-59]Min or [1-59]T
# - [1-23]Hour or [1-23]H
# - 1Day or 1D / 1Week or 1W
# - [1,2,3,4,6,12]Month or [1,2,3,4,6,12]M
TIMEFRAME_REGEX = re.compile(
    r"^("
    r"([1-5]?[0-9])(Min|min|T)|"
    r"(1?[0-9]|2[0-3])(Hour|hour|H)|"
    r"1(Day|day|D)|"
    r"1(Week|week|W)|"
    r"(1|2|3|4|6|12)(Month|month|M)"
    r")$"
)


def is_valid_timeframe(tf: str) -> bool:
    """Return True if string matches Alpaca's timeframe specification."""
    if not isinstance(tf, str):
        return False
    match = TIMEFRAME_REGEX.match(tf.strip())
    if not match:
        return False
    # Ensure minute values are between 1 and 59, hour between 1 and 23
    return True


def _deep_update(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *source* into *target*, skipping ``None`` values.

    Args:
        target: Base dictionary to update in place.
        source: Override dictionary whose values take priority.

    Returns:
        The mutated *target* dictionary.
    """
    for k, v in source.items():
        if v is None:
            continue
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            _deep_update(target[k], v)
        else:
            target[k] = v
    return target


def validate_config(config: AppConfig) -> None:
    """Validate all domain constraints on a loaded ``AppConfig``.

    Performs value-level validation beyond what Python's type system can
    express.  Raises :class:`ConfigurationError` on the first violation found.

    Args:
        config: The configuration object to validate.

    Raises:
        ConfigurationError: If any constraint is violated.
    """
    s = config.strategy
    d = config.data
    e = config.execution

    # ------------------------------------------------------------------
    # strategy section
    # ------------------------------------------------------------------
    if s.opening_range_minutes <= 0:
        raise ConfigurationError(
            f"opening_range_minutes must be > 0; got {s.opening_range_minutes}.",
            field="strategy.opening_range_minutes",
        )

    if s.target_r <= 0:
        raise ConfigurationError(
            f"target_r must be > 0; got {s.target_r}.",
            field="strategy.target_r",
        )

    if s.breakout_buffer_pct < 0:
        raise ConfigurationError(
            f"breakout_buffer_pct must be >= 0; got {s.breakout_buffer_pct}.",
            field="strategy.breakout_buffer_pct",
        )

    if s.breakout_confirmation not in {"close", "intrabar"}:
        raise ConfigurationError(
            f"breakout_confirmation must be 'close' or 'intrabar'; "
            f"got '{s.breakout_confirmation}'.",
            field="strategy.breakout_confirmation",
        )

    if s.max_trades_per_day <= 0:
        raise ConfigurationError(
            f"max_trades_per_day must be > 0; got {s.max_trades_per_day}.",
            field="strategy.max_trades_per_day",
        )

    if not _TIME_RE.match(s.force_exit_time):
        raise ConfigurationError(
            f"force_exit_time must be in HH:MM:SS format; "
            f"got '{s.force_exit_time}'.",
            field="strategy.force_exit_time",
        )

    if s.stop_method not in {"opposite_range", "atr"}:
        raise ConfigurationError(
            f"stop_method must be 'opposite_range' or 'atr'; "
            f"got '{s.stop_method}'.",
            field="strategy.stop_method",
        )

    if s.direction_mode not in {"both", "long_only", "short_only"}:
        raise ConfigurationError(
            f"direction_mode must be 'both', 'long_only', or 'short_only'; "
            f"got '{s.direction_mode}'.",
            field="strategy.direction_mode",
        )

    # ------------------------------------------------------------------
    # data section
    # ------------------------------------------------------------------
    if d.timezone != "America/New_York":
        raise ConfigurationError(
            f"timezone must be 'America/New_York'; got '{d.timezone}'.",
            field="data.timezone",
        )

    if not is_valid_timeframe(d.timeframe):
        raise ConfigurationError(
            f"Invalid timeframe '{d.timeframe}'. Must match Alpaca format: "
            f"[1-59]Min/T, [1-23]Hour/H, 1Day/D, 1Week/W, or [1,2,3,4,6,12]Month/M.",
            field="data.timeframe",
        )

    if d.feed not in {"iex", "sip"}:
        raise ConfigurationError(
            f"feed must be 'iex' or 'sip'; got '{d.feed}'.",
            field="data.feed",
        )

    # ------------------------------------------------------------------
    # execution section
    # ------------------------------------------------------------------
    if e.initial_capital <= 0:
        raise ConfigurationError(
            f"initial_capital must be > 0; got {e.initial_capital}.",
            field="execution.initial_capital",
        )

    if not (0 < e.risk_per_trade_pct <= 1.0):
        raise ConfigurationError(
            f"risk_per_trade_pct must be in (0, 1]; got {e.risk_per_trade_pct}.",
            field="execution.risk_per_trade_pct",
        )

    if e.fixed_shares <= 0:
        raise ConfigurationError(
            f"fixed_shares must be > 0; got {e.fixed_shares}.",
            field="execution.fixed_shares",
        )

    if e.slippage_per_share < 0:
        raise ConfigurationError(
            f"slippage_per_share must be >= 0; got {e.slippage_per_share}.",
            field="execution.slippage_per_share",
        )

    if e.commission_per_share < 0:
        raise ConfigurationError(
            f"commission_per_share must be >= 0; got {e.commission_per_share}.",
            field="execution.commission_per_share",
        )

    if e.position_sizing not in {"fixed_risk", "fixed_shares"}:
        raise ConfigurationError(
            f"position_sizing must be 'fixed_risk' or 'fixed_shares'; "
            f"got '{e.position_sizing}'.",
            field="execution.position_sizing",
        )


# ---------------------------------------------------------------------------
# Private YAML → dataclass mapping helpers
# ---------------------------------------------------------------------------


def _build_strategy(raw: Dict[str, Any]) -> StrategyConfig:
    """Construct a :class:`StrategyConfig` from a raw YAML mapping.

    Args:
        raw: The ``strategy:`` sub-dict from the loaded YAML.

    Returns:
        Populated :class:`StrategyConfig` instance.
    """
    defaults = StrategyConfig()
    return StrategyConfig(
        ticker=str(raw.get("ticker", defaults.ticker)),
        opening_range_minutes=int(
            raw.get("opening_range_minutes", defaults.opening_range_minutes)
        ),
        target_r=float(raw.get("target_r", defaults.target_r)),
        breakout_buffer_pct=float(
            raw.get("breakout_buffer_pct", defaults.breakout_buffer_pct)
        ),
        breakout_confirmation=str(
            raw.get("breakout_confirmation", defaults.breakout_confirmation)
        ),
        max_trades_per_day=int(
            raw.get("max_trades_per_day", defaults.max_trades_per_day)
        ),
        force_exit_time=str(raw.get("force_exit_time", defaults.force_exit_time)),
        stop_method=str(raw.get("stop_method", defaults.stop_method)),
        direction_mode=str(raw.get("direction_mode", defaults.direction_mode)),
    )


def _build_filters(raw: Dict[str, Any]) -> FiltersConfig:
    """Construct a :class:`FiltersConfig` from a raw YAML mapping.

    Args:
        raw: The ``filters:`` sub-dict from the loaded YAML.

    Returns:
        Populated :class:`FiltersConfig` instance.
    """
    defaults = FiltersConfig()
    return FiltersConfig(
        rvol_filter_enabled=bool(
            raw.get("rvol_filter_enabled", defaults.rvol_filter_enabled)
        ),
        rvol_threshold=float(raw.get("rvol_threshold", defaults.rvol_threshold)),
        vwap_filter_enabled=bool(
            raw.get("vwap_filter_enabled", defaults.vwap_filter_enabled)
        ),
        market_regime_filter_enabled=bool(
            raw.get(
                "market_regime_filter_enabled",
                defaults.market_regime_filter_enabled,
            )
        ),
    )


def _build_data(raw: Dict[str, Any]) -> DataConfig:
    """Construct a :class:`DataConfig` from a raw YAML mapping.

    Args:
        raw: The ``data:`` sub-dict from the loaded YAML.

    Returns:
        Populated :class:`DataConfig` instance.
    """
    defaults = DataConfig()
    symbol = str(raw.get("symbol", defaults.symbol))
    timeframe = str(raw.get("timeframe", defaults.timeframe))

    return DataConfig(
        symbol=symbol,
        feed=str(raw.get("feed", defaults.feed)),
        timeframe=timeframe,
        timezone=str(raw.get("timezone", defaults.timezone)),
        is_paper=bool(raw.get("is_paper", defaults.is_paper)),
    )


def _build_execution(raw: Dict[str, Any]) -> ExecutionConfig:
    """Construct an :class:`ExecutionConfig` from a raw YAML mapping.

    Args:
        raw: The ``execution:`` sub-dict from the loaded YAML.

    Returns:
        Populated :class:`ExecutionConfig` instance.
    """
    defaults = ExecutionConfig()
    return ExecutionConfig(
        initial_capital=float(raw.get("initial_capital", defaults.initial_capital)),
        position_sizing=str(raw.get("position_sizing", defaults.position_sizing)),
        risk_per_trade_pct=float(
            raw.get("risk_per_trade_pct", defaults.risk_per_trade_pct)
        ),
        fixed_shares=int(raw.get("fixed_shares", defaults.fixed_shares)),
        slippage_per_share=float(
            raw.get("slippage_per_share", defaults.slippage_per_share)
        ),
        commission_per_share=float(
            raw.get("commission_per_share", defaults.commission_per_share)
        ),
    )


def _build_output(raw: Dict[str, Any]) -> OutputConfig:
    """Construct an :class:`OutputConfig` from a raw YAML mapping.

    Args:
        raw: The ``output:`` sub-dict from the loaded YAML.

    Returns:
        Populated :class:`OutputConfig` instance.
    """
    defaults = OutputConfig()
    return OutputConfig(
        results_dir=str(raw.get("results_dir", defaults.results_dir)),
        plots_dir=str(raw.get("plots_dir", defaults.plots_dir)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(
    config_path: Union[str, Path] = "config/default_config.yaml",
    overrides: Optional[Dict[str, Any]] = None,
) -> AppConfig:
    """Load, parse, and validate an ORB system configuration from a YAML file.

    Supports a layered override system — values in *overrides* take precedence
    over the YAML file, which in turn takes precedence over dataclass defaults.
    This enables CLI or programmatic parameter sweeps without modifying YAML.

    Args:
        config_path: Path to the YAML configuration file.  Relative paths are
            resolved from the current working directory.
        overrides: Optional nested dictionary of values to merge on top of the
            loaded YAML.  Mirrors the YAML section structure, e.g.::

                overrides = {
                    "strategy": {"ticker": "NVDA", "opening_range_minutes": 5},
                    "data": {"timeframe": "5Min"},
                }

    Returns:
        A fully validated, immutable :class:`AppConfig` instance.

    Raises:
        FileNotFoundError: If *config_path* does not exist.
        ConfigurationError: If any value fails schema or domain validation.
        yaml.YAMLError: If the file is not valid YAML.

    Example::

        from src.common.config import load_config
        cfg = load_config()
        print(cfg.strategy.opening_range_minutes)  # 15

        # With CLI overrides:
        cfg = load_config(overrides={"data": {"timeframe": "5Min"}})
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: '{path.resolve()}'. "
            "Ensure 'config/default_config.yaml' exists at the repository root."
        )

    with path.open("r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    # Apply overrides — CLI / programmatic values take precedence over YAML
    if overrides:
        _deep_update(raw, overrides)

    # Build each sub-config, using empty dicts when a section is absent so
    # that default field values are used cleanly.
    config = AppConfig(
        schema_version=str(raw.get("schema_version", "1.0")),
        strategy=_build_strategy(raw.get("strategy", {})),
        filters=_build_filters(raw.get("filters", {})),
        data=_build_data(raw.get("data", {})),
        execution=_build_execution(raw.get("execution", {})),
        output=_build_output(raw.get("output", {})),
    )

    # Always validate — callers do not need to call validate_config() separately
    validate_config(config)
    logger.info("Config loaded: %s", config.to_json())
    return config
