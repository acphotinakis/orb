"""
tests/services/test_run_models.py
==================================
P1-T03 (precedence/migration) and P1-T04 (rejection) coverage for the shared
RunRequest contract.
"""

import math

import pytest
import yaml

from src.common.exceptions import ConfigurationError
from src.services.run_models import (
    RESEARCH_PRESET_1MIN,
    RunRequest,
    build_run_request,
)

REPO_CONFIG = "config/default_config.yaml"


def _write_yaml(tmp_path, mapping) -> str:
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return str(path)


def _minimal_yaml(**overrides):
    base = {
        "schema_version": "1.0",
        "strategy": {
            "ticker": "SPY",
            "opening_range_minutes": 15,
            "target_r": 2.0,
            "breakout_buffer_pct": 0.0,
            "breakout_confirmation": "close",
            "max_trades_per_day": 1,
            "force_exit_time": "15:59:00",
            "stop_method": "opposite_range",
            "direction_mode": "both",
        },
        "filters": {
            "rvol_filter_enabled": False,
            "rvol_threshold": 1.5,
            "vwap_filter_enabled": False,
            "market_regime_filter_enabled": False,
        },
        "data": {
            "symbol": "SPY",
            "feed": "sip",
            "timeframe": "1Min",
            "timezone": "America/New_York",
            "is_paper": True,
        },
        "execution": {
            "initial_capital": 100000.0,
            "position_sizing": "fixed_risk",
            "risk_per_trade_pct": 0.01,
            "fixed_shares": 100,
            "slippage_per_share": 0.01,
            "commission_per_share": 0.0035,
        },
        "output": {"results_dir": "results/backtest", "plots_dir": "plots"},
    }
    for section, values in overrides.items():
        base[section].update(values)
    return base


# ---------------------------------------------------------------------------
# P1-T03: precedence, migration, presets
# ---------------------------------------------------------------------------


def test_repo_default_config_builds_with_migrated_parameters():
    """Checked-in YAML parameters become effective run defaults (T03)."""
    req = build_run_request(REPO_CONFIG)
    assert isinstance(req, RunRequest)
    # Migrated from parameters: (symbol fills the ticker default too)
    assert req.config.strategy.ticker == "SPY"
    assert req.config.data.symbol == "SPY"
    assert req.start_date == "2025-08-01"
    assert req.end_date == "2025-08-01"
    assert req.refresh_cache is True
    assert req.generate_plots is True
    assert req.log_level == "INFO"


def test_explicit_overrides_beat_yaml_and_parameters(tmp_path):
    mapping = _minimal_yaml()
    mapping["data"]["feed"] = "sip"
    mapping["parameters"] = {"feed": "iex", "start_date": "2024-01-02"}
    path = _write_yaml(tmp_path, mapping)

    # Explicit section value beats migrated parameter default.
    req = build_run_request(path)
    assert req.config.data.feed == "sip"
    assert req.start_date == "2024-01-02"

    # Explicit caller override beats both.
    req2 = build_run_request(path, config_overrides={"data": {"feed": "iex"}})
    assert req2.config.data.feed == "iex"

    req3 = build_run_request(path, options={"start_date": "2024-03-01"})
    assert req3.start_date == "2024-03-01"


def test_parameters_symbol_fills_both_ticker_fields(tmp_path):
    mapping = _minimal_yaml()
    del mapping["strategy"]["ticker"]
    del mapping["data"]["symbol"]
    mapping["parameters"] = {"symbol": "QQQ"}
    path = _write_yaml(tmp_path, mapping)

    req = build_run_request(path)
    assert req.config.strategy.ticker == "QQQ"
    assert req.config.data.symbol == "QQQ"


def test_research_preset_applies_one_minute():
    req = build_run_request(REPO_CONFIG, config_overrides=RESEARCH_PRESET_1MIN)
    assert req.config.data.timeframe == "1Min"


def test_unknown_keys_rejected(tmp_path):
    mapping = _minimal_yaml()
    path = _write_yaml(tmp_path, mapping)

    with pytest.raises(ConfigurationError):
        build_run_request(path, config_overrides={"nope": {"x": 1}})
    with pytest.raises(ConfigurationError):
        build_run_request(path, config_overrides={"strategy": {"targer_r": 2.0}})
    with pytest.raises(ConfigurationError):
        build_run_request(path, options={"bogus_option": 1})

    mapping["parameters"] = {"symobl": "SPY"}
    bad_path = _write_yaml(tmp_path, mapping)
    with pytest.raises(ConfigurationError):
        build_run_request(bad_path)


