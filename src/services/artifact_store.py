"""
src.services.artifact_store
===========================
Cache identity, atomic publication, and immutable run manifests (P1-O3).

Contracts
---------
* **Dataset identity** — a processed dataset is keyed by every input that
  affects its content: source-data fingerprint, feed, symbol, timeframe,
  covered date bounds, processing version, opening-range minutes, and
  force-exit time.  Different inputs land in different directories, so an
  incompatible cache hit is impossible by construction.
* **Atomic publication** — every file is written to a temporary sibling and
  moved with :func:`os.replace`, so readers observe a valid old or new file,
  never partial bytes.  The validated ``manifest.json`` is written **last**;
  its presence with ``status == "succeeded"`` is the completion marker.  No
  multi-file transaction is claimed.
* **Per-key synchronization** — an in-process lock registry serializes writers
  sharing one cache key (threads).  Cross-process safety rests on atomic
  replacement; the single-worker design (P3) means no concurrent processes
  contend for a key.
* **Path safety** — user-derived components (run IDs, symbols) are validated
  against an allowlist pattern and resolved against the storage root with
  symlink resolution, so escapes raise instead of writing outside storage.

U1 note: manifests record the engine accounting reconciliation
(``final_capital - initial_capital - sum(net pnl)``) as evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from src.common.exceptions import ConfigurationError

#: Bump when the processed transformation changes so old datasets invalidate.
PROCESSING_VERSION = "1"

#: Schema version stamped into every manifest.
MANIFEST_SCHEMA_VERSION = "1.0"

#: User-derived single-path components (run IDs, symbols-as-dirs).
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_RESERVED_COMPONENTS = {".", ".."}

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def cache_key_lock(key: str) -> threading.Lock:
    """Return the process-wide lock serializing writers for one cache key."""
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def fingerprint_dataframe(df: pd.DataFrame) -> str:
    """Stable sha256 over canonical frame content (columns, dtypes, values).

    Timestamps are normalized to UTC int64 so timezone representation cannot
    change the fingerprint.  Any cell change yields a different digest.
    """
    canonical = df.copy()
    cols = list(canonical.columns)
    parts = ["cols:" + ",".join(f"{c}:{canonical[c].dtype}" for c in cols)]
    parts.append(f"rows:{len(canonical)}")
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
    for col in cols:
        series = canonical[col]
        try:
            if pd.api.types.is_datetime64_any_dtype(series):
                vals = series.dt.tz_convert("UTC").astype("int64").to_numpy()
            else:
                vals = series.to_numpy()
            digest.update(vals.tobytes())
        except Exception:
            digest.update(str(series.tolist()).encode("utf-8"))
    return digest.hexdigest()


def source_code_fingerprint(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Best-effort source revision record; never raises.

    Returns ``{"available": False, ...}`` when git is missing or fails, so
    offline/test environments keep working while provenance stays explicit.
    """
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if revision.returncode != 0:
            raise RuntimeError("git rev-parse failed")
        return {
            "available": True,
            "revision": revision.stdout.strip(),
            "dirty": bool(status.stdout.strip()),
        }
    except Exception as exc:
        return {"available": False, "revision": None, "dirty": None, "error": str(exc)}


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Dataset identity
# ---------------------------------------------------------------------------


def dataset_identity(
    source_fingerprint: str,
    *,
    feed: str,
    symbol: str,
    timeframe: str,
    date_min: str,
    date_max: str,
    or_minutes: int,
    force_exit_time: str,
    processing_version: str = PROCESSING_VERSION,
) -> Dict[str, Any]:
    """Build the full identity mapping for a processed dataset."""
    return {
        "processing_version": processing_version,
        "source_fingerprint": source_fingerprint,
        "feed": feed,
        "symbol": symbol,
        "timeframe": timeframe,
        "date_min": date_min,
        "date_max": date_max,
        "opening_range_minutes": or_minutes,
        "force_exit_time": force_exit_time,
    }


def identity_short_hash(identity: Mapping[str, Any]) -> str:
    """16-hex-char digest uniquely naming the identity directory."""
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def ensure_safe_component(value: str, *, field_name: str) -> str:
    """Validate a user-derived single path component (no traversal)."""
    if not isinstance(value, str) or value in _RESERVED_COMPONENTS or not _SAFE_COMPONENT_RE.match(value):
        raise ConfigurationError(
            f"{field_name} must match [A-Za-z0-9._-] (no path separators); "
            f"got {value!r}.",
            field=field_name,
        )
    return value


