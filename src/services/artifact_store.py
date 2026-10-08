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
import math
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from src.common.exceptions import ConfigurationError

#: Bump when the processed transformation changes so old datasets invalidate.
PROCESSING_VERSION = "1"

#: Schema version stamped into every manifest.
MANIFEST_SCHEMA_VERSION = "1.0"

#: User-derived single-path components (run IDs, symbols-as-dirs).
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_RESERVED_COMPONENTS = {".", ".."}

_locks: dict[str, threading.Lock] = {}
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
        if pd.api.types.is_datetime64_any_dtype(series):
            vals = series.dt.tz_convert("UTC").astype("int64").to_numpy()
            digest.update(vals.tobytes())
            continue
        vals = series.to_numpy()
        if vals.dtype == object:
            # Object arrays hash pointers with tobytes(); encode content
            # deterministically instead (length-prefixed UTF-8, null sentinel).
            for value in vals:
                if (
                    value is None
                    or value is pd.NA
                    or (isinstance(value, float) and math.isnan(value))
                ):
                    digest.update(b"\x00null\x00")
                else:
                    encoded = str(value).encode("utf-8")
                    digest.update(len(encoded).to_bytes(8, "big") + encoded)
        else:
            try:
                digest.update(vals.tobytes())
            except (AttributeError, TypeError, ValueError):
                digest.update(str(series.tolist()).encode("utf-8"))

    return digest.hexdigest()


def source_code_fingerprint(repo_root: Path | None = None) -> dict[str, Any]:
    """Best-effort source revision record; never raises.

    Returns ``{"available": False, ...}`` when git is missing or fails, so
    offline/test environments keep working while provenance stays explicit.
    """
    root = (
        Path(repo_root)
        if repo_root is not None
        else Path(__file__).resolve().parents[2]
    )
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if revision.returncode != 0:
            raise RuntimeError("git rev-parse failed")
        return {
            "available": True,
            "revision": revision.stdout.strip(),
            "dirty": bool(status.stdout.strip()),
        }
    except (OSError, subprocess.SubprocessError) as exc:
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
) -> dict[str, Any]:
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
    if (
        not isinstance(value, str)
        or value in _RESERVED_COMPONENTS
        or not _SAFE_COMPONENT_RE.match(value)
    ):
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
    run_label: str | None,
    config_dict: dict[str, Any],
    request_dict: dict[str, Any],
    source_dict: dict[str, Any],
    datasets_dict: dict[str, Any],
    artifacts_dict: dict[str, Any],
    accounting_dict: dict[str, Any],
    status: str = "succeeded",
    created_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
        KeyError,
        TypeError,
        ConfigurationError,
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Read-only discovery + artifact readers (P2: browsing never computes)
# ---------------------------------------------------------------------------

#: Run states visible to the results explorer.
RUN_STATUS_COMPLETE = "complete"
RUN_STATUS_CORRUPT = "corrupt"  # manifest present but invalid
RUN_STATUS_LEGACY = "legacy"  # no manifest: predates provenance, unsupported


@dataclass(frozen=True)
class DiscoveredRun:
    """One experiment directory as seen by the read-only explorer."""

    run_id: str
    status: str
    experiment_dir: Path
    run_label: str | None = None
    completed_at: str | None = None
    detail: str = ""


def _manifest_status(manifest: Mapping[str, Any]) -> str:
    return str(manifest.get("status", "unknown"))


