import dataclasses

import pytest

from src.common.config import (
    AppConfig,
    ConfigurationError,
    load_config,
    validate_config,
)


def test_load_default_config():
    cfg = load_config("config/default_config.yaml")
    assert isinstance(cfg, AppConfig)
    assert cfg.strategy.ticker == "SPY"
    assert cfg.strategy.opening_range_minutes == 15
    assert (
        cfg.strategy.target_r == 1.25
    )  # checked-in default (config/default_config.yaml); dataclass fallback is 2.0
    assert cfg.execution.initial_capital == 100_000.0


def test_config_validation_rules(mock_app_config):
    # Invalid opening range <= 0
    bad_strat = dataclasses.replace(mock_app_config.strategy, opening_range_minutes=-5)
    bad_cfg = dataclasses.replace(mock_app_config, strategy=bad_strat)
    with pytest.raises(ConfigurationError):
        validate_config(bad_cfg)