def test_effective_options_preserved(tmp_path):
    path = _write_yaml(tmp_path, _minimal_yaml())
    req = build_run_request(
        path,
        options={
            "start_date": "2024-01-02",
            "end_date": "2024-01-05",
            "refresh_cache": True,
            "generate_plots": False,
            "run_label": "my research run",
            "log_level": "DEBUG",
        },
    )
    assert (req.start_date, req.end_date) == ("2024-01-02", "2024-01-05")
    assert req.refresh_cache is True
    assert req.generate_plots is False
    assert req.run_label == "my research run"
    assert req.log_level == "DEBUG"


# ---------------------------------------------------------------------------
# P1-T04: rejections (all fail before any side effect)
# ---------------------------------------------------------------------------


def _expect_error(tmp_path, yaml_overrides=None, overrides=None, options=None):
    path = _write_yaml(tmp_path, _minimal_yaml(**(yaml_overrides or {})))
    with pytest.raises(ConfigurationError):
        build_run_request(path, config_overrides=overrides, options=options)


def test_mismatched_and_blank_symbols_rejected(tmp_path):
    _expect_error(tmp_path, overrides={"strategy": {"ticker": "QQQ"}})
    _expect_error(tmp_path, overrides={"strategy": {"ticker": "   "}})
    _expect_error(tmp_path, overrides={"data": {"symbol": "   "}})


def test_impossible_and_reversed_dates_rejected(tmp_path):
    _expect_error(tmp_path, options={"start_date": "2024-02-30"})
    _expect_error(tmp_path, options={"start_date": "not-a-date"})
    _expect_error(
        tmp_path,
        options={"start_date": "2024-02-02", "end_date": "2024-02-01"},
    )


def test_impossible_clock_rejected(tmp_path):
    _expect_error(tmp_path, overrides={"strategy": {"force_exit_time": "24:00:00"}})
    _expect_error(tmp_path, overrides={"strategy": {"force_exit_time": "15:61:00"}})


def test_non_finite_and_mistyped_numbers_rejected(tmp_path):
    _expect_error(tmp_path, overrides={"strategy": {"target_r": math.nan}})
    _expect_error(tmp_path, overrides={"strategy": {"target_r": math.inf}})
    _expect_error(tmp_path, overrides={"strategy": {"opening_range_minutes": True}})
    _expect_error(tmp_path, overrides={"strategy": {"ticker": 123}})


def test_string_booleans_rejected(tmp_path):
    _expect_error(tmp_path, overrides={"data": {"is_paper": "true"}})
    _expect_error(tmp_path, options={"refresh_cache": "yes"})
    _expect_error(tmp_path, options={"generate_plots": 1})


def test_invalid_enum_and_timeframe_rejected(tmp_path):
    _expect_error(tmp_path, overrides={"strategy": {"direction_mode": "sideways"}})
    _expect_error(tmp_path, overrides={"data": {"timeframe": "BogusTF"}})


def test_unsupported_controls_rejected_with_field_errors(tmp_path):
    path = _write_yaml(tmp_path, _minimal_yaml())

    with pytest.raises(ConfigurationError) as exc:
        build_run_request(
            path, config_overrides={"strategy": {"breakout_confirmation": "intrabar"}}
        )
    assert "strategy.breakout_confirmation" in str(exc.value)

    with pytest.raises(ConfigurationError) as exc:
        build_run_request(path, config_overrides={"strategy": {"stop_method": "atr"}})
    assert "strategy.stop_method" in str(exc.value)

    with pytest.raises(ConfigurationError) as exc:
        build_run_request(
            path, config_overrides={"filters": {"rvol_filter_enabled": True}}
        )
    assert "filters.rvol_filter_enabled" in str(exc.value)


def test_unsupported_timeframe_window_combos_rejected(tmp_path):
    # Daily bars cannot host a minute opening range.
    _expect_error(tmp_path, overrides={"data": {"timeframe": "1Day"}})
    # 15-minute range is not a multiple of 30-minute bars.
    _expect_error(
        tmp_path,
        yaml_overrides={"strategy": {"opening_range_minutes": 15}},
        overrides={"data": {"timeframe": "30Min"}},
    )


def test_bad_label_and_log_level_rejected(tmp_path):
    _expect_error(tmp_path, options={"run_label": "   "})
    _expect_error(tmp_path, options={"run_label": "x" * 129})
    _expect_error(tmp_path, options={"log_level": "VERBOSE"})


def test_valid_request_passes(tmp_path):
    path = _write_yaml(tmp_path, _minimal_yaml())
    req = build_run_request(
        path,
        options={"start_date": "2024-01-02", "end_date": "2024-01-03"},
    )
    assert req.config.strategy.ticker == "SPY"
    assert req.log_level == "INFO"
