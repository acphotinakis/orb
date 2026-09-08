"""
src.dashboard.app
=================
ORB results explorer (P2, strictly read-only).

Browsing a run only reads validated artifacts through
:mod:`src.services.artifact_store` and renders them with
:mod:`src.dashboard.charts`.  This module never imports the backtest engine,
data fetchers, or the pipeline, so exploring results cannot trigger a
simulation, a download, or an output mutation (P2-T08 guards this).

Run::

    .orb_venv/bin/python -m src.dashboard.demo --root demo_runs
    .orb_venv/bin/streamlit run src/dashboard/app.py
"""

from __future__ import annotations

from pathlib import Path
import sys

# Streamlit executes this file with the script directory on sys.path.
# Resolve the checkout from the file so direct launches also work without
# PYTHONPATH, regardless of the caller's working directory.
_REPOSITORY_ROOT = str(Path(__file__).resolve().parents[2])
if _REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, _REPOSITORY_ROOT)

import pandas as pd
import streamlit as st

from src.dashboard.charts import (
    downsample_for_display,
    equity_figure,
    r_distribution_figure,
    session_candlestick,
)
from src.services.artifact_store import (
    RUN_STATUS_COMPLETE,
    display_value,
    discover_runs,
    is_synthetic_run,
    read_download_bytes,
    read_run_artifacts,
    read_session_bars,
    to_et_display,
)
from src.common.exceptions import ConfigurationError
from src.services.run_models import RESEARCH_PRESET_1MIN, build_run_request
from src.services.run_service import RunService

MAX_TABLE_ROWS = 500


def _run_option_label(run) -> str:
    base = run.run_label or run.run_id
    stamp = f" — completed {run.completed_at}" if run.completed_at else ""
    return f"{base} [{run.run_id[:8]}]{stamp}"


def _metric_cards(tm: dict, pm: dict) -> None:
    cols = st.columns(4)
    cols[0].metric("Total net P&L ($)", display_value(tm.get("total_pnl_dollars")))
    cols[1].metric("Win rate", display_value(tm.get("win_rate"), suffix=""))
    cols[2].metric("Total trades", display_value(tm.get("total_trades")))
    cols[3].metric("Ending equity ($)", display_value(pm.get("ending_equity")))
    cols2 = st.columns(4)
    cols2[0].metric("Profit factor", display_value(tm.get("profit_factor")))
    cols2[1].metric("Expectancy (R)", display_value(tm.get("expectancy_r")))
    cols2[2].metric("Max drawdown (%)", display_value(pm.get("max_drawdown_pct")))
    cols2[3].metric("Total return (%)", display_value(pm.get("total_return_pct")))
    st.caption(
        "Full-run metrics with artifact units. Filtered table statistics below "
        "are labeled as filtered and never replace these cards."
    )


def _trade_table(trades: pd.DataFrame) -> pd.DataFrame:
    st.subheader("Trades")
    if trades.empty:
        st.info("This run executed no trades — empty tables and plots are expected.")
        return trades
    directions = ["All"] + sorted(trades["direction"].dropna().unique().tolist())
    reasons = ["All"] + sorted(trades["exit_reason"].dropna().unique().tolist())
    col_a, col_b = st.columns(2)
    direction = col_a.selectbox("Direction filter", directions, key="dir_filter")
    reason = col_b.selectbox("Exit-reason filter", reasons, key="reason_filter")
    filtered = trades
    if direction != "All":
        filtered = filtered[filtered["direction"] == direction]
    if reason != "All":
        filtered = filtered[filtered["exit_reason"] == reason]
    if len(filtered) != len(trades):
        st.caption(
            f"Filtered view: {len(filtered)} of {len(trades)} trades. "
            "Headline metric cards above remain full-run values."
        )
    shown = filtered.head(MAX_TABLE_ROWS)
    st.dataframe(shown, width="stretch")
    if len(filtered) > MAX_TABLE_ROWS:
        st.caption(
            f"Showing first {MAX_TABLE_ROWS} of {len(filtered)} rows; "
            "download trades.csv for the full-resolution ledger."
        )
    return filtered