def ensure_within_root(root: Path, path: Path, *, field_name: str = "path") -> Path:
    """Resolve (following symlinks) and require containment in *root*."""
    root_real = Path(os.path.realpath(root))
    path_real = Path(os.path.realpath(path))
    try:
        path_real.relative_to(root_real)
    except ValueError:
        raise ConfigurationError(
            f"{field_name} escapes storage root: {path!r}.",
            field=field_name,
        )
    return path_real


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    """Write bytes atomically (temp sibling + os.replace).

    Callers validate user-derived path components before calling; the temp
    file lives in the destination directory so the rename is atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def atomic_write_text(path: Path, text: str) -> Path:
    """Write text atomically (UTF-8)."""
    return atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> Path:
    """Write a zstd Parquet file atomically."""
    import io

    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", compression="zstd", index=False)
    return atomic_write_bytes(path, buffer.getvalue())


# ---------------------------------------------------------------------------
# Manifests (published last)
# ---------------------------------------------------------------------------

_REQUIRED_MANIFEST_KEYS = (
    "manifest_version",
    "run_id",
    "status",
    "created_at",
    "completed_at",
    "config",
    "request",
    "source",
    "datasets",
    "artifacts",
)


def build_manifest(
    *,
    run_id: str,
    run_label: Optional[str],
    config_dict: Dict[str, Any],
    request_dict: Dict[str, Any],
    source_dict: Dict[str, Any],
    datasets_dict: Dict[str, Any],
    artifacts_dict: Dict[str, Any],
    accounting_dict: Dict[str, Any],
    status: str = "succeeded",
    created_at: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble (not yet publish) a run manifest."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "manifest_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "run_label": run_label,
        "status": status,
        "created_at": created_at or now,
        "completed_at": completed_at or now,
        "config": config_dict,
        "request": request_dict,
        "source": source_dict,
        "datasets": datasets_dict,
        "artifacts": artifacts_dict,
        "accounting": accounting_dict,
    }


def validate_manifest(
    manifest: Mapping[str, Any], experiment_dir: Path
) -> Dict[str, Any]:
    """Validate manifest structure and that every artifact checksums intact.

    Raises :class:`ConfigurationError` on any problem.  Returns a summary.
    """
    for key in _REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            raise ConfigurationError(
                f"Manifest is missing required key '{key}'.",
                field="manifest",
            )
    if manifest["status"] != "succeeded":
        raise ConfigurationError(
            f"Manifest status is '{manifest['status']}', not 'succeeded'.",
            field="manifest.status",
        )
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise ConfigurationError(
            "Manifest lists no artifacts.", field="manifest.artifacts"
        )
    checked = 0
    for name, entry in artifacts.items():
        rel = entry.get("relative_path")
        expected = entry.get("sha256")
        if not rel or not expected:
            raise ConfigurationError(
                f"Artifact '{name}' lacks path/checksum.", field="manifest.artifacts"
            )
        target = ensure_within_root(experiment_dir, experiment_dir / rel)
        if not target.is_file():
            raise ConfigurationError(
                f"Artifact '{name}' missing at '{rel}'.", field="manifest.artifacts"
            )
        actual = sha256_file(target)
        if actual != expected:
            raise ConfigurationError(
                f"Artifact '{name}' checksum mismatch.", field="manifest.artifacts"
            )
        checked += 1
    return {"artifacts_checked": checked, "status": "succeeded"}


def publish_manifest_last(experiment_dir: Path, manifest: Mapping[str, Any]) -> Path:
    """Validate then atomically publish ``manifest.json`` as the final step."""
    validate_manifest(manifest, experiment_dir)
    path = experiment_dir / "manifest.json"
    return atomic_write_text(
        path, json.dumps(manifest, indent=2, sort_keys=True, default=str)
    )


def is_run_complete(experiment_dir: Path) -> bool:
    """True only with a present, valid, succeeded manifest (no false positives)."""
    manifest_path = experiment_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest, experiment_dir)
    except Exception:
        return False
    return True
