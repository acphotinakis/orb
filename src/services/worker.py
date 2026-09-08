"""
src.services.worker
===================
Single-run worker process entry point (P3-O2).

Runs ONE registry run to a terminal outcome in an isolated interpreter with
its own logging, then exits.  Invoked as::

    python -m src.services.worker <storage_root> <run_id>

 never by shell-built commands.  The worker:

 #. rebuilds its config/options from the durable registry (no YAML, no UI),
 #. streams stage/session progress into the registry event journal,
 #. polls the registry for cancellation between stages/sessions/bars,
 #. publishes artifacts only on the uninterrupted success path
    (:mod:`src.pipeline` writes the manifest last),
 #. commits exactly one terminal outcome (``succeeded`` / ``failed`` /
    ``cancelled``); a lost race keeps the earlier terminal (P3-T06).

A killed worker commits nothing; the supervisor's reconcile marks the run
``failed`` without launching a second copy of a live worker.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any, Dict

from src.common.exceptions import CancelledRun, ORBBaseException
from src.common.logger import get_logger
from src.pipeline import ORBPipeline
from src.services.registry import RunRegistry
from src.services.run_models import app_config_from_dict

logger = get_logger(__name__)


def run_worker(storage_root: str | Path, run_id: str) -> int:
    """Execute one run to a terminal outcome.  Returns a process exit code."""
    root = Path(storage_root)
    registry = RunRegistry(root)
    try:
        row = registry.get(run_id)
    except KeyError:
        logger.error("Unknown run '%s'; nothing to do.", run_id)
        return 2
    if row["status"] not in ("running", "cancelling"):
        logger.error(
            "Run '%s' is '%s', not dispatchable; refusing to rerun.",
            run_id,
            row["status"],
        )
        return 2

    import json as _json

    config = app_config_from_dict(_json.loads(row["config_json"]))
    options: Dict[str, Any] = _json.loads(row["options_json"])

    def _on_event(event: Dict[str, Any]) -> None:
        try:
            registry.append_event(run_id, event)
        except (KeyError, ValueError) as exc:
            logger.warning("Event journal write skipped (%s).", exc)

    def _cancel_requested() -> bool:
        try:
            return registry.get(run_id)["status"] in ("cancelling", "cancelled")
        except KeyError:
            return True  # registry gone: stop promptly

    try:
        ORBPipeline(config=config, base_dir=root).run(
            start_date=options.get("start_date"),
            end_date=options.get("end_date"),
            refresh_cache=bool(options.get("refresh_cache", False)),
            record_trace=bool(options.get("record_trace", False)),
            generate_plots=bool(options.get("generate_plots", True)),
            run_id=run_id,
            base_dir=root,
            log_level=str(options.get("log_level", "INFO")),
            run_label=options.get("run_label"),
            on_event=_on_event,
            cancel_requested=_cancel_requested,
        )
        if not registry.finish(run_id, "succeeded"):
            # A concurrent cancel won the race after publication: stay honest.
            registry.finish(run_id, "cancelled")
            logger.info("Run '%s' completed but a cancel won the race.", run_id)
        return 0
    except CancelledRun:
        registry.finish(run_id, "cancelled")
        logger.info("Run '%s' cancelled at a safe boundary.", run_id)
        return 0
    except (ORBBaseException, Exception) as exc:  # noqa: BLE001 — must terminalize
        registry.finish(run_id, "failed", f"{type(exc).__name__}: {exc}")
        logger.error(
            "Run '%s' failed: %s\n%s", run_id, exc, traceback.format_exc(limit=5)
        )
        return 1
    finally:
        # Hand the freed slot to the next queued run (best-effort, only after
        # our own terminal outcome above is committed).  A crash before this
        # point leaves dispatch to the next supervisor call.
        try:
            from src.services.run_service import dispatch_next

            dispatch_next(root)
        except Exception as exc:
            logger.warning("Completion handoff failed (%s).", exc)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print("usage: python -m src.services.worker <storage_root> <run_id>",
              file=sys.stderr)
        return 2
    return run_worker(args[0], args[1])


if __name__ == "__main__":
    sys.exit(main())