def discover_runs(storage_root: Path | str) -> list[DiscoveredRun]:
    """List experiment runs under ``<root>/experiments/`` without computing.

    * ``complete`` — valid, checksum-verified, succeeded manifest.
    * ``corrupt`` — manifest present but invalid (kept listed with the
      reason; never blocks healthy runs).
    * ``legacy`` — no manifest: unsupported for browsing (explicit validated
      import may arrive later; provenance is never guessed).

    Never raises for a single bad directory; the reason lands in ``detail``.
    """
    from pathlib import Path as _Path

    root = _Path(storage_root)
    exp_root = root / "experiments"
    if not exp_root.is_dir():
        return []
    found: list[DiscoveredRun] = []
    for child in sorted(exp_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        # run_id is the leading component of "<run_id>__<symbol>_<tf>_<slug>".
        short_id = child.name.split("__")[0]
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            found.append(
                DiscoveredRun(
                    run_id=short_id,
                    status=RUN_STATUS_LEGACY,
                    experiment_dir=child,
                    detail=(
                        "No manifest.json: predates run provenance; "
                        "unsupported for browsing."
                    ),
                )
            )
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            validate_manifest(manifest, child)
            found.append(
                DiscoveredRun(
                    run_id=str(manifest.get("run_id", short_id)),
                    status=RUN_STATUS_COMPLETE,
                    experiment_dir=child,
                    run_label=manifest.get("run_label"),
                    completed_at=manifest.get("completed_at"),
                )
            )
        except ConfigurationError as exc:
            found.append(
                DiscoveredRun(
                    run_id=short_id,
                    status=RUN_STATUS_CORRUPT,
                    experiment_dir=child,
                    detail=str(exc),
                )
            )
        except Exception as exc:
            found.append(
                DiscoveredRun(
                    run_id=short_id,
                    status=RUN_STATUS_CORRUPT,
                    experiment_dir=child,
                    detail=f"Unreadable manifest: {exc}",
                )
            )
    return found


@dataclass(frozen=True)
class RunArtifacts:
    """Validated, in-memory contents of one completed run (read-only)."""

    manifest: Mapping[str, Any]
    metrics: Mapping[str, Any]
    trades: pd.DataFrame
    equity: pd.DataFrame
    daily: pd.DataFrame
    config_snapshot: Mapping[str, Any]


def _read_csv_checked(experiment_dir: Path, rel: str) -> pd.DataFrame:
    target = ensure_within_root(experiment_dir, experiment_dir / rel)
    if not target.is_file():
        raise ConfigurationError(f"Artifact '{rel}' is missing.", field="artifacts")
    try:
        return pd.read_csv(target)
    except Exception as exc:
        raise ConfigurationError(
            f"Artifact '{rel}' is unreadable: {exc}.", field="artifacts"
        )


def read_run_artifacts(storage_root: Path | str, run_id: str) -> RunArtifacts:
    """Read and validate every artifact of one completed run (read-only).

    Args:
        storage_root: Storage root containing ``experiments/``.
        run_id: Run identifier (validated as a safe path component).

    Raises:
        ConfigurationError: Unknown run, path escape, incomplete/corrupt run,
            or unreadable artifact.  Never synthesizes fallback values and
            never launches computation.
    """
    from pathlib import Path as _Path

    ensure_safe_component(run_id, field_name="run_id")
    root = _Path(storage_root)
    # run_id is the leading component of the experiment directory name
    # ("<run_id>__<symbol>_<tf>_<slug>"); match it exactly, never by substring.
    exp_root = root / "experiments"
    try:
        children = list(exp_root.iterdir())
    except OSError:
        children = []
    candidates = [
        c
        for c in children
        if c.is_dir() and (c.name == run_id or c.name.startswith(run_id + "__"))
    ]
    if not candidates:
        raise ConfigurationError(f"Unknown run '{run_id}'.", field="run_id")
    if len(candidates) > 1:
        raise ConfigurationError(
            f"Ambiguous run '{run_id}': {len(candidates)} matches.",
            field="run_id",
        )
    experiment_dir = ensure_within_root(root, candidates[0])

    manifest_path = experiment_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(
            f"Run '{run_id}' has no readable manifest: {exc}.", field="manifest"
        )
    validate_manifest(manifest, experiment_dir)

    metrics_rel = "results/metrics.json"
    metrics_target = ensure_within_root(experiment_dir, experiment_dir / metrics_rel)
    try:
        metrics = json.loads(metrics_target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigurationError(
            f"Artifact '{metrics_rel}' is unreadable: {exc}.", field="artifacts"
        )

    config_rel = "config_snapshot.yaml"
    config_target = ensure_within_root(experiment_dir, experiment_dir / config_rel)
    try:
        import yaml as _yaml

        config_snapshot = (
            _yaml.safe_load(config_target.read_text(encoding="utf-8")) or {}
        )
    except Exception as exc:
        raise ConfigurationError(
            f"Artifact '{config_rel}' is unreadable: {exc}.", field="artifacts"
        )

    return RunArtifacts(
        manifest=manifest,
        metrics=metrics,
        trades=_read_csv_checked(experiment_dir, "results/trades.csv"),
        equity=_read_csv_checked(experiment_dir, "results/equity_curve.csv"),
        daily=_read_csv_checked(experiment_dir, "results/daily_summary.csv"),
        config_snapshot=config_snapshot,
    )


def read_session_bars(
    storage_root: Path | str, manifest: Mapping[str, Any], session_id: str
) -> pd.DataFrame:
    """Load one session's processed OHLCV bars via the manifest dataset ref.

    Read-only: serves the immutable dataset the run recorded, requesting only
    the selected session.  Raises :class:`ConfigurationError` when the
    reference is missing, escapes storage, or lacks OHLC columns.
    """
    from pathlib import Path as _Path

    try:
        rel = manifest["datasets"]["processed"]["path"]
    except KeyError:
        raise ConfigurationError(
            "Manifest has no processed dataset reference.", field="datasets"
        )
    root = _Path(storage_root)
    target = ensure_within_root(root, root / rel)
    if not target.is_file():
        raise ConfigurationError(
            f"Processed dataset '{rel}' is unavailable.", field="datasets"
        )
    try:
        df = pd.read_parquet(target, engine="pyarrow")
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise ConfigurationError(
            f"Processed dataset '{rel}' is unreadable: {exc}.", field="datasets"
        ) from exc

    required = {"session_id", "timestamp", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ConfigurationError(
            f"Processed dataset lacks columns: {sorted(missing)}.", field="datasets"
        )
    session = df.loc[df["session_id"] == session_id].reset_index(drop=True)
    if session.empty:
        raise ConfigurationError(
            f"Session '{session_id}' not found in the recorded dataset.",
            field="session_id",
        )
    return session


def read_trace(
    storage_root: Path | str, run_id: str
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Read and version-check a run's decision trace (P4).

    Returns ``(header, events)``.  Raises :class:`ConfigurationError` when
    the run has no trace (tracing is opt-in), and :class:`ValueError` for
    unknown schema versions — replay never invents missing decisions.
    """
    data, _ = read_download_bytes(storage_root, run_id, "results/decision_trace.jsonl")
    from src.backtest.trace import TraceCollector

    try:
        return TraceCollector.parse_jsonl(data.decode("utf-8"))  # type: ignore
    except ValueError:
        raise
    except Exception as exc:
        raise ConfigurationError(
            f"Decision trace for '{run_id}' is unreadable: {exc}.",
            field="artifacts",
        )


#: Artifacts the explorer may serve for download (relative paths, fixed set).
DOWNLOAD_ALLOWLIST = (
    "results/trades.csv",
    "results/equity_curve.csv",
    "results/daily_summary.csv",
    "results/metrics.json",
    "results/decision_trace.jsonl",
    "results/source_snapshot.json",
    "config_snapshot.yaml",
)


def read_download_bytes(
    storage_root: Path | str, run_id: str, relative_path: str
) -> tuple[bytes, str]:
    """Return ``(bytes, filename)`` for one allowlisted artifact, unchanged."""
    from pathlib import Path as _Path

    if relative_path not in DOWNLOAD_ALLOWLIST:
        raise ConfigurationError(
            f"Download '{relative_path}' is not in the allowlist.",
            field="download",
        )
    ensure_safe_component(run_id, field_name="run_id")
    root = _Path(storage_root)
    try:
        children = list((root / "experiments").iterdir())
    except OSError:
        children = []
    matches = [
        c
        for c in children
        if c.is_dir() and (c.name == run_id or c.name.startswith(run_id + "__"))
    ]
    if len(matches) != 1:
        raise ConfigurationError(f"Unknown run '{run_id}'.", field="run_id")
    target = ensure_within_root(root, matches[0] / relative_path)
    return target.read_bytes(), _Path(relative_path).name


# ---------------------------------------------------------------------------
# Presentation helpers (units, timezones, unavailable-vs-zero)
# ---------------------------------------------------------------------------

EASTERN_TZ_NAME = "America/New_York"


def to_et_display(value: Any) -> str:
    """Format a timestamp in ET with an unambiguous numeric offset label."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    et = ts.tz_convert(EASTERN_TZ_NAME)
    return et.strftime("%Y-%m-%d %H:%M:%S") + " ET" + et.strftime("%z")


def display_value(value: Any, *, suffix: str = "") -> str:
    """Render a metric distinctly: ``"inf"`` → ∞, missing/NaN → n/a, else value.

    Unavailable metrics are never rendered as zero or a misleading percentage.
    """
    if value is None:
        return "n/a"
    if isinstance(value, str):
        if value.strip().lower() == "inf":
            return "∞" + suffix
        return value
    try:
        if isinstance(value, float) and math.isnan(value):
            return "n/a"
    except TypeError:
        return str(value)
    return f"{value}{suffix}"


def is_synthetic_run(manifest: Mapping[str, Any]) -> bool:
    """True when the run label marks synthetic demonstration data."""
    label = str(manifest.get("run_label") or "")
    return "synthetic" in label.lower()
