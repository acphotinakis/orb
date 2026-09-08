"""
src.services.run_models
========================
Shared run-submission contract for the ORB system (P1-O2).

A :class:`RunRequest` bundles the immutable strategy/data/execution
configuration (:class:`~src.common.config.AppConfig`) with the run options
(date bounds, refresh, plot generation, user label, log level).  The CLI, the
future dashboard, and the worker all build requests through
:func:`build_run_request`, so validation lives in exactly one place.

Precedence (highest wins)
-------------------------
1. Explicit caller options/overrides (CLI flags, UI form values).
2. Explicit YAML section values (e.g. ``data.feed``).
3. Legacy top-level YAML ``parameters:`` values (migrated as defaults).
4. Dataclass defaults in :mod:`src.common.config`.

Migration of ``parameters:``
----------------------------
Older config files carry a top-level ``parameters:`` mapping that
:func:`~src.common.config.load_config` ignores.  The builder folds known keys
in as *defaults* (never overriding explicit section values):

=================  =====================================================
``parameters`` key Migrated to
=================  =====================================================
``symbol``         ``strategy.ticker`` + ``data.symbol`` (defaults only)
``feed``           ``data.feed`` (default only)
``is_paper``       ``data.is_paper`` (default only)
``start_date``     run option ``start_date`` (default only)
``end_date``       run option ``end_date`` (default only)
``refresh_cache``  run option ``refresh_cache`` (default only)
``generate_plots`` run option ``generate_plots`` (default only)
``log_level``      run option ``log_level`` (default only)
=================  =====================================================

Unknown keys under ``parameters:``, unknown override paths, and unknown
option keys are rejected with :class:`ConfigurationError` so typos cannot
silently do nothing.

Transitional notes
------------------
- ``run_label`` is the user-facing tag and is intentionally separate from any
  filesystem identity (run IDs/UUIDs arrive with P1-O3 manifests).  The CLI
  currently forwards ``--run-id`` as the label until that split lands.
- One-minute bars are the dashboard's initial execution preset
  (:data:`RESEARCH_PRESET_1MIN`); the checked-in YAML default is unchanged.
- ``breakout_confirmation="intrabar"``, ``stop_method="atr"``, and any enabled
  signal filter are rejected until implemented (P1-O2).
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

import yaml

from src.common.config import (
    AppConfig,
    DataConfig,
    ExecutionConfig,
    FiltersConfig,
    OutputConfig,
    StrategyConfig,
    load_config,
)
from src.common.exceptions import ConfigurationError

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

#: Dashboard's initial execution preset: one-minute research bars (P1-O2).
#: Applied as ordinary overrides (explicit caller values still win).
RESEARCH_PRESET_1MIN: Dict[str, Any] = {"data": {"timeframe": "1Min"}}

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

#: Run-option fields accepted in ``options`` (anything else is a typo).
_OPTION_FIELDS = (
    "start_date",
    "end_date",
    "refresh_cache",
    "record_trace",
    "generate_plots",
    "run_label",
    "log_level",
)

#: Legacy ``parameters:`` keys and where they migrate to.  ``None`` target
#: means a run option of the same name.
_PARAMETER_TARGETS: Mapping[str, Optional[str]] = {
    "symbol": None,  # special-cased: strategy.ticker + data.symbol
    "feed": "data.feed",
    "is_paper": "data.is_paper",
    "start_date": None,
    "end_date": None,
    "refresh_cache": None,
    "generate_plots": None,
    "log_level": None,
}

_CONFIG_SECTIONS: Mapping[str, Any] = {
    "strategy": StrategyConfig,
    "filters": FiltersConfig,
    "data": DataConfig,
    "execution": ExecutionConfig,
    "output": OutputConfig,
}

# Field-name -> kind, derived from the dataclasses so the allowlist cannot
# drift from the implementation.
_FIELD_KINDS: Dict[str, Dict[str, str]] = {}
for _section, _cls in _CONFIG_SECTIONS.items():
    _kinds: Dict[str, str] = {}
    for _f in dataclasses.fields(_cls):
        _t = _f.type
        if _t == "bool":
            _kinds[_f.name] = "bool"
        elif _t in ("int", "float"):
            _kinds[_f.name] = "number"
        else:
            _kinds[_f.name] = "str"
    _FIELD_KINDS[_section] = _kinds

_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_TF_MIN_RE = re.compile(r"^([1-5]?\d)(Min|min|T)$")
_TF_HOUR_RE = re.compile(r"^(1?\d|2[0-3])(Hour|hour|H)$")


# ---------------------------------------------------------------------------
# Public model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunRequest:
    """Validated, immutable run submission (P1-O2).

    Attributes:
        config: Effective, validated :class:`AppConfig`.
        start_date: Backtest start (``"YYYY-MM-DD"``) or ``None`` for earliest
            cached data.
        end_date: Backtest end (``"YYYY-MM-DD"``) or ``None`` for latest.
        refresh_cache: Bypass raw cache and re-fetch when ``True``.
        generate_plots: Produce chart artifacts when ``True``.
        run_label: User-facing tag; never a filesystem identity.
        log_level: One of ``DEBUG``/``INFO``/``WARNING``/``ERROR``.
    """

    config: AppConfig
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    record_trace: bool = False
    refresh_cache: bool = False
    generate_plots: bool = True
    run_label: Optional[str] = None
    log_level: str = "INFO"


# ---------------------------------------------------------------------------
# Override / option key validation
# ---------------------------------------------------------------------------


def _check_override_value(path: str, kind: str, value: Any) -> None:
    """Type-check a single config override value (domain rules run later)."""
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigurationError(
                f"Override '{path}' must be a real boolean; got {value!r}.",
                field=f"overrides.{path}",
            )
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigurationError(
                f"Override '{path}' must be a number; got {value!r}.",
                field=f"overrides.{path}",
            )
        if not math.isfinite(value):
            raise ConfigurationError(
                f"Override '{path}' must be finite; got {value!r}.",
                field=f"overrides.{path}",
            )
    else:
        if not isinstance(value, str):
            raise ConfigurationError(
                f"Override '{path}' must be a string; got {value!r}.",
                field=f"overrides.{path}",
            )


def validate_override_keys(overrides: Mapping[str, Any]) -> None:
    """Reject unknown override paths and mistyped values before load."""
    for section, values in overrides.items():
        kinds = _FIELD_KINDS.get(section)
        if kinds is None:
            raise ConfigurationError(
                f"Unknown override section '{section}'. "
                f"Expected one of {sorted(_FIELD_KINDS)}.",
                field=f"overrides.{section}",
            )
        if not isinstance(values, Mapping):
            raise ConfigurationError(
                f"Override section '{section}' must be a mapping.",
                field=f"overrides.{section}",
            )
        for name, value in values.items():
            kind = kinds.get(name)
            if kind is None:
                raise ConfigurationError(
                    f"Unknown override field '{section}.{name}'. "
                    "Typos must not silently do nothing.",
                    field=f"overrides.{section}.{name}",
                )
            _check_override_value(f"{section}.{name}", kind, value)


def validate_option_keys(options: Mapping[str, Any]) -> None:
    """Reject unknown run-option keys."""
    for name in options:
        if name not in _OPTION_FIELDS:
            raise ConfigurationError(
                f"Unknown run option '{name}'. "
                f"Expected one of {sorted(_OPTION_FIELDS)}.",
                field=f"options.{name}",
            )


# ---------------------------------------------------------------------------
# YAML parameters migration
# ---------------------------------------------------------------------------


def _nested_get(mapping: Mapping[str, Any], dotted: str) -> Any:
    node: Any = mapping
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _nested_set(mapping: Dict[str, Any], dotted: str, value: Any) -> None:
    node = mapping
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def migrate_parameters(
    raw: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Split legacy ``parameters:`` into config-default overrides + options.

    Explicit YAML section values always win over migrated defaults.  Unknown
    ``parameters`` keys raise :class:`ConfigurationError`.

    Returns:
        ``(config_defaults, option_defaults)`` to be applied underneath
        explicit caller values.
    """
    params = raw.get("parameters", {}) or {}
    if not isinstance(params, Mapping):
        raise ConfigurationError(
            "Top-level 'parameters' must be a mapping.",
            field="parameters",
        )
    config_defaults: Dict[str, Any] = {}
    option_defaults: Dict[str, Any] = {}
    for name, value in params.items():
        if name not in _PARAMETER_TARGETS:
            raise ConfigurationError(
                f"Unknown 'parameters' key '{name}'. "
                f"Expected one of {sorted(_PARAMETER_TARGETS)}.",
                field=f"parameters.{name}",
            )
        target = _PARAMETER_TARGETS[name]
        if name == "symbol":
            # Default for both ticker fields; explicit sections win below.
            if _nested_get(raw, "strategy.ticker") is None:
                _nested_set(config_defaults, "strategy.ticker", value)
            if _nested_get(raw, "data.symbol") is None:
                _nested_set(config_defaults, "data.symbol", value)
        elif target is not None:
            if _nested_get(raw, target) is None:
                _nested_set(config_defaults, target, value)
        else:
            option_defaults[name] = value
    return config_defaults, option_defaults


