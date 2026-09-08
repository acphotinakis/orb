"""
src.backtest.trace
==================
Versioned decision traces captured from actual engine execution (P4-O1).

Traces explain what the engine decided, using the engine's own computed
values — replay and the UI never re-derive strategy logic.  Normal runs
record only coarse progress; detailed per-bar tracing is explicit, bounded,
and opt-in (:meth:`TraceCollector` caps events and flags truncation).

Schema version 1 event types
----------------------------
``trace_header`` (first) · ``or_frozen`` · ``signal_check`` · ``trade_opened``
· ``trade_closed``.  Every event carries ``trace_version``, ``run_id`` (when
known), ``seq``, and ``emitted_at``.  Bar timestamps are bar-OPEN times; each
check also records ``available_at`` (bar start + one bar duration — the P1-O4
availability instant).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

TRACE_SCHEMA_VERSION = 1

#: Decision rules referenced by traces and the code-trace pane.
RULE_BREAKOUT_CLOSE = "orb_breakout_close"
RULE_FLATTEN = "session_flatten"
RULE_EXIT_BRACKET = "exit_bracket"

RULE_SOURCE_MAP: Dict[str, Dict[str, str]] = {
    RULE_BREAKOUT_CLOSE: {
        "module": "src/strategy/signals.py",
        "function": "evaluate_bar_signal",
        "description": "Close-breakout entry rule shared by engine and scanner.",
    },
    RULE_EXIT_BRACKET: {
        "module": "src/backtest/engine.py",
        "function": "_evaluate_position_bar",
        "description": "Stop/target bracket evaluation with conservative dual-touch.",
    },
    RULE_FLATTEN: {
        "module": "src/backtest/engine.py",
        "function": "_force_close_position",
        "description": "End-of-session fallback flatten at the last bar.",
    },
}

#: Files the code-trace pane may display (read-only, repo-root confined).
TRACE_SOURCE_ALLOWLIST = (
    "src/strategy/signals.py",
    "src/backtest/engine.py",
    "src/backtest/models.py",
    "src/backtest/execution_model.py",
    "src/strategy/opening_range.py",
)

DEFAULT_MAX_TRACE_EVENTS = 200_000


class TraceCollector:
    """Bounded, ordered sink for engine decision events."""

    def __init__(self, max_events: int = DEFAULT_MAX_TRACE_EVENTS) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self.max_events = max_events
        self.events: List[Dict[str, Any]] = []
        self.truncated = False

    @property
    def full(self) -> bool:
        return len(self.events) >= self.max_events

    def record(self, event_type: str, **fields: Any) -> Optional[int]:
        """Append one event; returns its seq, or None when the cap is hit."""
        if self.full:
            self.truncated = True
            return None
        seq = len(self.events)
        self.events.append(
            {
                "trace_version": TRACE_SCHEMA_VERSION,
                "seq": seq,
                "event_type": event_type,
                "emitted_at": datetime.now(timezone.utc).isoformat(),
                **fields,
            }
        )
        return seq

    def header(self, **fields: Any) -> Dict[str, Any]:
        """Header record describing this trace (written first on export)."""
        return {
            "trace_version": TRACE_SCHEMA_VERSION,
            "event_type": "trace_header",
            "rule_sources": RULE_SOURCE_MAP,
            "truncated": self.truncated,
            "event_count": len(self.events),
            **fields,
        }

    def to_jsonl(self, **header_fields: Any) -> str:
        """Serialize header + events as JSON lines."""
        import json as _json

        lines = [_json.dumps(self.header(**header_fields), sort_keys=True)]
        lines.extend(_json.dumps(e, sort_keys=True, default=str) for e in self.events)
        return "\n".join(lines) + "\n"

    @staticmethod
    def parse_jsonl(payload: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Parse a trace file; raises ValueError on unknown versions.

        Returns ``(header, events)``.  Unknown ``trace_version`` values fail
        explicitly — replay never invents decisions from unsupported schemas.
        """
        import json as _json

        lines = [ln for ln in payload.splitlines() if ln.strip()]
        if not lines:
            raise ValueError("Empty trace payload.")
        header = _json.loads(lines[0])
        if header.get("event_type") != "trace_header":
            raise ValueError("Trace payload lacks a trace_header first line.")
        if header.get("trace_version") != TRACE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported trace_version {header.get('trace_version')!r}; "
                f"this build reads version {TRACE_SCHEMA_VERSION}."
            )
        events = [_json.loads(ln) for ln in lines[1:]]
        if header.get("event_count") != len(events):
            raise ValueError("Trace event count mismatch; file is incomplete.")
        previous = None
        import pandas as pd
        for index, event in enumerate(events):
            if not isinstance(event, dict) or event.get("trace_version") != TRACE_SCHEMA_VERSION:
                raise ValueError("Unsupported trace event version.")
            if event.get("seq") != index:
                raise ValueError("Trace sequence is missing or out of order.")
            try:
                available = pd.Timestamp(event["available_at"])
                if pd.isna(available) or available.tzinfo is None:
                    raise ValueError("Availability must be timezone-aware.")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Invalid trace availability.") from exc
            if previous is not None and available < previous:
                raise ValueError("Trace availability is out of order.")
            previous = available
        return header, events
