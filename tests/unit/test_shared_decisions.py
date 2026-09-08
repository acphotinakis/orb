"""
tests/unit/test_shared_decisions.py
====================================
P4-T01: the shared single-bar decision function preserves accepted engine
behavior, and the engine and SignalGenerator agree session by session.
"""

import pandas as pd

from src.backtest.engine import BacktestEngine
from src.strategy.opening_range import OpeningRangeCalculator
from src.strategy.signals import SignalGenerator, evaluate_bar_signal

from tests.unit.test_accounting_reconciliation import (
    _eod_fixture_config,
    _session_frame,
    _set_or,
)


def _sessions(df):
    return {sid: group for sid, group in df.groupby("session_id")}


def test_engine_and_generator_agree_per_session():
    """LONG, SHORT, and no-breakout sessions produce identical first signals."""
    cfg = _eod_fixture_config()
    frames = []
    long_day = _set_or(_session_frame(date_str="2024-01-02"))
    long_day.loc[15, ["high", "close"]] = [501.6, 501.5]
    frames.append(long_day)
    short_day = _set_or(_session_frame(date_str="2024-01-03"))
    short_day.loc[15, ["high", "low", "close"]] = [499.0, 498.0, 498.5]
    frames.append(short_day)
    flat_day = _set_or(_session_frame(date_str="2024-01-04"))
    frames.append(flat_day)
    df = pd.concat(frames, ignore_index=True)

    engine = BacktestEngine(config=cfg)
    result = engine.run(df)
    by_session = {}
    for trade in result.trades:
        by_session.setdefault(trade.date, trade)

    calculator = OpeningRangeCalculator(
        or_minutes=cfg.strategy.opening_range_minutes,
        timeframe=cfg.data.timeframe,
    )
    generator = SignalGenerator(config=cfg.strategy)
    for session_id, session_bars in _sessions(df).items():
        or_obj = calculator.calculate_session_or(session_bars)
        signal = generator.evaluate_session_signals(session_bars, or_obj)
        trade = by_session.get(session_id)
        if trade is None:
            assert signal is None, session_id
            continue
        assert signal is not None, session_id
        assert signal.direction == trade.direction
        # Signal price is pre-slippage; the trade fill adds adverse slippage.
        slip = cfg.execution.slippage_per_share
        if trade.direction == "LONG":
            assert trade.entry_price == signal.entry_price + slip
        else:
            assert trade.entry_price == signal.entry_price - slip
        assert signal.stop_loss == trade.stop_price
        assert signal.take_profit == trade.target_price
        assert pd.Timestamp(signal.timestamp) == pd.Timestamp(trade.entry_time)


def test_explicit_buffer_path_preserved():
    """A constructed buffer still overrides the config-derived one."""
    kwargs = dict(
        close_price=501.05,
        timestamp=pd.Timestamp("2024-01-02 09:45:00", tz="America/New_York"),
        session_id="2024-01-02",
        symbol="SPY",
        or_high=501.0,
        or_low=499.0,
        or_width=2.0,
        direction_mode="both",
        target_r=2.0,
        breakout_buffer_pct=0.0,
    )
    assert evaluate_bar_signal(**kwargs) is not None  # buffer 0 → breakout
    assert evaluate_bar_signal(**kwargs, buffer=0.5) is None  # 501.05 < 501.5


def test_boundary_and_geometry_unchanged():
    """Exact-edge closes and degenerate risk still yield no signal."""
    base = dict(
        timestamp=pd.Timestamp("2024-01-02 09:45:00", tz="America/New_York"),
        session_id="2024-01-02",
        symbol="SPY",
        or_high=501.0,
        or_low=499.0,
        or_width=2.0,
        direction_mode="both",
        target_r=2.0,
        breakout_buffer_pct=0.0,
    )
    assert evaluate_bar_signal(close_price=501.0, **base) is None
    assert evaluate_bar_signal(close_price=499.0, **base) is None
    # Degenerate inverted range: breakout shape but non-positive risk.
    inverted = dict(base, or_low=502.0)
    assert evaluate_bar_signal(close_price=501.5, **inverted) is None


def test_pre_extraction_engine_golden_parity():
    """Golden outputs captured from HEAD engine before rule extraction."""
    import json
    from pathlib import Path
    from src.backtest.trace import TraceCollector
    frames=[]
    for i,case in enumerate(['flat','long_target','short_target','dual_touch','fallback']):
        frame=_set_or(_session_frame(date_str=f'2024-01-0{i+2}'))
        if case != 'flat':
            frame.loc[15,['close','high']]=[501.5,501.6]
        if case=='long_target': frame.loc[16,'high']=507
        if case=='short_target':
            frame.loc[15,['close','low']]=[498.5,498]
            frame.loc[16,'low']=493
        if case=='dual_touch': frame.loc[16,['high','low']]=[507,498]
        if case=='fallback':
            frame.loc[16:,['close','high','low']]=[501.5,502,501]
        frames.append(frame)
    expected = json.loads((Path(__file__).parents[1] / "fixtures/phase03_engine_parity.json").read_text())
    for trace in (None, TraceCollector()):
        result = BacktestEngine(_eod_fixture_config()).run(pd.concat(frames, ignore_index=True), trace=trace)
        for key, frame in (("trades", result.trades_df), ("equity", result.equity_curve), ("daily", result.daily_summary)):
            assert json.loads(frame.to_json(orient="split", date_format="iso")) == expected[key]
        assert result.final_capital == expected["final_capital"]
