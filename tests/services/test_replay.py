"""
tests/services/test_replay.py
==============================
P4-T02..T08/T12: causality, determinism, incomplete traces, checkpoints —
all against traces captured from actual engine execution.
"""

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.trace import (
    TRACE_SCHEMA_VERSION,
    TraceCollector,
)
from src.common.exceptions import ConfigurationError
from src.services import replay
from src.services.artifact_store import read_trace
from tests.unit.test_accounting_reconciliation import (
    _eod_fixture_config,
    _session_frame,
    _set_or,
)


def _two_session_frame():
    day1 = _set_or(_session_frame(date_str="2024-01-02"))
    day1.loc[15, ["high", "close"]] = [501.6, 501.5]
    day1.loc[25, ["high", "close"]] = [507.0, 506.6]  # TARGET
    day2 = _set_or(_session_frame(date_str="2024-01-03"))
    day2.loc[15, ["high", "close"]] = [501.6, 501.5]
    day2.loc[16:, "close"] = 501.5  # held to EOD
    day2.loc[16:, "high"] = 502.0
    day2.loc[16:, "low"] = 501.0
    return pd.concat([day1, day2], ignore_index=True)


@pytest.fixture()
def traced():
    cfg = _eod_fixture_config()
    df = _two_session_frame()
    collector = TraceCollector()
    result = BacktestEngine(config=cfg).run(df, trace=collector)
    header = collector.header(run_id="fixture")
    return cfg, df, result, header, collector.events


def _reduce(traced, session_id, cursor, **kwargs):
    cfg, df, _result, _header, events = traced
    session_events = replay.filter_session_events(events, session_id)
    bars = df.loc[df["session_id"] == session_id].reset_index(drop=True)
    return replay.reduce_events(
        session_events,
        cursor,
        session_bars=bars,
        session_id=session_id,
        or_minutes=15,
        bar_minutes=1,
        **kwargs,
    )


def _full(traced, session_id):
    cfg, df, _result, _header, events = traced
    session_events = replay.filter_session_events(events, session_id)
    bars = df.loc[df["session_id"] == session_id].reset_index(drop=True)
    return replay.reduce_events(
        session_events,
        len(session_events),
        session_bars=bars,
        session_id=session_id,
        or_minutes=15,
        bar_minutes=1,
    )


def _state_key(state):
    return (
        state.cursor,
        str(state.cursor_time),
        len(state.visible_bars),
        state.range_observed_high,
        state.range_observed_low,
        state.range_frozen,
        state.range_frozen_high,
        state.range_frozen_low,
        str(state.open_position),
        tuple(sorted(str(t) for t in state.closed_trades)),
        round(state.realized_pnl, 6),
    )


# ---------------------------------------------------------------------------
# T08 first: complete replay must equal the finished run
# ---------------------------------------------------------------------------


def test_complete_replay_matches_run_artifacts(traced):
    cfg, df, result, _header, _events = traced
    for session_id in ("2024-01-02", "2024-01-03"):
        state = _full(traced, session_id)
        assert state.open_position is None
    day1 = _full(traced, "2024-01-02")
    assert len(day1.closed_trades) == 1
    assert day1.closed_trades[0]["exit_reason"] == "TARGET"
    day2 = _full(traced, "2024-01-03")
    assert day2.closed_trades[0]["exit_reason"] == "EOD"
    total = day1.realized_pnl + day2.realized_pnl
    assert total == pytest.approx(sum(t.pnl_dollars for t in result.trades))
    assert len(result.trades) == 2


# ---------------------------------------------------------------------------
# T02: future perturbation cannot leak into cursor state
# ---------------------------------------------------------------------------


def test_future_perturbation_leaves_cursor_state_unchanged(traced):
    cfg, df, _result, _header, events = traced
    session_id = "2024-01-02"
    session_events = replay.filter_session_events(events, session_id)
    cursor = len(session_events) // 2
    before = _reduce(traced, session_id, cursor)

    perturbed = df.copy()
    cursor_time = before.cursor_time
    mask = (perturbed["session_id"] == session_id) & (
        pd.to_datetime(perturbed["timestamp"]) > cursor_time
    )
    perturbed.loc[mask, "high"] = 9999.0
    perturbed.loc[mask, "low"] = 0.01
    perturbed.loc[mask, "close"] = 9999.0
    bars = perturbed.loc[perturbed["session_id"] == session_id].reset_index(drop=True)
    after = replay.reduce_events(
        session_events,
        cursor,
        session_bars=bars,
        session_id=session_id,
        or_minutes=15,
        bar_minutes=1,
    )
    assert _state_key(after) == _state_key(before)
    # Perturbing the OR window itself is also contained: the frozen range at a
    # pre-freeze cursor still reflects only observed bars.
    assert after.range_frozen_high is None or cursor_time >= pd.Timestamp(
        "2024-01-02 09:45:00", tz="America/New_York"
    )


