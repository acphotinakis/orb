"""
tests/dashboard/test_results_explorer.py
=========================================
P2-T01..T12: offline demo, artifact parity, selection, empty states,
corruption tolerance, timezone labels, read-only boundary, traversal safety,
large-table bounds, scope labeling, and UI smoke tests.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from src.common.exceptions import ConfigurationError
from src.dashboard import charts
from src.dashboard.demo import generate_demo
from src.services.artifact_store import (
    RUN_STATUS_COMPLETE,
    discover_runs,
    display_value,
    is_synthetic_run,
    read_download_bytes,
    read_run_artifacts,
    read_session_bars,
    to_et_display,
)


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    generate_demo(root)
    return root


def _complete_runs(demo_root):
    return [r for r in discover_runs(demo_root) if r.status == RUN_STATUS_COMPLETE]


def _run_by_prefix(demo_root, prefix):
    return next(r for r in _complete_runs(demo_root) if r.run_id.startswith(prefix))


# ---------------------------------------------------------------------------
# T01: offline first launch
# ---------------------------------------------------------------------------


def test_empty_root_discovers_nothing(tmp_path):
    assert discover_runs(tmp_path) == []


def test_demo_generates_offline_without_credentials(demo_root):
    runs = _complete_runs(demo_root)
    assert {r.run_id for r in runs} == {"demo_normal", "demo_zero_trades"}
    for run in runs:
        manifest = json.loads((run.experiment_dir / "manifest.json").read_text())
        assert is_synthetic_run(manifest) is True


# ---------------------------------------------------------------------------
# T02: artifact parity (metrics, rows, series match fixture files)
# ---------------------------------------------------------------------------


def test_headline_parity_with_artifacts(demo_root):
    run = _run_by_prefix(demo_root, "demo_normal")
    art = read_run_artifacts(demo_root, run.run_id)
    on_disk_metrics = json.loads(
        (run.experiment_dir / "results" / "metrics.json").read_text()
    )
    assert dict(art.metrics) == on_disk_metrics
    on_disk_trades = pd.read_csv(run.experiment_dir / "results" / "trades.csv")
    pd.testing.assert_frame_equal(art.trades, on_disk_trades)
    on_disk_equity = pd.read_csv(run.experiment_dir / "results" / "equity_curve.csv")
    pd.testing.assert_frame_equal(art.equity, on_disk_equity)
    # Chart inputs derive from the same frames (no recomputation).
    fig = charts.equity_figure(art.equity)
    assert list(fig.data[0].y) == art.equity["equity"].tolist()


# ---------------------------------------------------------------------------
# T03: selection points at the right trade/session; runs reset explicitly
# ---------------------------------------------------------------------------


def test_trade_selection_syncs_session(demo_root):
    run = _run_by_prefix(demo_root, "demo_normal")
    art = read_run_artifacts(demo_root, run.run_id)
    trade = art.trades.iloc[0]
    session = trade["date"]
    bars = read_session_bars(demo_root, art.manifest, session)
    assert (bars["session_id"] == session).all()
    assert pd.Timestamp(trade["entry_time"]) >= bars["timestamp"].min()
    assert pd.Timestamp(trade["exit_time"]) <= bars["timestamp"].max()


def test_switching_runs_resets_data(demo_root):
    normal = read_run_artifacts(demo_root, "demo_normal")
    zero = read_run_artifacts(demo_root, "demo_zero_trades")
    assert len(normal.trades) > 0 and zero.trades.empty
    assert normal.manifest["run_id"] != zero.manifest["run_id"]


# ---------------------------------------------------------------------------
# T04: empty states never crash, unavailable never renders as zero
# ---------------------------------------------------------------------------


def test_zero_trade_run_reads_cleanly(demo_root):
    art = read_run_artifacts(demo_root, "demo_zero_trades")
    assert art.trades.empty
    assert not art.equity.empty
    assert charts.r_distribution_figure(art.trades).layout.annotations
    assert display_value("inf") == "∞"
    assert display_value(None) == "n/a"
    assert display_value(float("nan")) == "n/a"
    assert display_value(0.0) == "0.0"


def test_empty_frames_produce_annotated_figures():
    assert charts.equity_figure(pd.DataFrame()).layout.annotations
    assert charts.session_candlestick(pd.DataFrame()).layout.annotations
    assert charts.session_candlestick(
        pd.DataFrame({"timestamp": []})
    ).layout.annotations


# ---------------------------------------------------------------------------
# T05/T06: corrupt, partial, legacy, disappearing artifacts
# ---------------------------------------------------------------------------


def _clone_run(demo_root, tmp_path, prefix="demo_normal"):
    src = _run_by_prefix(demo_root, prefix).experiment_dir
    dest_root = tmp_path / "clone"
    import shutil

    shutil.copytree(demo_root / "experiments", dest_root / "experiments")
    shutil.copytree(demo_root / "data", dest_root / "data", dirs_exist_ok=True)
    return dest_root, dest_root / "experiments" / src.name


def test_truncated_csv_is_explicit(tmp_path, demo_root):
    dest_root, dest = _clone_run(demo_root, tmp_path)
    (dest / "results" / "trades.csv").write_text("trade_id,directio", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        read_run_artifacts(dest_root, "demo_normal")


def test_checksum_mismatch_marks_corrupt_others_usable(tmp_path, demo_root):
    dest_root, dest = _clone_run(demo_root, tmp_path)
    with open(dest / "results" / "trades.csv", "a", encoding="utf-8") as fh:
        fh.write("9999,LONG,2099-01-01,0,0,0,0,0,0,0,0,0,0,0,0,EOD,0,0\n")
    statuses = {r.run_id: r.status for r in discover_runs(dest_root)}
    assert statuses["demo_normal"] == "corrupt"
    assert statuses["demo_zero_trades"] == "complete"
    with pytest.raises(ConfigurationError):
        read_run_artifacts(dest_root, "demo_normal")
    healthy = read_run_artifacts(dest_root, "demo_zero_trades")
    assert healthy.trades.empty and not healthy.equity.empty


def test_legacy_dir_unsupported_without_guessing(tmp_path, demo_root):
    dest_root, dest = _clone_run(demo_root, tmp_path)
    (dest / "manifest.json").unlink()
    statuses = {r.run_id: r.status for r in discover_runs(dest_root)}
    assert statuses["demo_normal"] == "legacy"
    with pytest.raises(ConfigurationError):
        read_run_artifacts(dest_root, "demo_normal")


def test_disappearing_run_reads_as_unknown(tmp_path, demo_root):
    dest_root, _dest = _clone_run(demo_root, tmp_path)
    runs = discover_runs(dest_root)
    assert any(r.run_id == "demo_normal" for r in runs)
    import shutil

    shutil.rmtree(dest_root / "experiments" / "demo_normal__SPY_1Min_20240102_20240104")
    with pytest.raises(ConfigurationError):
        read_run_artifacts(dest_root, "demo_normal")


def test_absent_dataset_is_explicit(tmp_path, demo_root):
    dest_root, dest = _clone_run(demo_root, tmp_path)
    art = read_run_artifacts(dest_root, "demo_normal")
    session = art.equity["session_id"].iloc[0]
    import shutil

    shutil.rmtree(dest_root / "data")
    with pytest.raises(ConfigurationError):
        read_session_bars(dest_root, art.manifest, session)


# ---------------------------------------------------------------------------
# T07: timezone labels
# ---------------------------------------------------------------------------


def test_et_labels_carry_offsets():
    winter = to_et_display("2024-01-02T14:30:00Z")
    summer = to_et_display("2024-07-02T13:30:00Z")
    assert winter == "2024-01-02 09:30:00 ET-0500"
    assert summer == "2024-07-02 09:30:00 ET-0400"


def test_session_linkage_uses_recorded_data(demo_root):
    art = read_run_artifacts(demo_root, "demo_normal")
    for session in art.equity["session_id"].unique()[:2]:
        bars = read_session_bars(demo_root, art.manifest, session)
        assert (bars["session_id"] == session).all()


# ---------------------------------------------------------------------------
# T08: browsing never simulates, fetches, or mutates
# ---------------------------------------------------------------------------


def test_browsing_triggers_no_compute_or_fetch(demo_root, monkeypatch):
    from src import pipeline as pipeline_module
    from src.backtest import engine as engine_module
    from src.data import fetcher as fetcher_module

    def _boom(*args, **kwargs):
        raise AssertionError("compute/fetch must not run while browsing")

    monkeypatch.setattr(engine_module.BacktestEngine, "run", _boom)
    monkeypatch.setattr(fetcher_module.DataFetcher, "fetch_and_cache", _boom)
    monkeypatch.setattr(pipeline_module.ORBPipeline, "run", _boom)

    runs = discover_runs(demo_root)
    art = read_run_artifacts(demo_root, "demo_normal")
    session = art.equity["session_id"].iloc[0]
    read_session_bars(demo_root, art.manifest, session)
    charts.equity_figure(art.equity)
    charts.session_candlestick(read_session_bars(demo_root, art.manifest, session))
    charts.r_distribution_figure(art.trades)
    read_download_bytes(demo_root, "demo_normal", "results/trades.csv")
    assert [r.run_id for r in runs]


# ---------------------------------------------------------------------------
# T09: traversal, allowlist, text-as-text
# ---------------------------------------------------------------------------


def test_run_traversal_and_download_allowlist(demo_root):
    with pytest.raises(ConfigurationError):
        read_run_artifacts(demo_root, "../evil")
    with pytest.raises(ConfigurationError):
        read_download_bytes(demo_root, "demo_normal", "results/evil.csv")
    with pytest.raises(ConfigurationError):
        read_download_bytes(demo_root, "demo_normal", "../manifest.json")
    data, name = read_download_bytes(demo_root, "demo_normal", "results/trades.csv")
    on_disk = (
        demo_root
        / "experiments"
        / "demo_normal__SPY_1Min_20240102_20240104"
        / "results"
        / "trades.csv"
    ).read_bytes()
    assert data == on_disk and name == "trades.csv"


def test_evil_label_round_trips_as_text(tmp_path, demo_root):
    dest_root, dest = _clone_run(demo_root, tmp_path)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["run_label"] = "<script>alert(1)</script>,=CMD|xxx"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # Checksum over manifest is not part of validation; discovery reads it raw.
    runs = {r.run_id: r for r in discover_runs(dest_root)}
    assert runs["demo_normal"].run_label == "<script>alert(1)</script>,=CMD|xxx"


def test_app_source_has_no_executable_html():
    src = (
        Path(__file__).resolve().parents[2] / "src" / "dashboard" / "app.py"
    ).read_text()
    assert "unsafe_allow_html" not in src
    assert "st.markdown" not in src


# ---------------------------------------------------------------------------
# T10: large-table bounds
# ---------------------------------------------------------------------------


def test_large_run_stays_bounded_with_full_resolution_downloads(tmp_path):
    import numpy as np

    equity = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2024-01-02", periods=100_000, freq="1min", tz="UTC"
            ),
            "equity": np.linspace(100_000, 120_000, 100_000),
            "session_id": "2024-01-02",
        }
    )
    trades = pd.DataFrame(
        {
            "trade_id": range(10_000),
            "direction": "LONG",
            "r_multiple": 1.0,
        }
    )
    shown = charts.downsample_for_display(equity)
    assert len(shown) <= charts.MAX_DISPLAY_POINTS
    assert float(shown["equity"].max()) == float(equity["equity"].max())
    assert float(shown["equity"].min()) == float(equity["equity"].min())
    assert len(shown) == len(shown.drop_duplicates())
    fig = charts.equity_figure(shown)
    assert len(fig.data[0].y) <= charts.MAX_DISPLAY_POINTS

    dest_root = tmp_path / "big"
    exp = dest_root / "experiments" / "bigrun"
    (exp / "results").mkdir(parents=True)
    payload = trades.to_csv(index=False).encode()
    (exp / "results" / "trades.csv").write_bytes(payload)
    # Full-resolution bytes survive untouched (download path reads raw bytes).
    assert payload == (exp / "results" / "trades.csv").read_bytes()


# ---------------------------------------------------------------------------
# T12: filtered scope never relabels full-run metrics
# ---------------------------------------------------------------------------


def test_app_marks_filtered_scope(demo_root):
    from streamlit.testing.v1 import AppTest

    app_path = str(Path(__file__).resolve().parents[2] / "src" / "dashboard" / "app.py")
    at = AppTest.from_file(app_path)
    at.run()
    at.sidebar.text_input[0].set_value(str(demo_root)).run()
    assert not at.exception
    # Narrow to one exit reason: the filtered-scope caption must appear and
    # the headline metric cards must still show full-run values.
    at.selectbox(key="reason_filter").set_value("STOP").run()
    assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "Filtered view" in captions
    assert "full-run" in captions
    cards = {m.label: m.value for m in at.metric}
    assert cards["Total trades"] == "3"


def test_app_submit_flow_creates_watched_run(tmp_path):
    """P3 UI: invalid requests fail in the form; valid ones submit + complete."""
    import numpy as np
    from streamlit.testing.v1 import AppTest

    from src.services.run_service import RunService

    raw_dir = tmp_path / "data" / "raw" / "SPY" / "sip" / "1Min"
    raw_dir.mkdir(parents=True, exist_ok=True)
    bars = []
    for d in ["2024-01-02", "2024-01-03"]:
        open_utc = pd.Timestamp(f"{d} 09:30:00", tz="America/New_York").tz_convert(
            "UTC"
        )
        ts = pd.date_range(start=open_utc, periods=391, freq="1min")
        n = len(ts)
        bars.append(
            pd.DataFrame(
                {
                    "timestamp": ts,
                    "open": np.full(n, 500.0),
                    "high": np.full(n, 500.5),
                    "low": np.full(n, 499.5),
                    "close": np.full(n, 500.0),
                    "volume": np.full(n, 1000.0),
                }
            )
        )
    pd.concat(bars, ignore_index=True).to_parquet(
        raw_dir / "SPY_1Min_20240102_20240103.parquet",
        engine="pyarrow",
        compression="zstd",
        index=False,
    )

    app_path = str(Path(__file__).resolve().parents[2] / "src" / "dashboard" / "app.py")
    at = AppTest.from_file(app_path)
    at.run()
    at.sidebar.text_input[0].set_value(str(tmp_path)).run()
    assert not at.exception

    # Form widgets are flattened into the sidebar block: [root, symbol,
    # start, end, watch]. Buttons: [Run submit, Cancel watched run].
    at.sidebar.text_input[2].set_value("2024-01-02").run()  # start
    at.sidebar.text_input[3].set_value("2024-01-03").run()  # end
    at.sidebar.button[0].click().run()
    assert not at.exception
    watched = at.session_state["watched_run"]
    assert watched

    svc = RunService(tmp_path)
    deadline = __import__("time").time() + 90
    while __import__("time").time() < deadline:
        if svc.get_run(watched)["status"] in ("succeeded", "failed", "cancelled"):
            break
        __import__("time").sleep(0.5)
    row = svc.get_run(watched)

    if row["status"] != "succeeded":
        worker_log = tmp_path / "runs" / f"worker-{watched}.log"

        print("\n=== BACKGROUND RUN FAILURE ===")
        print(f"run_id: {watched}")
        print(f"status: {row['status']}")
        print(f"error: {row.get('error')}")

        if worker_log.exists():
            print("\n=== WORKER LOG ===")
            print(worker_log.read_text(encoding="utf-8", errors="replace"))
        else:
            print(f"Worker log missing: {worker_log}")

    assert row["status"] == "succeeded", row.get("error")

    # Invalid input surfaces a field error without submitting.
    at2 = AppTest.from_file(app_path)
    at2.run()
    at2.sidebar.text_input[0].set_value(str(tmp_path)).run()
    at2.sidebar.text_input[2].set_value("2024-02-30").run()  # impossible date
    at2.sidebar.button[0].click().run()  # Run submit
    assert "Invalid request" in " ".join(e.value for e in at2.error)
    assert at2.session_state["watched_run"] == ""


def test_app_run_switch_and_empty_state(demo_root, tmp_path):
    from streamlit.testing.v1 import AppTest

    app_path = str(Path(__file__).resolve().parents[2] / "src" / "dashboard" / "app.py")
    at = AppTest.from_file(app_path)
    at.run()
    at.sidebar.text_input[0].set_value(str(demo_root)).run()
    first = {m.label: m.value for m in at.metric}["Total trades"]
    assert first == "3"
    run_box = at.sidebar.selectbox(key="run_selector")
    zero = [o for o in run_box.options if "zero trades" in o][0]
    run_box.set_value(zero).run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Total trades"] == "0"

    at2 = AppTest.from_file(app_path)
    at2.run()
    at2.sidebar.text_input[0].set_value(str(tmp_path)).run()
    assert "No completed runs found" in " ".join(t.value for t in at2.info)
