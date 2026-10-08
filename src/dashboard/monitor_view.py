"""
src.dashboard.monitor_view
==========================
Market Monitor tab — real-time ORB signal tracking (P5-O4).

Displays forming/frozen opening range, simulated position status, signal
history, feed freshness indicators, and connection controls using a
StreamCollector backed by an injectable StreamAdapter.

Design constraints (P5-O4):
- Display pause is independent of ingestion: closing the browser does not
  stop the collector.
- Stop must actually release the subscription and persist stopped state.
- Historical run results and streaming state are visually distinct.
- No broker order calls; no credential exposure in UI payloads.
- Heartbeats do NOT reset the freshness indicator (P5-A5).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.monitor_state import SessionConfig, snapshot
from src.services.stream_collector import (
    CollectorState,
    FinalizationPolicy,
    StreamCollector,
)

# Session-state keys (prefixed to avoid collision with other views)
_KEY_COLLECTOR = "_monitor_collector"
_KEY_THREAD = "_monitor_thread"
_KEY_PAUSED = "_monitor_paused"

_DEFAULT_SESSION_CFG = SessionConfig(
    or_minutes=15,
    bar_minutes=1,
    direction_mode="both",
    target_r=2.0,
    breakout_buffer_pct=0.0,
    max_trades=1,
    force_exit_time="15:59:00",
)
_DEFAULT_POLICY = FinalizationPolicy(
    reorder_window_seconds=10,
    max_gap_bars=5,
    stale_threshold_seconds=90,
    backfill_overlap_bars=2,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_monitor(
    symbol: str = "SPY", feed: str = "sip", timeframe: str = "1Min"
) -> None:
    """Render the Market Monitor tab.

    Does NOT instantiate a real provider adapter — the UI only controls and
    displays state from a pre-existing StreamCollector injected via
    ``st.session_state``.  Call :func:`attach_collector` before rendering to
    wire up a real or fake collector.
    """
    st.subheader("Market Monitor")
    st.caption(
        "Simulated signals only — no broker orders are submitted. "
        "Closing this tab does not stop the data collector."
    )

    collector: StreamCollector | None = st.session_state.get(_KEY_COLLECTOR)

    if collector is None:
        st.info(
            "No market data collector is attached. "
            "Use `attach_collector()` to wire a StreamCollector before rendering, "
            "or run the demo with a FakeStreamAdapter."
        )
        _render_demo_controls(symbol, feed, timeframe)
        return

    state: CollectorState = collector.state

    # ── Connection / freshness status ──────────────────────────────────────
    _render_connection_status(state)

    # ── Playback controls ───────────────────────────────────────────────────
    col_pause, col_stop = st.columns(2)
    paused = st.session_state.get(_KEY_PAUSED, False)
    if col_pause.button("Pause display" if not paused else "Resume display"):
        st.session_state[_KEY_PAUSED] = not paused
    if col_stop.button("Stop collector", disabled=state.stopped):
        collector.stop()
        st.warning(
            "Collector stopped. Subscription released. Restart the app to reconnect."
        )

    if paused:
        st.info("Display paused — ingestion continues in the background.")
        return

    # ── Session state ───────────────────────────────────────────────────────
    mon = state.monitor_state
    if mon is None:
        st.info("Waiting for first bar…")
        return

    _render_range_status(mon)
    _render_simulated_position(mon)
    _render_signal_history(mon)
    _render_collector_stats(state)


# ---------------------------------------------------------------------------
# Connection / freshness panel
# ---------------------------------------------------------------------------


def _render_connection_status(state: CollectorState) -> None:
    conn = state.connection_state
    colour = {
        "connected": "🟢",
        "connecting": "🟡",
        "recovering": "🟠",
        "disconnected": "🔴",
        "stopped": "⚫",
    }.get(conn, "❓")
    staleness = ""
    if state.stale:
        staleness = " ⚠️ STALE — no bar received recently"
    st.markdown(
        f"**Feed:** `{state.symbol}` / `{state.feed}` / `{state.timeframe}`  "
        f"  {colour} `{conn}`{staleness}"
    )
    cols = st.columns(3)
    cols[0].metric("Bars received", state.bars_received)
    cols[1].metric("Bars finalized", state.bars_finalized)
    cols[2].metric("Gaps detected", state.gaps_detected)
    if state.last_bar_market_ts:
        st.caption(f"Last bar market time: {state.last_bar_market_ts}")
    if state.last_heartbeat_at:
        st.caption(
            f"Last heartbeat (wall): {state.last_heartbeat_at} "
            f"— heartbeats do not reset bar freshness"
        )
    if state.bars_dropped_dup:
        st.caption(f"Duplicate bars silently dropped: {state.bars_dropped_dup}")


# ---------------------------------------------------------------------------
# Opening range panel
# ---------------------------------------------------------------------------


def _render_range_status(mon) -> None:
    st.subheader("Opening Range")
    if mon.degraded:
        st.error(f"⚠️ Degraded — no new signals: {mon.degraded_reason}")
    if mon.or_frozen:
        status = "✅ Frozen" if mon.or_valid else "❌ Invalid (too few bars)"
        st.markdown(f"**Status:** {status}")
        cols = st.columns(2)
        cols[0].metric("OR High", f"{mon.or_high:.2f}" if mon.or_high else "N/A")
        cols[1].metric("OR Low", f"{mon.or_low:.2f}" if mon.or_low else "N/A")
    else:
        forming_high = f"{mon.or_high:.2f}" if mon.or_high else "—"
        forming_low = f"{mon.or_low:.2f}" if mon.or_low else "—"
        st.markdown(
            f"⏳ **Forming** — observed high: `{forming_high}`, low: `{forming_low}` "
            f"({mon.bars_observed} bars so far)"
        )


# ---------------------------------------------------------------------------
# Simulated position panel
# ---------------------------------------------------------------------------


def _render_simulated_position(mon) -> None:
    st.subheader("Simulated position")
    st.caption("Simulated only — no real orders are submitted.")
    if mon.open_signal is not None:
        sig = mon.open_signal
        st.success(
            f"OPEN {sig.direction} @ {sig.entry_price:.2f} | "
            f"SL {sig.stop_loss:.2f} | TP {sig.take_profit:.2f} | "
            f"1R = {sig.risk_amount:.2f}"
        )
    else:
        st.info("No open simulated position.")
    pnl_label = f"Simulated session P&L: ${mon.realized_pnl_sim:+,.2f}"
    trade_label = f"Simulated trades this session: {mon.trades_this_session}"
    st.caption(f"{pnl_label}  ·  {trade_label}")


# ---------------------------------------------------------------------------
# Signal history panel
# ---------------------------------------------------------------------------


def _render_signal_history(mon) -> None:
    st.subheader("Simulated signal history")
    if not mon.closed_signals:
        st.info("No closed simulated signals this session.")
        return
    rows = []
    for sig in mon.closed_signals:
        rows.append(
            {
                "Direction": sig.direction,
                "Entry": sig.entry_price,
                "Stop": sig.stop_loss,
                "Target": sig.take_profit,
                "1R": sig.risk_amount,
                "OR High": sig.or_high,
                "OR Low": sig.or_low,
            }
        )
    import pandas as pd

    st.dataframe(pd.DataFrame(rows), use_container_width=True)


# ---------------------------------------------------------------------------
# Collector stats
# ---------------------------------------------------------------------------


def _render_collector_stats(state: CollectorState) -> None:
    with st.expander("Collector internals"):
        if state.monitor_state:
            st.json(snapshot(state.monitor_state))
        else:
            st.json({"status": "no monitor state yet"})


# ---------------------------------------------------------------------------
# Demo controls (when no collector is attached)
# ---------------------------------------------------------------------------


def _render_demo_controls(symbol: str, feed: str, timeframe: str) -> None:
    """Attach a FakeStreamAdapter collector for offline demonstration."""
    st.subheader("Demo mode")
    st.caption(
        "Attach a deterministic fake stream to explore the monitor UI "
        "without credentials or a real market data feed."
    )
    or_minutes = st.slider("OR minutes", 5, 30, 15)
    total_bars = st.slider("Total bars to stream", 30, 390, 100)
    breakout_bar = st.number_input(
        "Breakout bar index (–1 = no breakout)",
        min_value=-1,
        max_value=total_bars - 1,
        value=20,
    )
    direction = st.radio("Breakout direction", ["LONG", "SHORT"], horizontal=True)
    if st.button("Attach fake stream"):
        from src.data.live_stream import FakeStreamAdapter, make_fake_bars

        session_id = pd.Timestamp.now(tz="America/New_York").date().isoformat()
        bars = make_fake_bars(
            session_id,
            or_minutes=or_minutes,
            total_bars=total_bars,
            breakout_bar=int(breakout_bar) if breakout_bar >= 0 else None,
            breakout_direction=direction,
            symbol=symbol,
            feed=feed,
        )
        adapter = FakeStreamAdapter(bars)
        cfg = SessionConfig(or_minutes=or_minutes)
        collector = StreamCollector(adapter, symbol, feed, timeframe, cfg)
        collector._session_id = session_id
        from src.services.monitor_state import new_session

        collector._monitor_state = new_session(session_id)
        st.session_state[_KEY_COLLECTOR] = collector
        st.rerun()


# ---------------------------------------------------------------------------
# API for wiring a real collector before rendering
# ---------------------------------------------------------------------------


def attach_collector(collector: StreamCollector) -> None:
    """Wire a StreamCollector into the Streamlit session state.

    Call this once (e.g., in app.py after initializing the real adapter) before
    ``render_monitor()`` is invoked.  Does nothing if a collector is already
    attached and not stopped.
    """
    existing: StreamCollector | None = st.session_state.get(_KEY_COLLECTOR)
    if existing is not None and not existing.state.stopped:
        return  # Deduplicate across browser reloads (P5-A1)
    st.session_state[_KEY_COLLECTOR] = collector