def main() -> None:
    st.set_page_config(page_title="ORB Results Explorer", layout="wide")
    st.title("ORB Results Explorer")
    st.caption("Read-only: browsing never runs simulations or fetches market data.")

    root = st.sidebar.text_input("Storage root", value=".")
    _run_control_section(root)
    try:
        runs = discover_runs(root)
    except Exception as exc:  # noqa: BLE001 — bad root must not crash the app
        st.error(f"Cannot list runs under {root!r}: {exc}")
        return

    complete = [r for r in runs if r.status == RUN_STATUS_COMPLETE]
    others = [r for r in runs if r.status != RUN_STATUS_COMPLETE]

    if not complete:
        st.info(
            "No completed runs found. Generate the offline demo:\n\n"
            "`.orb_venv/bin/python -m src.dashboard.demo --root demo_runs`\n\n"
            "then enter `demo_runs` as the storage root. No credentials or "
            "network access are required."
        )
        if others:
            with st.expander("Unsupported runs"):
                for run in others:
                    st.text(f"{run.run_id}: {run.status} — {run.detail}")
        return

    options = { _run_option_label(r): r.run_id for r in complete }
    selected_label = st.sidebar.selectbox("Run", sorted(options), key="run_selector")
    run_id = options[selected_label]

    try:
        art = read_run_artifacts(root, run_id)
    except ConfigurationError as exc:
        st.error(f"Run unavailable: {exc}. Other runs remain usable.")
        return

    manifest = art.manifest
    mode = st.sidebar.radio("View", ["Completed results", "Replay", "Compare", "Market Monitor"], key="view_mode")
    if mode == "Replay":
        from src.dashboard.replay_view import render_replay
        render_replay(root, run_id, manifest)
        return
    if mode == "Compare":
        from src.dashboard.comparison_view import render_comparison
        render_comparison(root, run_id, art, options)
        return
    if mode == "Market Monitor":
        from src.dashboard.monitor_view import render_monitor
        render_monitor(
            symbol=manifest.get("config", {}).get("data", {}).get("symbol", "SPY"),
            feed=manifest.get("config", {}).get("data", {}).get("feed", "sip"),
            timeframe=manifest.get("config", {}).get("data", {}).get("timeframe", "1Min"),
        )
        return
    if is_synthetic_run(manifest):
        st.warning("Synthetic demonstration data — not market results.")
    st.text(f"Run: {manifest.get('run_label') or run_id}")
    datasets = manifest.get("datasets", {})
    processed = datasets.get("processed", {})
    st.text(
        f"Dataset identity {str(processed.get('identity_hash', '?'))[:12]}… | "
        f"source {str(datasets.get('raw', {}).get('fingerprint', '?'))[:12]}… | "
        f"completed {manifest.get('completed_at', '?')}"
    )

    tm = dict(art.metrics.get("trade_metrics", {}))
    pm = dict(art.metrics.get("portfolio_metrics", {}))
    _metric_cards(tm, pm)

    st.subheader("Equity")
    equity = art.equity
    if not equity.empty:
        first_eq = float(equity["equity"].iloc[0])
        last_eq = float(equity["equity"].iloc[-1])
        st.caption(
            f"Text summary: equity from ${first_eq:,.2f} to ${last_eq:,.2f} "
            f"over {len(equity)} recorded bars."
        )
    st.plotly_chart(
        equity_figure(downsample_for_display(equity)), width="stretch"
    )

    filtered_trades = _trade_table(art.trades)

    st.subheader("Session explorer")
    sessions = sorted(equity["session_id"].dropna().unique().tolist()) if (
        not equity.empty and "session_id" in equity.columns
    ) else []
    if not sessions:
        st.info("No sessions recorded for this run.")
    else:
        default_session = None
        if not filtered_trades.empty and "date" in filtered_trades.columns:
            dates = filtered_trades["date"].dropna().unique().tolist()
            if dates and dates[0] in sessions:
                default_session = dates[0]
        session = st.selectbox(
            "Session",
            sessions,
            index=sessions.index(default_session) if default_session else 0,
        )
        try:
            bars = read_session_bars(root, manifest, session)
            st.text(f"Session {session}: {len(bars)} bars (selected-session OHLCV only).")
            session_trades = (
                filtered_trades[filtered_trades["date"] == session]
                if (not filtered_trades.empty and "date" in filtered_trades.columns)
                else filtered_trades.iloc[0:0]
            )
            or_high = float(session_trades["or_high"].iloc[0]) if (
                not session_trades.empty and "or_high" in session_trades.columns
            ) else None
            or_low = float(session_trades["or_low"].iloc[0]) if (
                not session_trades.empty and "or_low" in session_trades.columns
            ) else None
            st.plotly_chart(
                session_candlestick(
                    bars, or_high=or_high, or_low=or_low,
                    session_trades=session_trades,
                ),
                width="stretch",
            )
            if not session_trades.empty:
                trade_ids = session_trades["trade_id"].astype(str).tolist()
                chosen = st.selectbox("Trade detail", trade_ids, key="trade_detail")
                detail = session_trades[
                    session_trades["trade_id"].astype(str) == chosen
                ].iloc[0]
                detail_text = "\n".join(
                    f"{col}: "
                    f"{to_et_display(detail[col]) if col in ('entry_time', 'exit_time') else detail[col]}"
                    for col in session_trades.columns
                )
                st.text(detail_text)  # rendered as text, never as HTML
        except ConfigurationError as exc:
            st.error(f"Session unavailable: {exc}")

    st.subheader("R multiples")
    st.plotly_chart(r_distribution_figure(art.trades), width="stretch")

    st.subheader("Downloads")
    for rel in (
        "results/trades.csv",
        "results/equity_curve.csv",
        "results/daily_summary.csv",
        "results/metrics.json",
        "config_snapshot.yaml",
    ):
        try:
            data, filename = read_download_bytes(root, run_id, rel)
            st.download_button(
                f"Download {filename}", data=data, file_name=filename, key=f"dl_{filename}"
            )
        except ConfigurationError:
            st.caption(f"{rel} not available for this run.")

    if others:
        with st.expander("Unsupported runs"):
            for run in others:
                st.text(f"{run.run_id}: {run.status} — {run.detail}")


