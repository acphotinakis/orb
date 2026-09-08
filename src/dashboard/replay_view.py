"""Replay presentation consumes cursor state exclusively; no completed artifacts."""
from __future__ import annotations

import json
import time

import pandas as pd
import streamlit as st

from src.dashboard.charts import session_candlestick, equity_figure, downsample_for_display
from src.services.artifact_store import read_trace, read_download_bytes
from src.services.replay import ReplayIndex, Playback, playback_action, filter_session_events
from src.services.source_snapshot import historical_source


def visible_components(state):
    """Single causality boundary for charts, hover data, tables, cards, inputs."""
    closed = pd.DataFrame([e.get("trade", {}) for e in state.closed_trades])
    markers = closed.copy()
    if state.open_position:
        p = state.open_position
        markers = pd.concat([markers, pd.DataFrame([{
            "trade_id": p["trade_id"], "direction": p["direction"],
            "entry_time": p["bar_start"], "entry_price": p["entry_price"],
            "stop_price": p["stop_loss"], "target_price": p["take_profit"],
        }])], ignore_index=True)
    high = state.range_frozen_high if state.range_frozen else state.range_observed_high
    low = state.range_frozen_low if state.range_frozen else state.range_observed_low
    return {
        "candles": session_candlestick(state.visible_bars, or_high=high, or_low=low,
                                       session_trades=markers),
        "equity_chart": equity_figure(downsample_for_display(state.equity)),
        "bars": state.visible_bars.tail(500), "trades": closed.tail(500),
        "position": state.open_position, "decision": state.decision,
        "metrics": {"Realized session P&L ($)": state.realized_pnl,
                    "Closed trades so far": len(state.closed_trades),
                    "Equity at cursor ($)": float(state.equity.iloc[-1]["equity"]) if not state.equity.empty else None},
        "range": {"frozen": state.range_frozen, "high": high, "low": low},
    }


def render_replay(root, run_id, manifest):
    st.subheader("Historical replay")
    st.caption("Only information available at the cursor. Full-run metrics and downloads are in Completed results.")
    try:
        header, events = read_trace(root, run_id)
    except (ValueError, Exception) as exc:
        st.info(f"Replay unavailable: {exc}. Record a new run with Record replay trace enabled.")
        return
    if header.get("truncated"):
        st.warning("Trace truncated: replay covers only the recorded prefix; the end is not the completed run.")
    sessions = sorted({e["session_id"] for e in events if e.get("session_id")})
    if not sessions:
        st.info("Trace has no session events.")
        return
    session = st.selectbox("Replay session", sessions, key=f"replay_session_{run_id}")
    digest = manifest.get("artifacts", {}).get("results/decision_trace.jsonl", {}).get("sha256")
    identity = (str(root), run_id, session, digest)
    if st.session_state.get("replay_identity") != identity:
        st.session_state["replay_identity"] = identity
        st.session_state["replay_index"] = ReplayIndex(filter_session_events(events, session))
        st.session_state["playback"] = Playback()
    try:
        raw, _ = read_download_bytes(root, run_id, "results/source_snapshot.json")
        snapshot = json.loads(raw)
    except Exception:
        snapshot = {}
    _playback_fragment(snapshot, manifest.get("source", {}))


def _act(action, value=None):
    st.session_state["playback"] = playback_action(
        st.session_state["playback"], action, len(st.session_state["replay_index"].events),
        time.monotonic(), value,
    )


def _seek():
    _act("seek", st.session_state["replay_cursor"])


def _speed():
    _act("speed", st.session_state["replay_speed"])


@st.fragment(run_every=0.125)
def _playback_fragment(snapshot, source):
    index = st.session_state["replay_index"]
    _act("tick")
    playback = st.session_state["playback"]
    controls = st.columns(5)
    controls[0].button("Play", on_click=_act, args=("play",), disabled=playback.cursor >= len(index.events))
    controls[1].button("Pause", on_click=_act, args=("pause",), disabled=not playback.playing)
    controls[2].button("Step", on_click=_act, args=("step",), disabled=playback.cursor >= len(index.events))
    controls[3].button("Reset", on_click=_act, args=("reset",))
    st.session_state["replay_speed"] = playback.speed
    controls[4].selectbox("Speed", [0.5, 1.0, 2.0, 4.0, 8.0], key="replay_speed", on_change=_speed)
    st.session_state["replay_cursor"] = playback.cursor
    st.slider("Event cursor", 0, len(index.events), key="replay_cursor", on_change=_seek)
    state = index.seek(playback.cursor, max_bars=2000)
    view = visible_components(state)
    st.text(f"Cursor {playback.cursor}/{len(index.events)} — {state.cursor_time or 'before first observation'}")
    for col, (label, value) in zip(st.columns(3), view["metrics"].items()):
        col.metric(label, "N/A" if value is None else f"{value:,.2f}")
    st.text(f"Range {'frozen' if state.range_frozen else 'forming'}: {view['range']['low']} – {view['range']['high']}")
    st.caption("Charts show at most the latest 2,000 observed candles; tables show the latest 500 rows. Gaps are retained.")
    st.plotly_chart(view["candles"], width="stretch")
    st.plotly_chart(view["equity_chart"], width="stretch")
    st.subheader("Observed bars")
    st.dataframe(view["bars"], width="stretch")
    st.subheader("Closed trades at cursor")
    st.dataframe(view["trades"], width="stretch")
    st.subheader("Position at cursor")
    st.json(view["position"] or {})
    st.subheader("Executed decision and recorded code")
    st.json(view["decision"] or {})
    st.caption(f"Recorded source: {snapshot.get('source', source)}")
    decision = view["decision"] or {}
    rule = snapshot.get("rule_sources", {}).get(decision.get("rule_id"), {})
    if rule:
        try:
            st.text(f"{decision['rule_id']} → {rule['module']}:{rule['function']}")
            st.code(historical_source(snapshot, rule["module"], rule["function"]), language="python")
        except ValueError as exc:
            st.info(str(exc))
    elif not snapshot:
        st.info("Historical source snapshot unavailable; current worktree code is never substituted.")
    else:
        st.info("No executed rule selected at this cursor.")
