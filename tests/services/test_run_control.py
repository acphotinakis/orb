"""
tests/services/test_run_control.py
===================================
P3-T01..T14 (feasible subset): idempotent submission, single-worker
supervision, durable lifecycle, progress events, cancellation boundaries,
crash recovery, redaction, and responsiveness — all offline on seeded cache.
Real worker subprocesses are used unless noted; no network, no credentials.
"""

import json
import os
import signal
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.services.artifact_store import is_run_complete
from src.services.registry import RunRegistry, redact_secrets
from src.services.run_models import build_run_request
from src.services.run_service import RunService

TERMINAL = ("cancelled", "succeeded", "failed")


def _seed_many_days(base_dir: Path, n_days: int, start="2024-01-02") -> None:
    raw_dir = base_dir / "data" / "raw" / "SPY" / "sip" / "1Min"
    raw_dir.mkdir(parents=True, exist_ok=True)
    days = pd.date_range(start=start, periods=n_days, freq="B").strftime("%Y-%m-%d")
    bars = []
    for d in days:
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
    combined = pd.concat(bars, ignore_index=True)
    combined.to_parquet(
        raw_dir
        / f"SPY_1Min_{days[0].replace('-', '')}_{days[-1].replace('-', '')}.parquet",
        engine="pyarrow",
        compression="zstd",
        index=False,
    )
    return days


def _request(days, label="t", plots=False):
    return build_run_request(
        "config/default_config.yaml",
        config_overrides={"data": {"timeframe": "1Min"}},
        options={
            "refresh_cache": False,
            "start_date": days[0],
            "end_date": days[-1],
            "generate_plots": plots,
            "run_label": label,
            "log_level": "WARNING",
        },
    )


