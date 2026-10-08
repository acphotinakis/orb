"""Compatibility-aware completed-run comparison."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.services.artifact_store import read_run_artifacts
from src.services.replay import (
    align_equity,
    compatibility_notes,
    diff_configs,
    metric_deltas,
    normalized_returns,
)


def render_comparison(root, run_id, left, options):
    st.subheader("Compare completed runs")
    label = st.selectbox("Compare with", sorted(options), key="compare_with")
    try:
        right = read_run_artifacts(root, options[label])
    except Exception as exc:
        st.error(f"Comparison unavailable: {exc}")
        return
    for note in compatibility_notes(left.manifest, right.manifest):
        st.warning(note)
    st.caption(
        "A is the selected run; B is Compare with. "
        "Metric deltas are B − A over each original full period, "
        "not recalculated for the chart window."
    )
    st.dataframe(
        pd.DataFrame(
            metric_deltas(left.metrics, right.metrics),
            columns=["section", "metric", "A", "B", "B − A"],
        ),
        width="stretch",
    )
    # String rendering permits mixed config types without coercing numeric deltas.
    st.subheader("Configuration differences")
    st.dataframe(
        pd.DataFrame(
            [
                (p, repr(a), repr(b))
                for p, a, b in diff_configs(left.config_snapshot, right.config_snapshot)
            ],
            columns=["setting", "A", "B"],
        ),
        width="stretch",
    )
    st.subheader("Recorded source and data identities")
    st.json(
        {
            "A": {k: left.manifest.get(k) for k in ("source", "datasets")},
            "B": {k: right.manifest.get(k) for k in ("source", "datasets")},
        }
    )
    mode = st.radio(
        "Alignment",
        ["common", "full"],
        format_func=lambda x: (
            "Common timestamps"
            if x == "common"
            else "Full periods (missing values retained)"
        ),
    )
    measure = st.radio("Equity units", ["Absolute equity", "Normalized return"])
    try:
        aligned = align_equity(left.equity, right.equity, mode)
        if aligned.empty:
            st.info(
                "No overlapping timestamps. "
                "Choose Full periods to inspect each run separately."
            )
            return
        if len(aligned) == 1:
            st.info("Only one common timestamp; points do not establish a return path.")
        columns = ("equity_a", "equity_b")
        if measure == "Normalized return":
            a = (
                left.manifest.get("config", {})
                .get("execution", {})
                .get("initial_capital")
            )
            b = (
                right.manifest.get("config", {})
                .get("execution", {})
                .get("initial_capital")
            )
            aligned = normalized_returns(aligned, a, b)
            st.caption(
                f"Baselines: A initial capital {a}; B initial capital {b}. "
                "Return = equity / original initial capital − 1."
            )
            columns = ("return_a", "return_b")
        else:
            st.caption(
                "Absolute account equity in dollars; "
                "unequal initial capital is not adjusted."
            )
        st.caption(
            "One equity observation per timestamp: the last recorded mark wins "
            "(including fallback EOD). No interpolation or zero-fill; "
            "different observed calendars are labeled."
        )
        if set(left.equity.timestamp) != set(right.equity.timestamp):
            st.warning(
                "Different observed calendars/timestamps; "
                "the common view excludes unmatched observations."
            )
        fig = go.Figure()
        for name, column in zip(("A", "B"), columns):
            fig.add_trace(
                go.Scatter(
                    x=aligned.timestamp,
                    y=aligned[column],
                    name=name,
                    mode="lines+markers",
                    connectgaps=False,
                )
            )
        st.plotly_chart(fig, width="stretch")
    except (ValueError, KeyError, TypeError) as exc:
        st.info(f"Equity comparison unavailable: {exc}")