def _run_control_section(root: str) -> None:
    """Submit validated runs, poll durable progress, cancel, reconnect (P3)."""
    st.sidebar.header("Run control")
    # Lazy service: merely rendering the explorer must not create storage
    # (read-only boundary — P2-A4). The registry initializes on first use.
    _svc: list = []

    def svc() -> RunService:
        if not _svc:
            _svc.append(RunService(root))
        return _svc[0]
    if "request_token" not in st.session_state:
        import uuid as _uuid

        st.session_state["request_token"] = _uuid.uuid4().hex
    if "watched_run" not in st.session_state:
        st.session_state["watched_run"] = ""

    with st.sidebar.form("run_submit_form"):
        symbol = st.text_input("Symbol", value="SPY")
        start_date = st.text_input("Start date (YYYY-MM-DD)", value="")
        end_date = st.text_input("End date (YYYY-MM-DD)", value="")
        timeframe = st.selectbox("Timeframe", ["1Min", "5Min", "15Min"], index=0)
        refresh = st.checkbox("Refresh provider data", value=False)
        record_trace = st.checkbox("Record replay trace", value=False)
        submitted = st.form_submit_button("Run")
    watched = st.sidebar.text_input(
        "Watch run ID (reconnect)", value=st.session_state["watched_run"]
    )
    st.session_state["watched_run"] = watched
    if st.sidebar.button("Cancel watched run", disabled=not watched):
        try:
            new_status = svc().cancel_run(watched)
            st.sidebar.text(f"Cancel → {new_status}")
        except KeyError:
            st.sidebar.error(f"Unknown run '{watched}'.")

    if submitted:
        try:
            overrides = {"data": {"timeframe": timeframe}}
            if symbol.strip():
                overrides["strategy"] = {"ticker": symbol.strip().upper()}
                overrides["data"]["symbol"] = symbol.strip().upper()
            request = build_run_request(
                "config/default_config.yaml",
                config_overrides={**RESEARCH_PRESET_1MIN, **overrides},
                options={
                    "start_date": start_date.strip() or None,
                    "end_date": end_date.strip() or None,
                    "run_label": f"dashboard:{symbol.strip().upper()}",
                    "log_level": "INFO",
                    "refresh_cache": refresh,
                    "record_trace": record_trace,
                },
            )
        except ConfigurationError as exc:
            st.sidebar.error(f"Invalid request: {exc}")
            return
        except FileNotFoundError as exc:
            st.sidebar.error(f"Config missing: {exc}")
            return
        run_id = svc().submit_run(request, st.session_state["request_token"])
        st.session_state["watched_run"] = run_id
        st.sidebar.text(f"Submitted run {run_id[:8]}… (retry-safe token)")

    if st.session_state["watched_run"]:
        _watch_fragment(root, st.session_state["watched_run"])


@st.fragment(run_every=1)
def _watch_fragment(root: str, run_id: str) -> None:
    """Timed progress poll: display-only, never owns the job (P3-O3)."""
    svc = RunService(root)
    try:
        row = svc.get_run(run_id)
    except KeyError:
        st.sidebar.error(f"Unknown run '{run_id}'.")
        return
    st.sidebar.text(f"Status: {row['status']}")
    if row.get("error"):
        st.sidebar.text(f"Error: {row['error'][:200]}")
    try:
        events = svc.get_events(run_id, limit=5)
    except KeyError:
        return
    for event in events[-5:]:
        st.sidebar.text(
            f"#{event.get('seq')}: {event.get('event_type')} "
            f"{event.get('stage', '')} {event.get('session_id', '')}"
        )


if __name__ == "__main__":
    main()
