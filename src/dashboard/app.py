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
    selected_label = st.sidebar.selectbox("Run", sorted(options))
    run_id = options[selected_label]

    try:
        art = read_run_artifacts(root, run_id)
    except ConfigurationError as exc:
        st.error(f"Run unavailable: {exc}. Other runs remain usable.")
        return

    manifest = art.manifest
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


if __name__ == "__main__":
    main()
