"""
src.services.run_service
========================
Submission, status, cancellation, and single-worker supervision (P3-O1/O2).

Exactly one worker process is active across browser sessions and supervisor
restarts: :meth:`RunService.ensure_supervisor` reconciles dead workers, then
claims at most one queued run and spawns its worker with structured arguments
(never shell-built commands).  Submission is idempotent per request token —
retries return the existing run ID while a deliberate rerun mints a new token
and run ID.  Form edits never touch an active run: workers rebuild immutable
inputs from the registry at launch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.common.logger import get_logger
from src.services.artifact_store import source_code_fingerprint
from src.services.registry import RunRegistry
from src.services.run_models import RunRequest

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


class RunService:
    """UI/CLI-facing facade over the durable registry + worker supervision."""

    def __init__(
        self, storage_root: str | Path, heartbeat_timeout_secs: float = 120.0
    ) -> None:
        self.storage_root = Path(storage_root)
        self.registry = RunRegistry(self.storage_root)
        self.heartbeat_timeout_secs = heartbeat_timeout_secs
        self._owner = f"supervisor-{os.getpid()}"

    # -- submission ----------------------------------------------------

    def submit_run(
        self, request: RunRequest, request_token: Optional[str] = None
    ) -> str:
        """Validate (already done by the request), persist, enqueue, dispatch.

        Args:
            request: Validated :class:`RunRequest`.
            request_token: Idempotency key; a fresh one is minted when omitted.

        Returns:
            The run ID (existing one when the token was already submitted).
        """
        token = request_token or uuid4().hex
        run_id = self.registry.submit(
            request_token=token,
            run_label=request.run_label,
            config=request.config.to_dict(),
            options={
                "start_date": request.start_date,
                "end_date": request.end_date,
                "refresh_cache": request.refresh_cache,
                "generate_plots": request.generate_plots,
                "log_level": request.log_level,
                "run_label": request.run_label,
            },
            source=source_code_fingerprint(),
        )
        self.ensure_supervisor()
        return run_id

    # -- reads ----------------------------------------------------------

    def get_run(self, run_id: str) -> Dict[str, Any]:
        return self.registry.get(run_id)

    def list_runs(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.registry.list_runs(limit=limit)

    def get_events(
        self, run_id: str, after_seq: int = 0, limit: int = 500
    ) -> List[Dict[str, Any]]:
        return self.registry.get_events(run_id, after_seq=after_seq, limit=limit)

    def read_log_tail(self, run_id: str, max_bytes: int = 65536) -> str:
        """Bounded tail of the run's execution log (UI-safe size)."""
        matches = [
            c
            for c in (self.storage_root / "experiments").iterdir()
            if c.is_dir() and (c.name == run_id or c.name.startswith(run_id + "__"))
        ]
        if len(matches) != 1:
            return ""
        log_path = matches[0] / "logs" / "execution.log"
        if not log_path.is_file():
            return ""
        size = log_path.stat().st_size
        with open(log_path, "rb") as fh:
            if size > max_bytes:
                fh.seek(-max_bytes, os.SEEK_END)
            return fh.read().decode("utf-8", errors="replace")

    # -- cancellation ----------------------------------------------------

    def cancel_run(self, run_id: str) -> str:
        """queued->cancelled (never launches) | running->cancelling (acked at a
        boundary) | terminal states pass through unchanged."""
        status = self.registry.request_cancel(run_id)
        self.ensure_supervisor()
        return status

    # -- supervision ------------------------------------------------------

    def ensure_supervisor(self) -> Optional[str]:
        """Reconcile dead workers, then dispatch at most one queued run.

        Returns the dispatched run ID, or ``None`` when the slot is busy or
        the queue is empty.  Safe to call on every UI interaction.
        """
        reconciled = self.registry.reconcile(self.heartbeat_timeout_secs)
        for run_id in reconciled:
            logger.warning("Reconciled dead worker for run '%s' as failed.", run_id)
        claimed = self.registry.claim_next_queued(self._owner)
        if claimed is None:
            return None
        log_path = self.storage_root / "runs" / f"worker-{claimed}.log"
        log_handle = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.services.worker",
                 str(self.storage_root), claimed],
                cwd=str(_REPO_ROOT),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as exc:
            log_handle.close()
            logger.error("Worker spawn failed for '%s' (%s); re-queued.", claimed, exc)
            self.registry.release_to_queued(claimed, self._owner)
            return None
        finally:
            # The descriptor is duplicated into the child; close ours.
            log_handle.close()
        adopted = self.registry.adopt_worker(claimed, self._owner, proc.pid)
        if not adopted:
            logger.error("Run '%s' left the slot before adopt; terminating worker.", claimed)
            proc.terminate()
            return None
        logger.info("Dispatched run '%s' to worker pid %d.", claimed, proc.pid)
        return claimed

    def reconcile(self) -> List[str]:
        """Public crash-recovery entry (no dispatch)."""
        return self.registry.reconcile(self.heartbeat_timeout_secs)


def dispatch_next(storage_root: str | Path, owner: str = "worker-handoff") -> Optional[str]:
    """Claim and spawn at most one queued run (called by workers on exit and
    by the service after submit/cancel).  Best-effort: never raises."""
    try:
        registry = RunRegistry(storage_root)
        registry.reconcile()
        claimed = registry.claim_next_queued(owner)
        if claimed is None:
            return None
        log_path = Path(storage_root) / "runs" / f"worker-{claimed}.log"
        log_handle = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.services.worker",
                 str(storage_root), claimed],
                cwd=str(_REPO_ROOT),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            log_handle.close()
            registry.release_to_queued(claimed, owner)
            return None
        finally:
            log_handle.close()
        if not registry.adopt_worker(claimed, owner, proc.pid):
            proc.terminate()
            return None
        return claimed
    except Exception as exc:  # noqa: BLE001 — dispatch must never break completion
        logger.warning("Dispatch handoff failed (%s).", exc)
        return None


def serialize_run(row: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe projection of a registry row (secrets never stored anyway)."""
    return {
        "run_id": row["run_id"],
        "run_label": row.get("run_label"),
        "status": row["status"],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "error": row.get("error"),
    }
