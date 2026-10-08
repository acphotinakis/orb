"""
src.services.registry
=====================
Durable run registry for single-worker execution (P3-O2).

SQLite-backed state machine shared by the run service (UI/CLI side) and the
worker process.  Every mutation is a transaction with compare-and-swap
semantics, so cancel/completion races commit exactly one terminal outcome
(P3-T06) and browser reloads never lose jobs (P3-T04).

States and legal transitions
----------------------------
queued -> running      (supervisor claim; only when no worker is active)
queued -> cancelled    (cancel while queued; the worker never launches)
running -> cancelling  (cancel request; worker acknowledges at a boundary)
running -> succeeded   (worker finished + published; manifest is the proof)
running -> failed      (worker error, or supervisor reconcile of a dead worker)
cancelling -> cancelled (worker acknowledged; nothing published)
cancelling -> failed    (worker errored while cancelling)
cancelling -> succeeded is NOT a transition: a worker that already published
  but lost the race finishes ``cancelled`` instead (artifacts exist and stay
  listed by the explorer; the registry status stays honest about the request).

Secrets (API keys, tokens, passwords) are redacted before persistence.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

REGISTRY_DIRNAME = "runs"
REGISTRY_FILENAME = "registry.sqlite"

STATUSES = (
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "succeeded",
    "failed",
)
TERMINAL = ("cancelled", "succeeded", "failed")

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  request_token TEXT UNIQUE NOT NULL,
  run_label TEXT,
  status TEXT NOT NULL,
  config_json TEXT NOT NULL,
  options_json TEXT NOT NULL,
  source_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  lease_owner TEXT,
  lease_expires REAL,
  worker_pid INTEGER,
  last_heartbeat REAL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  seq INTEGER NOT NULL,
  event_json TEXT NOT NULL,
  UNIQUE (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events (run_id, seq);
"""

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|alpaca[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9-]{8,}\b"),
)


def redact_secrets(text: str) -> str:
    """Replace credential-like content with ``***`` (never raises)."""
    try:
        redacted = str(text)
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("***", redacted)
        return redacted
    except Exception:
        return "***"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