def test_future_or_extreme_hidden_during_formation(traced):
    cfg, df, _result, _header, events = traced
    session_events = replay.filter_session_events(events, "2024-01-02")
    # Cursor inside the OR window: only observed bars, never the final range.
    early_events = [
        e for e in session_events if (e.get("available_at") or "") < "2024-01-02 09:40"
    ]
    assert early_events, "fixture must contain pre-freeze decisions"
    early = replay.reduce_events(
        session_events,
        len(early_events),
        session_bars=df.loc[df["session_id"] == "2024-01-02"].reset_index(drop=True),
        session_id="2024-01-02",
        or_minutes=15,
        bar_minutes=1,
    )
    assert early.range_frozen is False
    assert early.range_frozen_high is None
    assert early.open_position is None
    assert early.realized_pnl == 0.0


# ---------------------------------------------------------------------------
# T03/T04: cursor positions, step/seek equivalence, backward rebuild
# ---------------------------------------------------------------------------


def test_cursor_positions_cover_lifecycle(traced):
    cfg, df, _result, _header, events = traced
    session_events = replay.filter_session_events(events, "2024-01-02")

    zero = _reduce(traced, "2024-01-02", 0)
    assert len(zero.visible_bars) == 0
    assert zero.open_position is None and zero.realized_pnl == 0.0

    # First entry: an open LONG with no realized P&L yet.
    opened_idx = next(
        i for i, e in enumerate(session_events) if e["event_type"] == "trade_opened"
    )
    opened = _reduce(traced, "2024-01-02", opened_idx + 1)
    assert opened.open_position is not None
    assert opened.open_position["direction"] == "LONG"
    assert opened.realized_pnl == 0.0

    full = _full(traced, "2024-01-02")
    assert full.open_position is None
    assert len(full.closed_trades) == 1


