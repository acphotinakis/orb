"""
src.services.monitor_state
==========================
Incremental ORB session-state machine for the live market monitor (P5-O3).

Pure functions and frozen dataclasses only.  No I/O.  Reuses Phase 4 shared
decision functions (evaluate_bar_signal) and Phase 1 execution assumptions.
Batch/stream parity: applying the same finalized RawBar sequence through
on_bar() must produce decisions matching the Phase 4 BacktestEngine output
for that session (P5-A4).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pandas as pd

from src.common.time_utils import ensure_eastern
from src.strategy.signals import evaluate_bar_signal

if TYPE_CHECKING:
    from src.data.live_stream import RawBar


@dataclass(frozen=True)
class SessionConfig:
    """Parameters governing one live monitor session.

    Attributes:
        or_minutes: Opening range window in minutes.
        bar_minutes: Bar resolution in minutes.
        direction_mode: One of both, long_only, short_only.
        target_r: Reward-to-risk multiple.
        breakout_buffer_pct: Fractional buffer beyond OR boundary.
        max_trades: Maximum simulated trades per session.
        force_exit_time: HH:MM:SS ET -- hard flatten time.
    """

    or_minutes: int = 15
    bar_minutes: int = 1
    direction_mode: str = "both"
    target_r: float = 2.0
    breakout_buffer_pct: float = 0.0
    max_trades: int = 1
    force_exit_time: str = "15:59:00"


@dataclass(frozen=True)
class MonitorSignal:
    """A detected simulated signal -- NOT a broker order.

    Attributes:
        session_id: Trading date YYYY-MM-DD.
        bar_start: Bar timestamp when breakout was confirmed.
        direction: LONG or SHORT.
        entry_price: Close price at breakout confirmation.
        stop_loss: Opposite OR boundary (1R reference).
        take_profit: Target price at target_r multiples.
        risk_amount: |entry - stop_loss|.
        or_high: Frozen session OR high.
        or_low: Frozen session OR low.
        is_simulated: Always True; never a real broker order.
    """

    session_id: str
    bar_start: pd.Timestamp
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    or_high: float
    or_low: float
    is_simulated: bool = True


@dataclass(frozen=True)
class MonitorState:
    """Complete session snapshot at one point in time.

    Attributes:
        session_id: Trading date YYYY-MM-DD.
        or_high: Observed OR high (accumulates until frozen).
        or_low: Observed OR low (accumulates until frozen).
        or_frozen: True once OR window has elapsed.
        or_valid: True when OR has enough bars and positive width.
        bars_observed: Total bars processed so far.
        trades_this_session: Simulated signals generated.
        open_signal: Current open simulated position, or None.
        closed_signals: Tuple of closed simulated positions.
        realized_pnl_sim: Simulated P&L (not real money).
        last_bar_start: bar_start of last processed bar.
        degraded: True when history is incomplete; no new signals issued.
        degraded_reason: Human-readable reason.
    """

    session_id: str
    or_high: float | None = None
    or_low: float | None = None
    or_frozen: bool = False
    or_valid: bool = False
    bars_observed: int = 0
    trades_this_session: int = 0
    open_signal: MonitorSignal | None = None
    closed_signals: tuple[MonitorSignal, ...] = ()
    realized_pnl_sim: float = 0.0
    last_bar_start: pd.Timestamp | None = None
    degraded: bool = False
    degraded_reason: str | None = None


def new_session(session_id: str) -> MonitorState:
    """Return a blank MonitorState for a new trading session."""
    return MonitorState(session_id=session_id)


def _parse_force_exit_time(force_exit_time: str) -> datetime.time:
    h, m, s = (int(x) for x in force_exit_time.split(":"))
    return datetime.time(h, m, s)


def _minute_of_day(bar_start: pd.Timestamp) -> int:
    """Minutes since 09:30:00 ET (bar at 09:30 -> 0, 09:45 -> 15)."""
    ts_et = ensure_eastern(bar_start)
    return (ts_et.hour * 60 + ts_et.minute) - (9 * 60 + 30)


def on_bar(state: MonitorState, bar: RawBar, cfg: SessionConfig) -> MonitorState:
    """Apply one finalized RawBar to the session state (pure function).

    Processing rules:
    1. If bar date != session_id: skip (return unchanged).
    2. If degraded: increment counters only (no signal generation).
    3. Accumulate OR bars (minute_of_day < or_minutes).
    4. Freeze OR when minute_of_day >= or_minutes; set or_valid.
    5. Trading window (minute_of_day >= or_minutes, time < force_exit):
       call evaluate_bar_signal if trades < max_trades and no open signal.
    6. Force-exit bar: close any open_signal at bar.close.
    7. Return a new MonitorState (never mutates).

    minute_of_day = (bar_start.hour * 60 + bar_start.minute) - (9*60+30)
    """
    bar_date = ensure_eastern(bar.bar_start).date().isoformat()
    if bar_date != state.session_id:
        return state

    if state.degraded:
        return replace(
            state,
            bars_observed=state.bars_observed + 1,
            last_bar_start=bar.bar_start,
        )

    mod = _minute_of_day(bar.bar_start)
    force_exit_t = _parse_force_exit_time(cfg.force_exit_time)
    ts_et = ensure_eastern(bar.bar_start)

    new_or_high = state.or_high
    new_or_low = state.or_low
    new_or_frozen = state.or_frozen
    new_or_valid = state.or_valid

    if not state.or_frozen and mod < cfg.or_minutes:
        new_or_high = bar.high if new_or_high is None else max(new_or_high, bar.high)
        new_or_low = bar.low if new_or_low is None else min(new_or_low, bar.low)

    if not state.or_frozen and mod >= cfg.or_minutes:
        new_or_frozen = True
        new_or_valid = (
            new_or_high is not None
            and new_or_low is not None
            and (new_or_high - new_or_low) > 0
        )

    new_open_signal = state.open_signal
    new_closed_signals = state.closed_signals
    new_trades = state.trades_this_session
    new_pnl = state.realized_pnl_sim

    is_force_exit = ts_et.time() >= force_exit_t

    if is_force_exit and new_open_signal is not None:
        pnl_sim = (
            (bar.close - new_open_signal.entry_price)
            if new_open_signal.direction == "LONG"
            else (new_open_signal.entry_price - bar.close)
        )
        new_pnl += pnl_sim
        new_closed_signals = new_closed_signals + (new_open_signal,)
        new_open_signal = None

    elif (
        not is_force_exit
        and new_or_frozen
        and new_or_valid
        and new_open_signal is None
        and new_trades < cfg.max_trades
        and mod >= cfg.or_minutes
    ):
        or_width = (new_or_high or 0.0) - (new_or_low or 0.0)
        sig = evaluate_bar_signal(
            close_price=bar.close,
            timestamp=bar.bar_start,
            session_id=state.session_id,
            symbol=bar.symbol,
            or_high=new_or_high,
            or_low=new_or_low,
            or_width=or_width,
            direction_mode=cfg.direction_mode,
            target_r=cfg.target_r,
            breakout_buffer_pct=cfg.breakout_buffer_pct,
        )
        if sig is not None:
            new_open_signal = MonitorSignal(
                session_id=state.session_id,
                bar_start=bar.bar_start,
                direction=sig.direction,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                risk_amount=sig.risk_amount,
                or_high=sig.or_high,
                or_low=sig.or_low,
                is_simulated=True,
            )
            new_trades += 1

    return replace(
        state,
        or_high=new_or_high,
        or_low=new_or_low,
        or_frozen=new_or_frozen,
        or_valid=new_or_valid,
        bars_observed=state.bars_observed + 1,
        trades_this_session=new_trades,
        open_signal=new_open_signal,
        closed_signals=new_closed_signals,
        realized_pnl_sim=new_pnl,
        last_bar_start=bar.bar_start,
    )


def mark_degraded(state: MonitorState, reason: str) -> MonitorState:
    """Return a new state with degraded=True and the given reason."""
    return replace(state, degraded=True, degraded_reason=reason)


def end_session(state: MonitorState) -> MonitorState:
    """Close any open simulated position (at last known bar price or 0)."""
    if state.open_signal is None:
        return state
    new_closed = state.closed_signals + (state.open_signal,)
    return replace(state, open_signal=None, closed_signals=new_closed)


def snapshot(state: MonitorState) -> dict:
    """Return a JSON-serializable dictionary of current state (for UI rendering)."""
    return {
        "session_id": state.session_id,
        "or_high": state.or_high,
        "or_low": state.or_low,
        "or_frozen": state.or_frozen,
        "or_valid": state.or_valid,
        "bars_observed": state.bars_observed,
        "trades_this_session": state.trades_this_session,
        "open_signal": (
            {
                "session_id": state.open_signal.session_id,
                "bar_start": state.open_signal.bar_start.isoformat(),
                "direction": state.open_signal.direction,
                "entry_price": state.open_signal.entry_price,
                "stop_loss": state.open_signal.stop_loss,
                "take_profit": state.open_signal.take_profit,
                "risk_amount": state.open_signal.risk_amount,
                "or_high": state.open_signal.or_high,
                "or_low": state.open_signal.or_low,
                "is_simulated": state.open_signal.is_simulated,
            }
            if state.open_signal is not None
            else None
        ),
        "closed_signals": [
            {
                "session_id": s.session_id,
                "bar_start": s.bar_start.isoformat(),
                "direction": s.direction,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "risk_amount": s.risk_amount,
                "or_high": s.or_high,
                "or_low": s.or_low,
                "is_simulated": s.is_simulated,
            }
            for s in state.closed_signals
        ],
        "realized_pnl_sim": state.realized_pnl_sim,
        "last_bar_start": (
            state.last_bar_start.isoformat()
            if state.last_bar_start is not None
            else None
        ),
        "degraded": state.degraded,
        "degraded_reason": state.degraded_reason,
    }