class RunRegistry:
    """Durable registry; open short-lived connections per operation."""

    def __init__(self, storage_root: Path | str) -> None:
        self.storage_root = Path(storage_root)
        self.path = self.storage_root / REGISTRY_DIRNAME / REGISTRY_FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as conn:
            conn.executescript(_SCHEMA)

    # -- helpers ------------------------------------------------------

    def _row(self, run_id: str) -> dict[str, Any]:
        with _connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run '{run_id}'.")
        return dict(row)

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    # -- submission (idempotent per request token) --------------------

    def submit(
        self,
        *,
        request_token: str,
        run_label: str | None,
        config: Mapping[str, Any],
        options: Mapping[str, Any],
        source: Mapping[str, Any],
        run_id: str | None = None,
    ) -> str:
        """Insert a queued run; retried submissions return the same run_id."""
        if not isinstance(request_token, str) or not request_token.strip():
            raise ValueError("request_token must be a non-empty string.")
        if len(request_token) > 128:
            raise ValueError("request_token must be at most 128 characters.")
        candidate = run_id or uuid4().hex
        now = time.time()
        payload = (
            candidate,
            request_token,
            run_label,
            "queued",
            json.dumps(dict(config), sort_keys=True, default=str),
            json.dumps(dict(options), sort_keys=True, default=str),
            json.dumps(dict(source), sort_keys=True, default=str),
            now,
            now,
        )
        with _connect(self.path) as conn:
            try:
                conn.execute(
                    "INSERT INTO runs (run_id, request_token, run_label, status,"
                    " config_json, options_json, source_json, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    payload,
                )
                return candidate
            except sqlite3.IntegrityError:
                pass
            row = conn.execute(
                "SELECT run_id FROM runs WHERE request_token = ?", (request_token,)
            ).fetchone()
            if row is None:
                raise RuntimeError("Idempotent submit lost its own race; retry.")
            return row[0]

    def get(self, run_id: str) -> dict[str, Any]:
        """Full run record (raises KeyError when unknown)."""
        return self._row(run_id)

    def get_by_token(self, request_token: str) -> dict[str, Any]:
        with _connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM runs WHERE request_token = ?", (request_token,)
            ).fetchone()
        if row is None:
            raise KeyError("Unknown request token.")
        return dict(row)

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- single-worker dispatch ---------------------------------------

    def active_run(self) -> dict[str, Any] | None:
        """The running/cancelling run holding the worker slot, if any."""
        with _connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM runs WHERE status IN ('running', 'cancelling')"
                " ORDER BY created_at LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def claim_next_queued(self, owner: str, lease_secs: float = 300.0) -> str | None:
        """Atomically promote the oldest queued run iff the slot is free."""
        now = time.time()
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                active = conn.execute(
                    "SELECT run_id FROM runs"
                    " WHERE status IN ('running', 'cancelling') LIMIT 1"
                ).fetchone()
                if active is not None:
                    conn.execute("ROLLBACK")
                    return None
                nxt = conn.execute(
                    "SELECT run_id FROM runs WHERE status = 'queued'"
                    " ORDER BY created_at LIMIT 1"
                ).fetchone()
                if nxt is None:
                    conn.execute("ROLLBACK")
                    return None
                run_id = nxt[0]
                cur = conn.execute(
                    "UPDATE runs SET status = 'running', lease_owner = ?,"
                    " lease_expires = ?, worker_pid = NULL, last_heartbeat = ?,"
                    " updated_at = ? WHERE run_id = ? AND status = 'queued'",
                    (owner, now + lease_secs, now, now, run_id),
                )
                if cur.rowcount != 1:
                    conn.execute("ROLLBACK")
                    return None
                conn.execute("COMMIT")
                return run_id
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    def adopt_worker(self, run_id: str, owner: str, pid: int) -> bool:
        """Record the spawned worker pid on a run this owner just claimed."""
        with _connect(self.path) as conn:
            cur = conn.execute(
                "UPDATE runs SET worker_pid = ?, last_heartbeat = ?, updated_at = ?"
                " WHERE run_id = ? AND status = 'running' AND lease_owner = ?",
                (pid, time.time(), time.time(), run_id, owner),
            )
            return cur.rowcount == 1

    def release_to_queued(self, run_id: str, owner: str) -> bool:
        """Return a claimed-but-never-started run to the queue (spawn failure)."""
        with _connect(self.path) as conn:
            cur = conn.execute(
                "UPDATE runs SET status = 'queued', lease_owner = NULL,"
                " worker_pid = NULL, updated_at = ?"
                " WHERE run_id = ? AND status = 'running' AND lease_owner = ?",
                (time.time(), run_id, owner),
            )
            return cur.rowcount == 1

    def heartbeat(self, run_id: str, owner: str) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                "UPDATE runs SET last_heartbeat = ?, updated_at = ?"
                " WHERE run_id = ? AND lease_owner = ?",
                (time.time(), time.time(), run_id, owner),
            )

    # -- events --------------------------------------------------------

    def append_event(self, run_id: str, event: Mapping[str, Any]) -> int:
        """Persist one event with the next monotonic per-run sequence number."""
        cleaned = dict(event)
        for key in ("error", "message"):
            if isinstance(cleaned.get(key), str):
                cleaned[key] = redact_secrets(cleaned[key])
        try:
            payload = json.dumps(cleaned, sort_keys=True, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Event is not JSON-serializable: {exc}.")
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise KeyError(f"Unknown run '{run_id}'.")
                if row[0] not in ("running", "cancelling"):
                    conn.execute("ROLLBACK")
                    raise ValueError(f"Cannot append events to a '{row[0]}' run.")
                nxt = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO events (run_id, seq, event_json) VALUES (?, ?, ?)",
                    (run_id, nxt, payload),
                )
                conn.execute(
                    "UPDATE runs SET last_heartbeat = ?, updated_at = ?"
                    " WHERE run_id = ?",
                    (time.time(), time.time(), run_id),
                )
                conn.execute("COMMIT")
                return int(nxt)
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    def get_events(
        self, run_id: str, after_seq: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Events after *after_seq* (cursor reads, deduplicated by sequence)."""
        self._row(run_id)  # KeyError when unknown
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT seq, event_json FROM events WHERE run_id = ? AND seq > ?"
                " ORDER BY seq LIMIT ?",
                (run_id, after_seq, limit),
            ).fetchall()
        out = []
        for seq, payload in rows:
            try:
                event = json.loads(payload)
            except ValueError:
                continue  # bounded corruption never crashes readers
            event.setdefault("seq", seq)
            out.append(event)
        return out

    # -- cancellation + terminal outcomes (exactly one wins) -----------

    def request_cancel(self, run_id: str) -> str:
        """queued->cancelled | running->cancelling | terminal/no-op passthrough."""
        now = time.time()
        with _connect(self.path) as conn:
            cur = conn.execute(
                "UPDATE runs SET status = 'cancelled', updated_at = ?"
                " WHERE run_id = ? AND status = 'queued'",
                (now, run_id),
            )
            if cur.rowcount == 1:
                return "cancelled"
            cur = conn.execute(
                "UPDATE runs SET status = 'cancelling', updated_at = ?"
                " WHERE run_id = ? AND status = 'running'",
                (now, run_id),
            )
            if cur.rowcount == 1:
                return "cancelling"
        return self._row(run_id)["status"]

    def finish(self, run_id: str, outcome: str, error: str | None = None) -> bool:
        """Commit one terminal outcome; False when another terminal won first."""
        allowed = {
            "succeeded": ("running",),
            "failed": ("running", "cancelling"),
            "cancelled": ("running", "cancelling"),
        }
        if outcome not in allowed:
            raise ValueError(f"Unknown outcome '{outcome}'.")
        redacted = redact_secrets(error) if error else None
        now = time.time()
        with _connect(self.path) as conn:
            cur = conn.execute(
                "UPDATE runs SET status = ?, error = ?, updated_at = ?"
                " WHERE run_id = ? AND status IN ({})".format(
                    ",".join("?" * len(allowed[outcome]))
                ),
                (outcome, redacted, now, run_id, *allowed[outcome]),
            )
            return cur.rowcount == 1

    # -- crash recovery --------------------------------------------------

    @staticmethod
    def _reap_children() -> None:
        """Reap finished child workers so SIGKILLed ones stop looking alive.

        An unreaped zombie still answers ``kill(pid, 0)``; without this sweep
        a dead worker would never reconcile.
        """
        try:
            while True:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
        except (ChildProcessError, OSError):
            pass

    def reconcile(self, heartbeat_timeout_secs: float = 120.0) -> list[str]:
        """Mark runs with dead workers failed; never silently rerun anything.

        A run is eligible only when its heartbeat is stale AND its recorded
        pid is gone.  Returns reconciled run IDs.
        """
        self._reap_children()
        now = time.time()
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT run_id, worker_pid, last_heartbeat FROM runs"
                " WHERE status IN ('running', 'cancelling')"
            ).fetchall()
        reconciled = []
        for run_id, pid, heartbeat in rows:
            if heartbeat is not None and now - heartbeat <= heartbeat_timeout_secs:
                continue
            if self._pid_alive(pid):
                continue
            with _connect(self.path) as conn:
                cur = conn.execute(
                    "UPDATE runs SET status = 'failed',"
                    " error = 'Worker process lost; "
                    "no terminal outcome was committed.',"
                    " updated_at = ? WHERE run_id = ?"
                    " AND status IN ('running', 'cancelling')",
                    (now, run_id),
                )
                if cur.rowcount == 1:
                    reconciled.append(run_id)
        return reconciled
