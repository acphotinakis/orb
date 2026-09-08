"""
tests/services/test_run_manifests.py
=====================================
P1-T07/T08/T13 (manifest level): publication ordering, tamper detection,
path safety, fingerprint sensitivity, and source provenance.
"""

import json

import pandas as pd
import pytest

from src.common.exceptions import ConfigurationError
from src.services.artifact_store import (
    atomic_write_text,
    build_manifest,
    ensure_safe_component,
    ensure_within_root,
    fingerprint_dataframe,
    is_run_complete,
    publish_manifest_last,
    sha256_file,
    source_code_fingerprint,
    validate_manifest,
)


def _manifest_payload(tmp_path, tamper=None):
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)
    trades = tmp_path / "results" / "trades.csv"
    trades.write_text("a,b\n1,2\n", encoding="utf-8")
    if tamper == "modify":
        pass  # modified after publish by the caller
    manifest = build_manifest(
        run_id="abc123",
        run_label="lbl",
        config_dict={"strategy": {"ticker": "SPY"}},
        request_dict={"start_date": "2024-01-02"},
        source_dict={"available": False},
        datasets_dict={},
        artifacts_dict={
            "results/trades.csv": {
                "relative_path": "results/trades.csv",
                "sha256": sha256_file(trades),
                "bytes": trades.stat().st_size,
            }
        },
        accounting_dict={},
    )
    return manifest


def test_incomplete_run_without_manifest_is_not_complete(tmp_path):
    """T07: artifacts without a manifest never read as complete."""
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)
    (tmp_path / "results" / "trades.csv").write_text("a\n1\n", encoding="utf-8")
    assert is_run_complete(tmp_path) is False


def test_corrupt_manifest_is_not_complete(tmp_path):
    """T07: malformed manifest tails do not crash; run reads incomplete."""
    (tmp_path / "manifest.json").write_text('{"manifest_version":', encoding="utf-8")
    assert is_run_complete(tmp_path) is False


def test_tampered_artifact_fails_validation(tmp_path):
    """T07: post-publication modification is detected, never silently served."""
    manifest = _manifest_payload(tmp_path)
    publish_manifest_last(tmp_path, manifest)
    assert is_run_complete(tmp_path) is True
    with open(tmp_path / "results" / "trades.csv", "a", encoding="utf-8") as fh:
        fh.write("3,4\n")
    assert is_run_complete(tmp_path) is False
    with pytest.raises(ConfigurationError):
        validate_manifest(
            json.loads((tmp_path / "manifest.json").read_text()), tmp_path
        )


def test_manifest_published_last_and_validated(tmp_path):
    """Artifacts written but manifest publish of a bad payload refuses."""
    manifest = _manifest_payload(tmp_path)
    manifest["artifacts"]["results/missing.csv"] = {
        "relative_path": "results/missing.csv",
        "sha256": "0" * 64,
        "bytes": 0,
    }
    with pytest.raises(ConfigurationError):
        publish_manifest_last(tmp_path, manifest)
    assert not (tmp_path / "manifest.json").exists()
    assert is_run_complete(tmp_path) is False


def test_run_id_traversal_rejected():
    """T08: unsafe run IDs never reach the filesystem."""
    for bad in ("../evil", "a/b", "/abs", "..", "", "has space", "semi;colon"):
        with pytest.raises(ConfigurationError):
            ensure_safe_component(bad, field_name="run_id")
    ensure_safe_component("cli_offline_run", field_name="run_id")


def test_symlink_escape_rejected(tmp_path):
    """T08: symlink-resolved paths outside storage raise (incl. traversal)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "root" / "link"
    link.parent.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfigurationError):
        ensure_within_root(tmp_path / "root", link / "x.parquet")
    ok = ensure_within_root(tmp_path / "root", tmp_path / "root" / "x.parquet")
    assert ok == tmp_path / "root" / "x.parquet"


def test_fingerprint_sensitive_to_single_cell(synthetic_rth_bars):
    """T13: any content change yields a different source fingerprint."""
    before = fingerprint_dataframe(synthetic_rth_bars)
    revised = synthetic_rth_bars.copy()
    revised.loc[100, "close"] = 999.0
    assert fingerprint_dataframe(revised) != before
    assert fingerprint_dataframe(synthetic_rth_bars) == before


def test_source_provenance_record_shape():
    """Manifest source block is explicit about availability (never implicit)."""
    record = source_code_fingerprint()
    assert set(record) >= {"available", "revision", "dirty"}
    if record["available"]:
        assert isinstance(record["revision"], str) and len(record["revision"]) == 40
        assert isinstance(record["dirty"], bool)


def test_atomic_write_never_leaves_partial(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "v1")
    assert target.read_text() == "v1"
    assert not list(tmp_path.glob(".tmp-*"))