def _deep_merge(base: Dict[str, Any], winner: Mapping[str, Any]) -> Dict[str, Any]:
    """Recursively merge *winner* over *base* (new dict)."""
    merged = copy.deepcopy(base)
    for key, value in winner.items():
        if (
            isinstance(value, Mapping)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


# ---------------------------------------------------------------------------
# Request-level validation
# ---------------------------------------------------------------------------


def _require_real_clock(value: str) -> None:
    """Reject HH:MM:SS strings that are not real clock times (e.g. 24:00:00)."""
    if not _TIME_RE.match(value):
        raise ConfigurationError(
            f"force_exit_time must be in HH:MM:SS format; got '{value}'.",
            field="strategy.force_exit_time",
        )
    try:
        _dt.datetime.strptime(value, "%H:%M:%S")
    except ValueError:
        raise ConfigurationError(
            f"force_exit_time must be a real clock time; got '{value}'.",
            field="strategy.force_exit_time",
        )


def _timeframe_minutes(timeframe: str) -> int:
    """Return bar length in minutes, rejecting non-intraday resolutions."""
    minute = _TF_MIN_RE.match(timeframe.strip())
    if minute:
        return int(minute.group(1))
    hour = _TF_HOUR_RE.match(timeframe.strip())
    if hour:
        return int(hour.group(1)) * 60
    raise ConfigurationError(
        f"timeframe '{timeframe}' is not supported for dashboard runs; "
        "use an intraday minute/hour resolution.",
        field="data.timeframe",
    )


def _require_valid_date(value: str, field_name: str) -> _dt.date:
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ConfigurationError(
            f"{field_name} must be a real calendar date in YYYY-MM-DD format; "
            f"got {value!r}.",
            field=f"options.{field_name}",
        )


def validate_request_values(request: RunRequest) -> None:
    """Enforce cross-field request contracts beyond :mod:`config` basics."""
    cfg = request.config

    # Symbol consistency (incl. blank/whitespace rejection).
    ticker = cfg.strategy.ticker
    symbol = cfg.data.symbol
    if not ticker or not ticker.strip():
        raise ConfigurationError(
            "strategy.ticker must be a non-blank symbol.",
            field="strategy.ticker",
        )
    if not symbol or not symbol.strip():
        raise ConfigurationError(
            "data.symbol must be a non-blank symbol.",
            field="data.symbol",
        )
    if ticker != symbol:
        raise ConfigurationError(
            f"strategy.ticker ({ticker!r}) must match data.symbol ({symbol!r}).",
            field="strategy.ticker",
        )

    # Real clock values (format alone permits 24:00:00).
    _require_real_clock(cfg.strategy.force_exit_time)

    # Unsupported execution paths fail before any side effect.
    if cfg.strategy.breakout_confirmation == "intrabar":
        raise ConfigurationError(
            "breakout_confirmation='intrabar' is not implemented; use 'close'.",
            field="strategy.breakout_confirmation",
        )
    if cfg.strategy.stop_method == "atr":
        raise ConfigurationError(
            "stop_method='atr' is not implemented; use 'opposite_range'.",
            field="strategy.stop_method",
        )
    for name in (
        "rvol_filter_enabled",
        "vwap_filter_enabled",
        "market_regime_filter_enabled",
    ):
        if getattr(cfg.filters, name):
            raise ConfigurationError(
                f"filters.{name} is not implemented; it must stay disabled.",
                field=f"filters.{name}",
            )

    # Supported timeframe/window combinations.
    bar_minutes = _timeframe_minutes(cfg.data.timeframe)
    or_minutes = cfg.strategy.opening_range_minutes
    if or_minutes < bar_minutes or or_minutes % bar_minutes != 0:
        raise ConfigurationError(
            f"opening_range_minutes ({or_minutes}) must be a positive multiple "
            f"of the {bar_minutes}-minute bar resolution.",
            field="strategy.opening_range_minutes",
        )

    # Ordered, real calendar dates.
    start = (
        _require_valid_date(request.start_date, "start_date")
        if request.start_date is not None
        else None
    )
    end = (
        _require_valid_date(request.end_date, "end_date")
        if request.end_date is not None
        else None
    )
    if start is not None and end is not None and start > end:
        raise ConfigurationError(
            f"start_date ({request.start_date}) must not be after "
            f"end_date ({request.end_date}).",
            field="options.start_date",
        )

    # Real booleans and known log level.
    for name in ("refresh_cache", "generate_plots", "record_trace"):
        if not isinstance(getattr(request, name), bool):
            raise ConfigurationError(
                f"{name} must be a real boolean.",
                field=f"options.{name}",
            )
    if request.log_level not in _LOG_LEVELS:
        raise ConfigurationError(
            f"log_level must be one of {list(_LOG_LEVELS)}; "
            f"got {request.log_level!r}.",
            field="options.log_level",
        )

    # User label: separate from filesystem identity, but must be sane.
    if request.run_label is not None:
        if not isinstance(request.run_label, str) or not request.run_label.strip():
            raise ConfigurationError(
                "run_label must be a non-blank string when provided.",
                field="options.run_label",
            )
        if len(request.run_label) > 128:
            raise ConfigurationError(
                "run_label must be at most 128 characters.",
                field="options.run_label",
            )


# ---------------------------------------------------------------------------
# Builder (single entry point for CLI / UI / worker)
# ---------------------------------------------------------------------------


def build_run_request(
    config_path: Union[str, Path] = "config/default_config.yaml",
    config_overrides: Optional[Mapping[str, Any]] = None,
    options: Optional[Mapping[str, Any]] = None,
) -> RunRequest:
    """Load, migrate, merge, and validate one effective run request.

    Args:
        config_path: YAML configuration file.
        config_overrides: Nested config overrides (highest precedence).
        options: Run options (``start_date``, ``end_date``,
            ``refresh_cache``, ``generate_plots``, ``run_label``,
            ``log_level``; explicit values beat migrated ``parameters``).

    Returns:
        A validated, immutable :class:`RunRequest`.

    Raises:
        FileNotFoundError: Missing config file.
        ConfigurationError: Unknown keys, migration conflicts, or any
            violated request contract.
    """
    config_overrides = dict(config_overrides or {})
    # None means "not provided" (e.g. unset CLI flags); it must not clobber
    # migrated YAML defaults.
    options = {k: v for k, v in dict(options or {}).items() if v is not None}
    validate_override_keys(config_overrides)
    validate_option_keys(options)

    path = Path(config_path)
    with path.open("r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    param_config_defaults, param_option_defaults = migrate_parameters(raw)
    merged_overrides = _deep_merge(param_config_defaults, config_overrides)
    merged_options = {**param_option_defaults, **options}

    config = load_config(config_path, overrides=merged_overrides or None)

    # Option values arriving via YAML are untyped: enforce strict types here,
    # before any coercion, so string "true"/1 cannot silently become True.
    for name in ("refresh_cache", "generate_plots", "record_trace"):
        if name in merged_options and not isinstance(merged_options[name], bool):
            raise ConfigurationError(
                f"{name} must be a real boolean; got {merged_options[name]!r}.",
                field=f"options.{name}",
            )
    if "log_level" in merged_options and not isinstance(
        merged_options["log_level"], str
    ):
        raise ConfigurationError(
            f"log_level must be a string; got {merged_options['log_level']!r}.",
            field="options.log_level",
        )
    if "run_label" in merged_options and not isinstance(
        merged_options["run_label"], str
    ):
        raise ConfigurationError(
            "run_label must be a string when provided.",
            field="options.run_label",
        )

    request = RunRequest(
        config=config,
        start_date=merged_options.get("start_date"),
        end_date=merged_options.get("end_date"),
        record_trace=bool(merged_options.get("record_trace", False)),
        refresh_cache=bool(merged_options.get("refresh_cache", False)),
        generate_plots=bool(merged_options.get("generate_plots", True)),
        run_label=merged_options.get("run_label"),
        log_level=str(merged_options.get("log_level", "INFO")),
    )
    validate_request_values(request)
    return request


# ---------------------------------------------------------------------------
# Worker-side reconstruction
# ---------------------------------------------------------------------------


def app_config_from_dict(data: Mapping[str, Any]) -> AppConfig:
    """Rebuild an :class:`AppConfig` from :meth:`AppConfig.to_dict` output.

    Used by the worker process, which receives its configuration through the
    durable registry instead of YAML files.
    """
    return AppConfig(
        schema_version=str(data.get("schema_version", "1.0")),
        strategy=StrategyConfig(**data.get("strategy", {})),
        filters=FiltersConfig(**data.get("filters", {})),
        data=DataConfig(**data.get("data", {})),
        execution=ExecutionConfig(**data.get("execution", {})),
        output=OutputConfig(**data.get("output", {})),
    )
