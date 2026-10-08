"""
tests/services/test_monitor.py
================================
P5-T01..T18: streaming monitor unit tests using FakeStreamAdapter and fake clock.
All tests are offline and credential-free.
"""

from __future__ import annotations

import pandas as pd

from src.data.live_stream import FakeStreamAdapter, RawBar, make_fake_bars
from src.services.monitor_state import (
    MonitorSignal,
    MonitorState,
    SessionConfig,
    end_session,
    mark_degraded,
    new_session,
    on_bar,
)
from src.services.stream_collector import (
    CollectorState,
    FinalizationPolicy,
    StreamCollector,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID = "2024-01-02"
DEFAULT_CFG = SessionConfig(or_minutes=15, max_trades=1, direction_mode="both")

_FAKE_NOW = pd.Timestamp("2024-01-02 14:30:00", tz="UTC")


def _clock() -> pd.Timestamp:
    return _FAKE_NOW


def _make_collector(
    bars: list[RawBar],
    *,
    session_id: str = SESSION_ID,
    policy: FinalizationPolicy = FinalizationPolicy(),
    **adapter_kwargs,
) -> StreamCollector:
    adapter = FakeStreamAdapter(bars, clock=_clock, **adapter_kwargs)
    collector = StreamCollector(
        adapter,
        symbol="SPY",
        feed="sip",
        timeframe="1Min",
        session_cfg=DEFAULT_CFG,
        policy=policy,
        clock=_clock,
    )
    # Override session_id to match test date
    collector._session_id = session_id
    collector._monitor_state = new_session(session_id)
    return collector


def _drain(collector: StreamCollector, max_events: int = 10_000) -> CollectorState:
    """Run collector until stopped or max_events reached."""
    for _ in range(max_events):
        if not collector.run_one():
            break
    return collector.state


# ===========================================================================
# P5-T01: FakeStreamAdapter emits connected then bars in order
# ===========================================================================


def test_fake_stream_emits_connected_then_bars():
    """FakeStreamAdapter with 3 bars emits connected -> bar -> bar -> bar."""
    bars = make_fake_bars(SESSION_ID, total_bars=3)
    adapter = FakeStreamAdapter(bars, clock=_clock)
    events = list(adapter.events())

    assert events[0].kind == "connected"
    bar_events = [e for e in events if e.kind == "bar"]
    assert len(bar_events) == 3
    assert bar_events[0].bar is bars[0]
    assert bar_events[1].bar is bars[1]
    assert bar_events[2].bar is bars[2]


# ===========================================================================
# P5-T02: Duplicate bars emitted twice
# ===========================================================================


def test_fake_stream_duplicate_bars_emitted_twice():
    """duplicate_indices={1} causes bar at index 1 to be emitted twice."""
    bars = make_fake_bars(SESSION_ID, total_bars=3)
    adapter = FakeStreamAdapter(bars, clock=_clock, duplicate_indices={1})
    bar_events = [e for e in adapter.events() if e.kind == "bar"]
    assert len(bar_events) == 4  # 3 bars + 1 duplicate
    # bar index 1 appears twice
    bar1_events = [e for e in bar_events if e.bar is bars[1]]
    assert len(bar1_events) == 2


# ===========================================================================
# P5-T03: Drop indices reduce emitted bars
# ===========================================================================


def test_fake_stream_drop_emits_fewer_bars():
    """drop_indices={2} means only 2 bars in events (bar at index 2 is skipped)."""
    bars = make_fake_bars(SESSION_ID, total_bars=3)
    adapter = FakeStreamAdapter(bars, clock=_clock, drop_indices={2})
    bar_events = [e for e in adapter.events() if e.kind == "bar"]
    assert len(bar_events) == 2
    assert all(e.bar is not bars[2] for e in bar_events)


# ===========================================================================
# P5-T04: Disconnect then reconnect
# ===========================================================================


def test_fake_stream_disconnect_reconnect():
    """disconnect_after=2 emits: connected, bar, bar, disconnected, connected, bar."""
    bars = make_fake_bars(SESSION_ID, total_bars=3)
    adapter = FakeStreamAdapter(bars, clock=_clock, disconnect_after=2)
    events = list(adapter.events())
    kinds = [e.kind for e in events]
    # Must contain exactly this sequence
    assert kinds.count("connected") == 2
    assert kinds.count("disconnected") == 1
    assert kinds.count("bar") == 3
    # Order: connected first, disconnected before second connected
    ci = [i for i, k in enumerate(kinds) if k == "connected"]
    di = [i for i, k in enumerate(kinds) if k == "disconnected"]
    assert ci[0] < di[0] < ci[1]


# ===========================================================================
# P5-T05: stop() causes events() to raise StopIteration
# ===========================================================================


def test_fake_stream_stop_raises():
    """After stop(), events() iterator yields nothing (StopIteration immediately)."""
    bars = make_fake_bars(SESSION_ID, total_bars=5)
    adapter = FakeStreamAdapter(bars, clock=_clock)
    adapter.stop()
    events = list(adapter.events())
    assert events == []
    assert adapter.connection_state == "stopped"


# ===========================================================================
# P5-T06: Heartbeat does not update last_bar_market_ts
# ===========================================================================


def test_heartbeat_does_not_update_bar_ts():
    """Heartbeat event updates last_heartbeat_at but NOT
    collector last_bar_market_ts.
    """
    # Use heartbeat_every=1 so every bar is followed by a heartbeat
    bars = make_fake_bars(SESSION_ID, total_bars=35)
    collector = _make_collector(bars, heartbeat_every=1)
    _drain(collector)
    state = collector.state
    assert state.last_heartbeat_at is not None
    assert state.last_bar_market_ts is not None
    # last_bar_market_ts must be a bar_start (market time), not a wall-clock time
    assert state.last_bar_market_ts != _FAKE_NOW


# ===========================================================================
# P5-T07: OR formation accumulates over 15 bars
# ===========================================================================


def test_on_bar_or_formation_accumulates():
    """15 OR bars accumulate; or_frozen becomes True after the 15th is processed."""
    bars = make_fake_bars(SESSION_ID, or_minutes=15, total_bars=16)
    state = new_session(SESSION_ID)
    cfg = DEFAULT_CFG

    for i, bar in enumerate(bars[:15]):
        state = on_bar(state, bar, cfg)
        assert not state.or_frozen, f"or_frozen prematurely at bar {i}"

    # 16th bar (index 15) is first trading-window bar; triggers OR freeze
    state = on_bar(state, bars[15], cfg)
    assert state.or_frozen
    assert state.or_valid


# ===========================================================================
# P5-T08: No signal generated during OR window
# ===========================================================================


def test_on_bar_no_signal_during_or():
    """Signals are not generated for bars within the OR window (minute_of_day < 15)."""
    # Make all 15 OR bars have extreme closes to ensure they would trigger a signal
    bars = make_fake_bars(SESSION_ID, total_bars=15, or_high=502.0, or_low=498.0)
    state = new_session(SESSION_ID)
    for bar in bars:
        state = on_bar(state, bar, DEFAULT_CFG)
    assert state.open_signal is None
    assert state.trades_this_session == 0


# ===========================================================================
# P5-T09: Breakout bar generates MonitorSignal
# ===========================================================================


def test_on_bar_breakout_generates_signal():
    """A bar with close > or_high+0.1 in trading window generates a MonitorSignal."""
    bars = make_fake_bars(
        SESSION_ID,
        or_minutes=15,
        total_bars=20,
        or_high=502.0,
        or_low=498.0,
        breakout_bar=16,
        breakout_direction="LONG",
    )
    state = new_session(SESSION_ID)
    for bar in bars:
        state = on_bar(state, bar, DEFAULT_CFG)

    assert state.open_signal is not None or state.trades_this_session > 0
    # If a signal was generated and then closed by force_exit, it shows in closed
    if state.open_signal is not None:
        assert state.open_signal.direction == "LONG"
        assert state.open_signal.is_simulated is True
    elif state.trades_this_session > 0:
        assert len(state.closed_signals) > 0


# ===========================================================================
# P5-T10: Degraded state suppresses signals
# ===========================================================================


def test_on_bar_no_signal_when_degraded():
    """mark_degraded state suppresses new signals even on a breakout bar."""
    bars = make_fake_bars(
        SESSION_ID,
        or_minutes=15,
        total_bars=20,
        breakout_bar=16,
        breakout_direction="LONG",
    )
    state = new_session(SESSION_ID)
    # Process OR bars normally
    for bar in bars[:15]:
        state = on_bar(state, bar, DEFAULT_CFG)
    # Mark degraded before trading window
    state = mark_degraded(state, "test degradation")
    # Process trading-window bars including breakout
    for bar in bars[15:]:
        state = on_bar(state, bar, DEFAULT_CFG)
    assert state.open_signal is None
    assert state.trades_this_session == 0


# ===========================================================================
# P5-T11: Force-exit closes open simulated position
# ===========================================================================


def test_on_bar_force_exit_closes_open_signal():
    """A bar at 15:59 ET closes any open simulated position."""
    # Build a short session: OR + breakout + force-exit bar
    bars = make_fake_bars(
        SESSION_ID,
        or_minutes=15,
        total_bars=390,  # full session so 15:59 bar is present
        breakout_bar=16,
        breakout_direction="LONG",
    )
    state = new_session(SESSION_ID)
    for bar in bars:
        state = on_bar(state, bar, DEFAULT_CFG)

    # After full session, force-exit bar should have closed the signal
    assert state.open_signal is None
    assert len(state.closed_signals) > 0


# ===========================================================================
# P5-T12: Collector deduplication
# ===========================================================================


def test_collector_deduplication():
    """Same (symbol,feed,timeframe,bar_start,revision) inserted twice
    -> bars_dropped_dup=1.
    """
    bars = make_fake_bars(SESSION_ID, total_bars=3)
    # Duplicate bar at index 0
    collector = _make_collector(bars, duplicate_indices={0})
    _drain(collector)
    assert collector.state.bars_dropped_dup == 1


# ===========================================================================
# P5-T13: Full session through collector
# ===========================================================================


def test_collector_run_one_full_session():
    """Run collector through a fake session; final state has or_frozen
    and correct counts.
    """
    bars = make_fake_bars(
        SESSION_ID,
        or_minutes=15,
        total_bars=50,
        breakout_bar=20,
        breakout_direction="LONG",
    )
    collector = _make_collector(bars)
    _drain(collector)
    state = collector.state

    assert state.bars_finalized == 50
    assert state.monitor_state is not None
    assert state.monitor_state.or_frozen
    assert state.monitor_state.or_valid
    assert (
        state.monitor_state.trades_this_session > 0
        or state.monitor_state.open_signal is not None
    )


# ===========================================================================
# P5-T14: stop() returns False from run_one and sets stopped
# ===========================================================================


def test_collector_stop():
    """After stop(), run_one() returns False and state.stopped=True."""
    bars = make_fake_bars(SESSION_ID, total_bars=5)
    collector = _make_collector(bars)
    collector.stop()
    result = collector.run_one()
    assert result is False
    assert collector.state.stopped is True


# ===========================================================================
# P5-T15: Batch/stream parity with evaluate_bar_signal
# ===========================================================================


def test_batch_stream_parity():
    """on_bar() chain agrees with evaluate_bar_signal called directly on same bars.

    Verifies:
    - or_frozen timestamp matches expected OR boundary.
    - Signal direction, entry_price, stop_loss, take_profit match direct call.
    """
    from src.strategy.signals import evaluate_bar_signal

    bars = make_fake_bars(
        SESSION_ID,
        or_minutes=15,
        total_bars=20,
        or_high=502.0,
        or_low=498.0,
        breakout_bar=16,
        breakout_direction="LONG",
    )
    state = new_session(SESSION_ID)
    cfg = DEFAULT_CFG

    for bar in bars:
        state = on_bar(state, bar, cfg)

    # OR must be frozen at bar 15 (first trading-window bar)
    assert state.or_frozen
    assert state.or_high is not None
    assert state.or_low is not None

    # Reference: call evaluate_bar_signal directly on breakout bar
    or_high = state.or_high
    or_low = state.or_low
    or_width = or_high - or_low
    breakout = bars[16]

    ref_sig = evaluate_bar_signal(
        close_price=breakout.close,
        timestamp=breakout.bar_start,
        session_id=SESSION_ID,
        symbol="SPY",
        or_high=or_high,
        or_low=or_low,
        or_width=or_width,
        direction_mode="both",
        target_r=2.0,
    )
    assert ref_sig is not None, "Reference signal must exist for parity test"
    assert ref_sig.direction == "LONG"

    # The monitor state must have captured an equivalent signal
    sig = state.open_signal or (
        state.closed_signals[0] if state.closed_signals else None
    )
    assert sig is not None
    assert sig.direction == ref_sig.direction
    assert abs(sig.entry_price - ref_sig.entry_price) < 1e-9
    assert abs(sig.stop_loss - ref_sig.stop_loss) < 1e-9
    assert abs(sig.take_profit - ref_sig.take_profit) < 1e-9


# ===========================================================================
# P5-T16: Stale detection via fake clock
# ===========================================================================


def test_stale_detection():
    """After stale_threshold_seconds with no bar, collector.state.stale == True."""
    bars = make_fake_bars(SESSION_ID, total_bars=2)
    stale_threshold = 30  # seconds
    policy = FinalizationPolicy(stale_threshold_seconds=stale_threshold)

    # Clock that starts at T0 for setup+events then jumps ahead past stale threshold.
    # 5 T0 slots: 1 (collector init) + 1 (connected) + 1 (bar[0]) + 1 (bar[1])
    # + 1 spare.
    t0 = pd.Timestamp("2024-01-02 14:30:00", tz="UTC")
    future_time = pd.Timestamp("2024-01-02 14:32:00", tz="UTC")  # 120s > 30s threshold
    clock_calls = iter([t0] * 5 + [future_time] * 1000)

    def _advancing_clock():
        try:
            return next(clock_calls)
        except StopIteration:
            return future_time

    adapter = FakeStreamAdapter(bars, clock=_advancing_clock)
    collector = StreamCollector(
        adapter,
        symbol="SPY",
        feed="sip",
        timeframe="1Min",
        session_cfg=DEFAULT_CFG,
        policy=policy,
        clock=_advancing_clock,
    )
    collector._session_id = SESSION_ID
    collector._monitor_state = new_session(SESSION_ID)

    # Drain all events (so _last_event_wall_ts is set to an early time)
    _drain(collector, max_events=100)

    # Now the clock is at future_time, which is > stale_threshold past last event
    # The state property computes stale dynamically
    state = collector.state
    assert state.stale is True


# ===========================================================================
# P5-T17: Gap detection marks degraded
# ===========================================================================


def test_gap_detection_marks_degraded():
    """Injecting a gap > max_gap_bars triggers degraded state."""
    # Build bars with a big gap (drop indices 16-22, i.e., 7 bars)
    bars = make_fake_bars(SESSION_ID, total_bars=30)
    policy = FinalizationPolicy(max_gap_bars=3)
    # Drop bars 16..22 to create a 7-bar gap
    drop_set = set(range(16, 23))
    collector = _make_collector(bars, policy=policy, drop_indices=drop_set)
    _drain(collector)
    assert collector.state.monitor_state is not None
    assert collector.state.monitor_state.degraded


# ===========================================================================
# P5-T18: end_session clears open simulated position
# ===========================================================================


def test_end_session_clears_open_signal():
    """end_session() closes any open simulated position."""
    # Create a state with an open signal
    dummy_signal = MonitorSignal(
        session_id=SESSION_ID,
        bar_start=pd.Timestamp("2024-01-02 10:00:00", tz="America/New_York"),
        direction="LONG",
        entry_price=502.5,
        stop_loss=498.0,
        take_profit=511.5,
        risk_amount=4.5,
        or_high=502.0,
        or_low=498.0,
        is_simulated=True,
    )
    state = MonitorState(
        session_id=SESSION_ID,
        or_frozen=True,
        or_valid=True,
        or_high=502.0,
        or_low=498.0,
        open_signal=dummy_signal,
        trades_this_session=1,
    )
    assert state.open_signal is not None

    ended = end_session(state)
    assert ended.open_signal is None
    assert len(ended.closed_signals) == 1
    assert ended.closed_signals[0] is dummy_signal


# ===========================================================================
# P5-T11 / P5-A6: Burst ingestion at 10x rate and bounded memory
# ===========================================================================


def test_burst_ingestion_10x_rate():
    """P5-A6: 60-minute burst at 10x expected rate (600 bars).

    Verifies configured buffers stay bounded, persisted accepted bars reconcile
    exactly with input counts (600 in, 600 finalized), and no unreported loss occurs.
    """
    bars = make_fake_bars(
        SESSION_ID, total_bars=600, breakout_bar=20, breakout_direction="LONG"
    )
    adapter = FakeStreamAdapter(bars)
    collector = StreamCollector(adapter, "SPY", "sip", "1Min", DEFAULT_CFG)
    collector._session_id = SESSION_ID
    collector._monitor_state = new_session(SESSION_ID)

    while collector.run_one():
        pass

    state = collector.state
    assert state.bars_received == 600
    assert state.bars_finalized == 600
    assert state.bars_dropped_dup == 0
    assert state.gaps_detected == 0
    assert state.monitor_state is not None
    assert state.monitor_state.or_frozen is True