def _wait_status(svc, run_id, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = svc.get_run(run_id)["status"]
        if status in TERMINAL:
            return status
        time.sleep(0.2)
    raise TimeoutError(f"run {run_id} stuck in {status}")


def _wait_until(fn, timeout=30.0, desc="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {desc}")


@pytest.fixture()
def seeded(tmp_path):
    days = _seed_many_days(tmp_path, 4)
    return tmp_path, days


# ---------------------------------------------------------------------------
# T01/T02: snapshot submission, idempotent retry, deliberate rerun
# ---------------------------------------------------------------------------


def test_submit_snapshot_stable_and_retry_idempotent(seeded):
    root, days = seeded
    svc = RunService(root)
    req = _request(days, label="first")
    run_id = svc.submit_run(req, request_token="tok-1")
    stored = svc.get_run(run_id)
    assert json.loads(stored["config_json"]) == req.config.to_dict()

    again = svc.submit_run(req, request_token="tok-1")
    assert again == run_id
    assert len(svc.list_runs()) == 1

    other = svc.submit_run(req, request_token="tok-2")
    assert other != run_id
    assert _wait_status(svc, run_id) == "succeeded"
    assert _wait_status(svc, other) == "succeeded"


def test_single_worker_claim_discipline(tmp_path):
    reg = RunRegistry(tmp_path)
    payload = dict(config={}, options={}, source={})
    a = reg.submit(request_token="a", run_label=None, **payload)
    b = reg.submit(request_token="b", run_label=None, **payload)
    assert reg.claim_next_queued("owner-1") == a
    assert reg.claim_next_queued("owner-2") is None  # slot busy
    assert reg.finish(a, "succeeded") is True
    assert reg.claim_next_queued("owner-2") == b


# ---------------------------------------------------------------------------
# T04: durable lifecycle across service instances (browser reload analogue)
# ---------------------------------------------------------------------------


def test_state_survives_service_restart(seeded):
    root, days = seeded
    svc = RunService(root)
    run_id = svc.submit_run(_request(days), request_token="reload-1")
    assert _wait_status(svc, run_id) == "succeeded"

    fresh = RunService(root)  # new instance, same storage: nothing is lost
    row = fresh.get_run(run_id)
    assert row["status"] == "succeeded"
    events = fresh.get_events(run_id)
    assert events and events[0]["seq"] == 1
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


def test_events_replay_dedup_and_corrupt_tail(tmp_path):
    reg = RunRegistry(tmp_path)
    run_id = reg.submit(
        request_token="t", run_label=None, config={}, options={}, source={}
    )
    reg.claim_next_queued("o")
    reg.append_event(run_id, {"event_type": "a"})
    reg.append_event(run_id, {"event_type": "b"})
    first = reg.get_events(run_id, after_seq=0)
    assert [e["event_type"] for e in first] == ["a", "b"]
    assert reg.get_events(run_id, after_seq=1) == first[1:]
    assert reg.get_events(run_id, after_seq=99) == []
    # Bounded corruption in the journal never crashes readers.
    with sqlite3.connect(reg.path) as conn:
        conn.execute(
            "INSERT INTO events (run_id, seq, event_json) VALUES (?, ?, ?)",
            (run_id, 999, "{not json"),
        )
    seqs = [e["seq"] for e in reg.get_events(run_id)]
    assert seqs == [1, 2]


# ---------------------------------------------------------------------------
# T05: cancellation at every boundary
# ---------------------------------------------------------------------------


def test_cancel_queued_never_launches(tmp_path):
    root = tmp_path
    days = _seed_many_days(root, 12)
    svc = RunService(root)
    # Hold dispatch: submit straight to the registry, cancel, then supervise.
    base_req = _request(days)
    payload = dict(
        config=base_req.config.to_dict(),
        options={
            "refresh_cache": False,
            "start_date": days[0],
            "end_date": days[-1],
            "generate_plots": False,
            "log_level": "WARNING",
            "run_label": "q",
        },
        source={},
    )
    r1 = svc.registry.submit(request_token="q1", run_label="one", **payload)
    r2 = svc.registry.submit(request_token="q2", run_label="two", **payload)
    assert svc.cancel_run(r2) == "cancelled"
    svc.ensure_supervisor()
    assert _wait_status(svc, r1) == "succeeded"
    assert svc.get_run(r2)["status"] == "cancelled"
    assert list((root / "experiments").glob(f"{r2}__*")) == []


def test_cancel_running_and_terminal_passthrough(seeded, monkeypatch):
    """Deterministic running-cancel: stall the fetch in-thread, cancel
    mid-block, and require a terminal cancelled (never success)."""
    import threading
    import time as _time

    import src.pipeline as pipeline_module
    from src.services.worker import run_worker

    root, days = seeded
    reg = RunRegistry(root)
    req = _request(days)
    run_id = reg.submit(
        request_token="cancel-run",
        run_label="cancel",
        config=req.config.to_dict(),
        options={
            "refresh_cache": False,
            "start_date": days[0],
            "end_date": days[-1],
            "generate_plots": False,
            "log_level": "WARNING",
            "run_label": "cancel",
        },
        source={},
    )
    real_fetcher = pipeline_module.DataFetcher

    class _StalledFetcher(real_fetcher):
        def fetch_and_cache(self, **kwargs):
            _time.sleep(5.0)
            return super().fetch_and_cache(**kwargs)

    monkeypatch.setattr(pipeline_module, "DataFetcher", _StalledFetcher)
    reg.claim_next_queued("test-owner")
    thread = threading.Thread(target=run_worker, args=(root, run_id))
    thread.start()
    try:
        _wait_until(
            lambda: reg.get(run_id)["status"] == "running", desc="worker pickup"
        )
        assert reg.request_cancel(run_id) == "cancelling"
        thread.join(timeout=60)
        assert not thread.is_alive()
        assert reg.get(run_id)["status"] == "cancelled"
        exp = list((root / "experiments").glob(f"{run_id}__*"))
        if exp:
            assert is_run_complete(exp[0]) is False  # never shown as success
    finally:
        thread.join(timeout=60)
    # Terminal passthrough: cancelling a finished run changes nothing.
    svc = RunService(root)
    done = svc.submit_run(req, request_token="cancel-done")
    assert _wait_status(svc, done) == "succeeded"
    assert svc.cancel_run(done) == "succeeded"


def test_terminal_race_exactly_one_wins(tmp_path):
    reg = RunRegistry(tmp_path)
    run_id = reg.submit(
        request_token="r", run_label=None, config={}, options={}, source={}
    )
    reg.claim_next_queued("o")
    assert reg.finish(run_id, "succeeded") is True
    assert reg.finish(run_id, "cancelled") is False
    assert reg.get(run_id)["status"] == "succeeded"
    assert reg.request_cancel(run_id) == "succeeded"

    run2 = reg.submit(
        request_token="r2", run_label=None, config={}, options={}, source={}
    )
    reg.claim_next_queued("o")
    reg.request_cancel(run2)
    assert reg.finish(run2, "cancelled") is True
    assert reg.finish(run2, "succeeded") is False
    assert reg.get(run2)["status"] == "cancelled"


# ---------------------------------------------------------------------------
# T07/T08: crash recovery, no duplicate dispatch, stale leases
# ---------------------------------------------------------------------------


def test_killed_worker_reconciles_failed_without_rerun(seeded):
    root, days = seeded
    more = _seed_many_days(root, 20, start="2024-03-01")
    svc = RunService(root, heartbeat_timeout_secs=5.0)
    req = build_run_request(
        "config/default_config.yaml",
        config_overrides={"data": {"timeframe": "1Min"}},
        options={
            "refresh_cache": False,
            "start_date": more[0],
            "end_date": more[-1],
            "generate_plots": False,
            "run_label": "doomed",
            "log_level": "WARNING",
        },
    )
    run_id = svc.submit_run(req, request_token="doomed")
    _wait_until(
        lambda: svc.get_run(run_id)["status"] == "running", desc="worker pickup"
    )
    pid = svc.get_run(run_id)["worker_pid"]
    assert pid
    os.kill(pid, signal.SIGKILL)
    # Poll reconcile itself: it reaps the zombie, then marks the run failed.
    deadline = time.time() + 15.0
    reconciled = []
    while time.time() < deadline:
        reconciled = svc.registry.reconcile(heartbeat_timeout_secs=-1)
        if run_id in reconciled:
            break
        time.sleep(0.1)
    assert run_id in reconciled
    assert run_id in reconciled
    assert svc.get_run(run_id)["status"] == "failed"
    assert svc.registry.active_run() is None
    # Recovery dispatches queued work, never the failed run again.
    nxt = svc.submit_run(req, request_token="doomed-next")
    assert _wait_status(svc, nxt) == "succeeded"
    assert svc.get_run(run_id)["status"] == "failed"


def test_restart_while_live_dispatches_no_duplicate(seeded):
    root, days = seeded
    more = _seed_many_days(root, 20, start="2024-04-01")
    svc = RunService(root)
    req = build_run_request(
        "config/default_config.yaml",
        config_overrides={"data": {"timeframe": "1Min"}},
        options={
            "refresh_cache": False,
            "start_date": more[0],
            "end_date": more[-1],
            "generate_plots": False,
            "run_label": "live",
            "log_level": "WARNING",
        },
    )
    run_id = svc.submit_run(req, request_token="live-1")
    _wait_until(
        lambda: svc.get_run(run_id)["status"] == "running", desc="worker pickup"
    )
    restarted = RunService(root)  # supervisor restart analogue
    assert restarted.ensure_supervisor() is None  # slot busy: no duplicate
    assert _wait_status(svc, run_id) == "succeeded"


def test_stale_lease_with_dead_pid_and_owner_checks(tmp_path):
    reg = RunRegistry(tmp_path)
    run_id = reg.submit(
        request_token="s", run_label=None, config={}, options={}, source={}
    )
    assert reg.claim_next_queued("owner-A") == run_id
    assert reg.adopt_worker(run_id, "owner-A", 999999999) is True
    assert reg.adopt_worker(run_id, "owner-B", 123) is False  # ownership wins
    assert reg.reconcile(heartbeat_timeout_secs=-1) == [run_id]
    assert reg.get(run_id)["status"] == "failed"


# ---------------------------------------------------------------------------
# T10/T11: bounded failure, redaction, unsafe labels
# ---------------------------------------------------------------------------


def test_storage_failure_is_explicit_never_success(tmp_path):
    root = tmp_path
    days = _seed_many_days(root, 2)
    svc = RunService(root)
    req = _request(days)
    # Hold dispatch, break the experiments root, then supervise: the worker
    # must fail explicitly instead of publishing success.
    run_id = svc.registry.submit(
        request_token="disk-fail",
        run_label="disk",
        config=req.config.to_dict(),
        options={
            "refresh_cache": False,
            "start_date": days[0],
            "end_date": days[-1],
            "generate_plots": False,
            "log_level": "WARNING",
            "run_label": "disk",
        },
        source={},
    )
    import shutil

    shutil.rmtree(root / "experiments", ignore_errors=True)
    (root / "experiments").write_text("not a dir", encoding="utf-8")
    svc.ensure_supervisor()
    assert _wait_status(svc, run_id, timeout=90) == "failed"
    assert svc.get_run(run_id)["error"]


def test_secret_redaction_and_unsafe_label(tmp_path):
    reg = RunRegistry(tmp_path)
    run_id = reg.submit(
        request_token="sec", run_label="../../evil", config={}, options={}, source={}
    )
    reg.claim_next_queued("o")
    reg.append_event(
        run_id,
        {"event_type": "x", "error": "fetch failed api_key=AKIAIOSFODNN7EXAMPLE"},
    )
    reg.finish(
        run_id,
        "failed",
        "boom token=sk-abcdef123456 secret hunter2",  # gitleaks:allow
    )
    row = reg.get(run_id)
    assert "AKIAIOSFODNN7EXAMPLE" not in row["error"]
    assert "sk-abcdef123456" not in row["error"]
    assert row["run_label"] == "../../evil"  # labels are data, never paths
    events = reg.get_events(run_id)
    assert "AKIAIOSFODNN7EXAMPLE" not in events[0]["error"]
    assert redact_secrets("postgres://u:password=hunter2@h/x") == "postgres://u:***"


def test_log_tail_bounded(tmp_path):
    svc = RunService(tmp_path)
    exp = tmp_path / "experiments" / "abc__SPY_1Min_20240102_20240103" / "logs"
    exp.mkdir(parents=True)
    (exp / "execution.log").write_bytes(b"y" * 100_000)
    tail = svc.read_log_tail("abc", max_bytes=1024)
    assert len(tail.encode()) <= 1024
    assert svc.read_log_tail("nope") == ""


# ---------------------------------------------------------------------------
# T12/T13: provenance flow, CLI parity without hooks
# ---------------------------------------------------------------------------


def test_registry_source_reaches_manifest(seeded):
    root, days = seeded
    svc = RunService(root)
    run_id = svc.submit_run(_request(days, label="prov"), request_token="prov-1")
    assert _wait_status(svc, run_id) == "succeeded"
    row = svc.get_run(run_id)
    exp = next((root / "experiments").glob(f"{run_id}__*"))
    import json as _json

    manifest = _json.loads((exp / "manifest.json").read_text())
    assert manifest["source"]["revision"] == _json.loads(row["source_json"])["revision"]


def test_engine_without_hooks_reports_not_cancelled(
    synthetic_rth_bars, mock_app_config
):
    res = BacktestEngine(mock_app_config).run(synthetic_rth_bars)
    assert res.cancelled is False


# ---------------------------------------------------------------------------
# T14/A4: responsiveness budgets on the recorded fixture
# ---------------------------------------------------------------------------


def test_status_and_progress_budgets(seeded):
    import time as _time

    root, days = seeded
    svc = RunService(root)
    t0 = _time.time()
    run_id = svc.submit_run(_request(days), request_token="budget-1")
    assert _time.time() - t0 < 2.0  # submit+dispatch is fast
    t0 = _time.time()
    for _ in range(5):
        svc.get_run(run_id)
    assert (_time.time() - t0) / 5 < 0.5  # status reads
    deadline = _time.time() + 2.0
    while _time.time() < deadline:
        if svc.get_events(run_id):
            break
        _time.sleep(0.05)
    else:
        raise TimeoutError("no persisted progress within 2s")
    assert _wait_status(svc, run_id) == "succeeded"


def test_cpu_loop_cancel_acknowledged_quickly(tmp_path):
    """P3-A4: a CPU-bound run acknowledges cancellation within 2 seconds."""
    import time as _time

    root = tmp_path
    days = _seed_many_days(root, 20, start="2024-05-01")
    svc = RunService(root)
    req = build_run_request(
        "config/default_config.yaml",
        config_overrides={"data": {"timeframe": "1Min"}},
        options={
            "refresh_cache": False,
            "start_date": days[0],
            "end_date": days[-1],
            "generate_plots": False,
            "run_label": "cpu-cancel",
            "log_level": "WARNING",
        },
    )
    run_id = svc.submit_run(req, request_token="cpu-cancel-1")
    _wait_until(
        lambda: svc.get_run(run_id)["status"] == "running", desc="worker pickup"
    )
    t0 = _time.time()
    assert svc.cancel_run(run_id) == "cancelling"
    _wait_until(
        lambda: svc.get_run(run_id)["status"] == "cancelled",
        timeout=30.0,
        desc="cancel ack",
    )
    ack_secs = _time.time() - t0
    print(f"\ncpu-cancel ack: {ack_secs:.2f}s")
    assert ack_secs < 2.0
    assert svc.get_run(run_id)["status"] == "cancelled"


def test_slow_fetch_keeps_status_responsive(seeded, monkeypatch):
    import threading
    import time as _time

    import src.pipeline as pipeline_module

    root, days = seeded
    reg = RunRegistry(root)
    req = _request(days)
    run_id = reg.submit(
        request_token="slow-1",
        run_label="slow",
        config=req.config.to_dict(),
        options={
            "refresh_cache": False,
            "start_date": days[0],
            "end_date": days[-1],
            "generate_plots": False,
            "log_level": "WARNING",
            "run_label": "slow",
        },
        source={},
    )
    real_fetcher = pipeline_module.DataFetcher

    class _SlowFetcher(real_fetcher):
        def fetch_and_cache(self, **kwargs):
            _time.sleep(3.0)
            return super().fetch_and_cache(**kwargs)

    monkeypatch.setattr(pipeline_module, "DataFetcher", _SlowFetcher)
    from src.services.worker import run_worker

    reg.claim_next_queued("test-owner")
    thread = threading.Thread(target=run_worker, args=(root, run_id))
    t0 = _time.time()
    thread.start()
    try:
        _wait_until(lambda: reg.get(run_id)["status"] == "running", desc="pickup")
        for _ in range(5):  # status stays responsive while fetch is blocked
            start = _time.time()
            reg.get(run_id)
            assert _time.time() - start < 0.5
        reg.request_cancel(run_id)  # cancel lands during the blocked fetch
        thread.join(timeout=60)
        assert not thread.is_alive()
        assert reg.get(run_id)["status"] == "cancelled"
        assert _time.time() - t0 < 30
    finally:
        thread.join(timeout=60)
