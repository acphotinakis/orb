"""
src.services.replay
===================
Deterministic replay reducer, cursor filtering, and run comparison (P4).

All functions are pure and UI-independent: given recorded trace events plus
the run's own artifacts, they reconstruct exactly the information available
at a cursor.  Future candles, entries, outcomes, frozen ranges, and full-run
metrics are never exposed at or before the cursor (P4-A2).

Cursor semantics: ``cursor`` counts applied trace events (``events[:cursor]``).
Only recorded bar observations and applied freeze events supply visible market
state. Full-session input frames are retained in the API for compatibility but
are never a replay data source. Equal availability times are resolved by seq.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

CHECKPOINT_EVERY = 5_000


@dataclass(frozen=True)
class ReplayState:
    """Information available at one replay cursor (never future state)."""

    cursor: int
    cursor_time: Optional[pd.Timestamp]
    visible_bars: pd.DataFrame
    range_observed_high: Optional[float]
    range_observed_low: Optional[float]
    range_frozen: bool
    range_frozen_high: Optional[float]
    range_frozen_low: Optional[float]
    open_position: Optional[Mapping[str, Any]]
    closed_trades: Tuple[Mapping[str, Any], ...] = ()
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class ReplayCheckpoint:
    """Lightweight resume point bounding seek work (P4-A7)."""

    cursor: int
    cursor_time: Optional[pd.Timestamp]
    open_position: Optional[Mapping[str, Any]]
    closed_trades: Tuple[Mapping[str, Any], ...] = ()
    realized_pnl: float = 0.0


def _parse_time(value: Any) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    try:
        return pd.Timestamp(value)
    except (ValueError, TypeError):
        return None


def _or_window_end(session_date: str, or_minutes: int) -> pd.Timestamp:
    day_open = pd.Timestamp(f"{session_date} 09:30:00", tz="America/New_York")
    return day_open + pd.Timedelta(minutes=or_minutes)


def reduce_events(
    events: List[Mapping[str, Any]],
    cursor: int,
    *,
    session_bars: pd.DataFrame,
    session_id: str,
    or_minutes: int,
    bar_minutes: int,
    start_from: Optional[ReplayCheckpoint] = None,
) -> ReplayState:
    """Reconstruct replay state by applying ``events[:cursor]`` in order.

    Args:
        events: Trace events in sequence order (single session).
        cursor: Number of events to apply (clamped to ``len(events)``).
        session_bars: Full session bars (filtered internally by availability).
        session_id: Session under replay.
        or_minutes: Opening-range length (freeze boundary).
        bar_minutes: Bar duration (availability = start + duration).
        start_from: Optional checkpoint at or before *cursor*.

    Backward seeks rebuild from the checkpoint (or scratch) — state is never
    mutated in place, so stepping back cannot leak advanced positions or P&L.
    """
    cursor = max(0, min(cursor, len(events)))
    start = 0
    open_position: Optional[Mapping[str, Any]] = None
    closed: List[Mapping[str, Any]] = []
    realized = 0.0
    cursor_time: Optional[pd.Timestamp] = None
    if start_from is not None and 0 <= start_from.cursor <= cursor:
        start = start_from.cursor
        open_position = start_from.open_position
        closed = list(start_from.closed_trades)
        realized = start_from.realized_pnl
        cursor_time = start_from.cursor_time

    for event in events[start:cursor]:
        available = _parse_time(event.get("available_at"))
        if available is not None and (cursor_time is None or available > cursor_time):
            cursor_time = available
        etype = event.get("event_type")
        if etype == "trade_opened":
            open_position = dict(event)
        elif etype == "trade_closed":
            trade_id = event.get("trade_id")
            if open_position is not None and open_position.get("trade_id") == trade_id:
                open_position = None
            closed.append(dict(event))
            try:
                realized += float(event.get("pnl_dollars", 0.0))
            except (TypeError, ValueError):
                pass

    # Recorded observations are authoritative. Never read an unapplied event's
    # payload or use full-session derived columns in a replay-visible frame.
    applied = events[:cursor]
    observed = [e for e in applied if e.get("event_type") == "bar_observed"]
    columns = ["timestamp", "open", "high", "low", "close", "volume",
               "minute_of_day", "is_opening_range"]
    visible = pd.DataFrame([
        {"timestamp": pd.Timestamp(e["bar_start"]),
         **{key: e.get(key) for key in columns if key != "timestamp"}}
        for e in observed
    ], columns=columns)
    freezes = [e for e in applied if e.get("event_type") == "or_frozen"]
    frozen = bool(freezes)
    or_bars = visible.loc[visible["is_opening_range"] == True]
    observed_high = float(or_bars["high"].max()) if not or_bars.empty else None
    observed_low = float(or_bars["low"].min()) if not or_bars.empty else None
    frozen_high = freezes[-1].get("or_high") if freezes else None
    frozen_low = freezes[-1].get("or_low") if freezes else None

    return ReplayState(
        cursor=cursor,
        cursor_time=cursor_time,
        visible_bars=visible,
        range_observed_high=observed_high,
        range_observed_low=observed_low,
        range_frozen=frozen,
        range_frozen_high=frozen_high,
        range_frozen_low=frozen_low,
        open_position=open_position,
        closed_trades=tuple(closed),
        realized_pnl=realized,
    )


def build_checkpoints(
    events: List[Mapping[str, Any]], every: int = CHECKPOINT_EVERY
) -> List[ReplayCheckpoint]:
    """Sparse resume points over the event stream (position/P&L only)."""
    if every < 1:
        raise ValueError("Checkpoint interval must be positive")
    checkpoints = [
        ReplayCheckpoint(cursor=0, cursor_time=None, open_position=None,
                         closed_trades=(), realized_pnl=0.0)
    ]
    open_position: Optional[Dict[str, Any]] = None
    closed: List[Dict[str, Any]] = []
    realized = 0.0
    cursor_time: Optional[pd.Timestamp] = None
    for index, event in enumerate(events, start=1):
        available = _parse_time(event.get("available_at"))
        if available is not None and (cursor_time is None or available > cursor_time):
            cursor_time = available
        if event.get("event_type") == "trade_opened":
            open_position = dict(event)
        elif event.get("event_type") == "trade_closed":
            trade_id = event.get("trade_id")
            if open_position is not None and open_position.get("trade_id") == trade_id:
                open_position = None
            closed.append(dict(event))
            try:
                realized += float(event.get("pnl_dollars", 0.0))
            except (TypeError, ValueError):
                pass
        if index % every == 0:
            checkpoints.append(
                ReplayCheckpoint(
                    cursor=index, cursor_time=cursor_time,
                    open_position=dict(open_position) if open_position else None,
                    closed_trades=tuple(dict(c) for c in closed),
                    realized_pnl=realized,
                )
            )
    return checkpoints


def nearest_checkpoint(
    checkpoints: List[ReplayCheckpoint], cursor: int
) -> ReplayCheckpoint:
    """Greatest checkpoint at or before *cursor* (cursor 0 always exists)."""
    best = checkpoints[0]
    for checkpoint in checkpoints:
        if checkpoint.cursor <= cursor and checkpoint.cursor >= best.cursor:
            best = checkpoint
    return best


def filter_session_events(
    events: List[Mapping[str, Any]], session_id: str
) -> List[Mapping[str, Any]]:
    """Trace events for one session, preserving sequence order."""
    return [e for e in events if e.get("session_id") == session_id]


# ---------------------------------------------------------------------------
# Comparison (P4-O3): explicit compatibility, aligned overlays
# ---------------------------------------------------------------------------


def diff_configs(
    config_a: Mapping[str, Any], config_b: Mapping[str, Any], prefix: str = ""
) -> List[Tuple[str, Any, Any]]:
    """Recursive config diff as ``(dotted_path, value_a, value_b)`` rows."""
    rows: List[Tuple[str, Any, Any]] = []
    keys = sorted(set(config_a) | set(config_b))
    for key in keys:
        path = f"{prefix}.{key}" if prefix else str(key)
        missing = object()
        value_a = config_a.get(key, missing)
        value_b = config_b.get(key, missing)
        if isinstance(value_a, Mapping) and isinstance(value_b, Mapping):
            rows.extend(diff_configs(value_a, value_b, path))
        elif value_a != value_b:
            rows.append((
                path,
                None if value_a is missing else value_a,
                None if value_b is missing else value_b,
            ))
    return rows


def metric_deltas(
    metrics_a: Mapping[str, Any], metrics_b: Mapping[str, Any]
) -> List[Tuple[str, str, Any, Any, Any]]:
    """Numeric trade/portfolio metric deltas as ``(section, key, a, b, b-a)``."""
    rows: List[Tuple[str, str, Any, Any, Any]] = []
    for section in ("trade_metrics", "portfolio_metrics"):
        section_a = metrics_a.get(section, {}) or {}
        section_b = metrics_b.get(section, {}) or {}
        for key in sorted(set(section_a) | set(section_b)):
            value_a, value_b = section_a.get(key), section_b.get(key)
            delta = None
            if isinstance(value_a, (int, float)) and isinstance(value_b, (int, float)):
                delta = value_b - value_a
            rows.append((section, key, value_a, value_b, delta))
    return rows


def compatibility_notes(
    manifest_a: Mapping[str, Any], manifest_b: Mapping[str, Any]
) -> List[str]:
    """Human-readable input-compatibility labels (never silent overlays)."""
    notes: List[str] = []
    dataset_a = manifest_a.get("datasets", {}).get("processed", {})
    dataset_b = manifest_b.get("datasets", {}).get("processed", {})
    for label, path in (
        ("symbol", "symbol"),
        ("feed", "feed"),
        ("timeframe", "timeframe"),
        ("data version", "identity_hash"),
    ):
        value_a, value_b = dataset_a.get(path), dataset_b.get(path)
        if value_a != value_b:
            notes.append(f"Different {label}: {value_a!r} vs {value_b!r}.")
    config_a = manifest_a.get("config", {}).get("execution", {})
    config_b = manifest_b.get("config", {}).get("execution", {})
    if config_a.get("initial_capital") != config_b.get("initial_capital"):
        notes.append(
            f"Different initial capital: {config_a.get('initial_capital')!r} vs "
            f"{config_b.get('initial_capital')!r}; absolute equity is not comparable."
        )
    request_a = manifest_a.get("request", {})
    request_b = manifest_b.get("request", {})
    if (request_a.get("start_date"), request_a.get("end_date")) != (
        request_b.get("start_date"), request_b.get("end_date")
    ):
        notes.append("Different date ranges: overlay defaults to common timestamps.")
    return notes


def align_equity(
    equity_a: pd.DataFrame, equity_b: pd.DataFrame, mode: str = "common"
) -> pd.DataFrame:
    """Align two equity curves on timestamps.

    * ``common`` (default): inner join on shared timestamps only.
    * ``full``: outer join with an explicit ``overlap`` flag per row; missing
      sessions are NEVER zero-filled — non-overlapping rows carry NaN, and
      the caller must label the view accordingly.
    """
    if mode not in ("common", "full"):
        raise ValueError("mode must be 'common' or 'full'")
    left = equity_a[["timestamp", "equity"]].rename(columns={"equity": "equity_a"})
    right = equity_b[["timestamp", "equity"]].rename(columns={"equity": "equity_b"})
    merged = pd.merge(left, right, on="timestamp", how="inner" if mode == "common" else "outer")
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    if mode == "full":
        merged["overlap"] = merged["equity_a"].notna() & merged["equity_b"].notna()
    return merged


def normalized_returns(
    aligned: pd.DataFrame, initial_a: float, initial_b: float
) -> pd.DataFrame:
    """Add explicitly-baselined normalized return columns (no silent policy)."""
    if not initial_a or initial_a <= 0 or not initial_b or initial_b <= 0:
        raise ValueError(
            "Normalized returns require positive starting equity for both runs."
        )
    out = aligned.copy()
    out["return_a"] = out["equity_a"] / initial_a - 1.0
    out["return_b"] = out["equity_b"] / initial_b - 1.0
    return out


# ---------------------------------------------------------------------------
# Code trace (P4-O4): recorded rule references, read-only source pane
# ---------------------------------------------------------------------------


def read_source_excerpt(
    repo_root: str | Path,
    module: str,
    function: Optional[str] = None,
    max_lines: int = 80,
) -> str:
    """Return a read-only excerpt of an allowlisted repository source file.

    Args:
        repo_root: Repository root confining all reads.
        module: Allowlisted ``src/...`` module path.
        function: Optional ``def <name>`` anchor; returns that block when
            found, else the file head.
        max_lines: Excerpt cap.

    Raises:
        ValueError: Module outside the allowlist or unreadable.  Current
        worktree text is returned as-is; callers label it with the RECORDED
        revision/fingerprint and an unavailable note when snapshots are
        missing — current code never masquerades as historical code.
    """
    from pathlib import Path as _Path

    from src.backtest.trace import TRACE_SOURCE_ALLOWLIST
    from src.services.artifact_store import ensure_within_root

    if module not in TRACE_SOURCE_ALLOWLIST:
        raise ValueError(f"Module '{module}' is outside the source allowlist.")
    root = _Path(repo_root)
    target = ensure_within_root(root, root / module)
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Source '{module}' is unavailable: {exc}.")
    if function:
        for index, line in enumerate(lines):
            if line.startswith(f"def {function}("):
                return "\n".join(lines[index:index + max_lines])
    return "\n".join(lines[:max_lines])