def test_step_seek_and_backward_rebuild_agree(traced):
    cfg, df, _result, _header, events = traced
    session_events = replay.filter_session_events(events, "2024-01-02")
    total = len(session_events)

    stepped = None
    for cursor in range(total + 1):
        stepped = _reduce(traced, "2024-01-02", cursor)
    jumped = _reduce(traced, "2024-01-02", total)
    assert _state_key(stepped) == _state_key(jumped)

    back = _reduce(traced, "2024-01-02", 0)
    fresh = _reduce(traced, "2024-01-02", 0)
    assert _state_key(back) == _state_key(fresh)
    mid = _reduce(traced, "2024-01-02", total // 3)
    assert mid.realized_pnl == 0.0  # entry not yet seen: no accumulated P&L


def test_reducer_is_pure_no_wall_clock(traced):
    first = _reduce(traced, "2024-01-02", 7)
    second = _reduce(traced, "2024-01-02", 7)
    assert _state_key(first) == _state_key(second)


# ---------------------------------------------------------------------------
# T07: truncated / unknown / missing traces
# ---------------------------------------------------------------------------


def test_truncated_trace_replays_available_prefix(traced):
    cfg, df, _result, _header, events = traced
    tiny = TraceCollector(max_events=5)
    BacktestEngine(config=cfg).run(df, trace=tiny)
    assert tiny.truncated is True
    header = tiny.header(run_id="x")
    assert header["truncated"] is True
    payload = tiny.to_jsonl(run_id="x")
    parsed_header, parsed_events = TraceCollector.parse_jsonl(payload)
    assert len(parsed_events) == 5
    state = replay.reduce_events(
        parsed_events,
        5,
        session_bars=df.loc[df["session_id"] == "2024-01-02"].reset_index(drop=True),
        session_id="2024-01-02",
        or_minutes=15,
        bar_minutes=1,
    )
    assert state.cursor == 5


def test_unknown_schema_version_fails_explicitly(traced):
    cfg, df, _result, header, events = traced
    collector = TraceCollector()
    BacktestEngine(config=cfg).run(df, trace=collector)
    payload = collector.to_jsonl(run_id="x").replace(
        f'"trace_version": {TRACE_SCHEMA_VERSION}', '"trace_version": 999', 1
    )
    with pytest.raises(ValueError, match="Unsupported trace_version"):
        TraceCollector.parse_jsonl(payload)


def test_missing_trace_is_explicit(tmp_path):
    with pytest.raises(ConfigurationError):
        read_trace(tmp_path, "nope")


# ---------------------------------------------------------------------------
# T12: checkpoint seeks equal sequential reduce and stay bounded
# ---------------------------------------------------------------------------


def test_checkpoints_match_and_seek_fast():
    import random
    import time as _time

    base = pd.Timestamp("2024-01-02 09:30:00", tz="America/New_York")
    events = []
    for i in range(100_000):
        available = base + pd.Timedelta(minutes=i)
        if i % 1000 == 500:
            events.append(
                {
                    "seq": i,
                    "event_type": "trade_opened",
                    "trade_id": i,
                    "available_at": str(available),
                }
            )
        elif i % 1000 == 700:
            events.append(
                {
                    "seq": i,
                    "event_type": "trade_closed",
                    "trade_id": i - 200,
                    "pnl_dollars": 10.0,
                    "available_at": str(available),
                }
            )
        else:
            events.append(
                {
                    "seq": i,
                    "event_type": "signal_check",
                    "outcome": "rejected",
                    "available_at": str(available),
                }
            )
    bars = pd.DataFrame(
        {
            "timestamp": [base + pd.Timedelta(minutes=i) for i in range(100_000)],
            "minute_of_day": list(range(100_000)),
            "high": 500.0,
            "low": 499.0,
        }
    )
    checkpoints = replay.build_checkpoints(events)
    assert checkpoints[0].cursor == 0

    rng = random.Random(42)
    for cursor in [rng.randrange(100_001) for _ in range(10)]:
        full = replay.reduce_events(
            events,
            cursor,
            session_bars=bars,
            session_id="2024-01-02",
            or_minutes=15,
            bar_minutes=1,
        )
        resume = replay.reduce_events(
            events,
            cursor,
            session_bars=bars,
            session_id="2024-01-02",
            or_minutes=15,
            bar_minutes=1,
            start_from=replay.nearest_checkpoint(checkpoints, cursor),
        )
        assert _state_key(full) == _state_key(resume)

    probe = [rng.randrange(100_001) for _ in range(5)]
    start = _time.time()
    for cursor in probe:
        replay.reduce_events(
            events,
            cursor,
            session_bars=bars,
            session_id="2024-01-02",
            or_minutes=15,
            bar_minutes=1,
            start_from=replay.nearest_checkpoint(checkpoints, cursor),
        )
    elapsed = (_time.time() - start) / len(probe)
    print(f"\ncheckpoint seek mean: {elapsed*1000:.0f}ms")
    assert elapsed < 0.5


def test_replay_index_seek_budget_100k():
    """P4-A7: ReplayIndex.seek() worst-case < 500 ms on a 100,000-event bar-heavy trace.

    Uses a realistic trace composition (≈98% bar_observed events) so that both
    the columnar bar index and the scalar checkpoint replay path are exercised.
    20 random forward/backward seeks are timed individually; every single seek
    must stay under the 500 ms budget, not just the mean.  The index build is a
    one-time cost paid at session load, not included in the per-seek budget.
    """
    import random
    import time as _time

    base = pd.Timestamp("2024-01-02 09:30:00", tz="America/New_York")
    events = []
    open_trade_id = None
    for i in range(100_000):
        available = base + pd.Timedelta(minutes=i)
        # Every 100th event: OR freeze marker (realistic OR boundary events)
        if i % 100 == 50:
            events.append(
                {
                    "seq": i,
                    "session_id": "2024-01-02",
                    "event_type": "or_frozen",
                    "bar_start": str(available),
                    "available_at": str(available),
                    "valid": True,
                    "or_high": 502.0,
                    "or_low": 498.0,
                    "or_width": 4.0,
                }
            )
        # Sparse trade open/close pairs (every 300 bars) with equity marks
        elif i % 300 == 200:
            open_trade_id = i
            events.append(
                {
                    "seq": i,
                    "session_id": "2024-01-02",
                    "event_type": "trade_opened",
                    "trade_id": i,
                    "rule_id": "orb_breakout_close",
                    "direction": "LONG",
                    "entry_price": 502.1,
                    "shares": 100,
                    "stop_loss": 498.0,
                    "take_profit": 510.0,
                    "bar_start": str(available),
                    "available_at": str(available),
                }
            )
        elif i % 300 == 250 and open_trade_id is not None:
            events.append(
                {
                    "seq": i,
                    "session_id": "2024-01-02",
                    "event_type": "trade_closed",
                    "trade_id": open_trade_id,
                    "pnl_dollars": 80.0,
                    "exit_reason": "TARGET",
                    "rule_id": "exit_bracket",
                    "bar_start": str(available),
                    "available_at": str(available),
                }
            )
            open_trade_id = None
        elif i % 300 == 275:
            events.append(
                {
                    "seq": i,
                    "session_id": "2024-01-02",
                    "event_type": "equity_mark",
                    "bar_start": str(available),
                    "available_at": str(available),
                    "cash": 100_000.0,
                    "position_value": 0.0,
                    "equity": 100_000.0,
                }
            )
        # Default: bar_observed (the dominant event type — ≈98% of events)
        else:
            events.append(
                {
                    "seq": i,
                    "session_id": "2024-01-02",
                    "event_type": "bar_observed",
                    "bar_start": str(available),
                    "available_at": str(available),
                    "open": 500.0,
                    "high": 502.0,
                    "low": 498.0,
                    "close": 501.0,
                    "volume": 10_000.0,
                    "minute_of_day": i % 390,
                    "is_opening_range": i < 15,
                }
            )

    bar_count = sum(1 for e in events if e["event_type"] == "bar_observed")
    assert len(events) == 100_000
    assert (
        bar_count / len(events) > 0.95
    ), "Fixture must be bar-heavy (>95% bar_observed)"

    # One-time index build (paid at session load, not per-seek)
    index = replay.ReplayIndex(events, every=1000)

    # 20 random seeks covering forward and backward jumps across the full range
    rng = random.Random(99)
    cursors = [rng.randrange(len(events) + 1) for _ in range(20)]

    worst_ms = 0.0
    for cursor in cursors:
        t0 = _time.perf_counter()
        state = index.seek(cursor, max_bars=2000)
        elapsed_ms = (_time.perf_counter() - t0) * 1_000
        worst_ms = max(worst_ms, elapsed_ms)
        # Sanity: cursor is clamped correctly
        assert state.cursor == cursor

    print(
        f"\nP4-A7 ReplayIndex seek: worst={worst_ms:.1f} ms over {len(cursors)} seeks "
        f"(100,000-event bar-heavy trace, budget=500 ms)"
    )
    assert (
        worst_ms < 500.0
    ), f"P4-A7 FAIL: worst seek {worst_ms:.1f} ms exceeds 500 ms budget"


def test_actual_formation_trace_cannot_leak_future_extremes(traced):

    cfg, df, _, _, events = traced
    session = "2024-01-02"
    original = replay.filter_session_events(events, session)
    cursor = next(
        i + 1
        for i, e in enumerate(original)
        if e.get("bar_start") == str(df.iloc[4]["timestamp"])
    )
    altered = df.copy()
    altered.loc[5:14, "high"] = 9000.0
    other = TraceCollector()
    BacktestEngine(cfg).run(altered, trace=other)
    changed = replay.filter_session_events(other.events, session)
    kwargs = dict(
        session_bars=altered, session_id=session, or_minutes=15, bar_minutes=1
    )
    before = replay.reduce_events(original, cursor, **kwargs)
    after = replay.reduce_events(changed, cursor, **kwargs)
    pd.testing.assert_frame_equal(before.visible_bars, after.visible_bars)
    assert len(before.visible_bars) == 5
    assert _state_key(before) == _state_key(after)
    assert before.range_observed_high < 9000
    assert before.range_frozen_high is None


def test_freeze_is_applied_by_sequence_not_timestamp(traced):
    events = replay.filter_session_events(traced[-1], "2024-01-02")
    freeze = next(i for i, e in enumerate(events) if e["event_type"] == "or_frozen")
    before = _reduce(traced, "2024-01-02", freeze)
    after = _reduce(traced, "2024-01-02", freeze + 1)
    assert before.cursor_time == after.cursor_time
    assert not before.range_frozen
    assert after.range_frozen_high == 501


@pytest.mark.parametrize("mutation", ["version", "sequence", "time"])
def test_malformed_event_rejected(traced, mutation):
    import json

    collector = TraceCollector()
    BacktestEngine(traced[0]).run(traced[1], trace=collector)
    lines = collector.to_jsonl().splitlines()
    event = json.loads(lines[2])
    event[
        {"version": "trace_version", "sequence": "seq", "time": "available_at"}[
            mutation
        ]
    ] = {"version": 999, "sequence": 400, "time": "invalid"}[mutation]
    lines[2] = json.dumps(event)
    with pytest.raises(ValueError):
        TraceCollector.parse_jsonl("\n".join(lines))
