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

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

CHECKPOINT_EVERY = 5_000


@dataclass(frozen=True)
class ReplayState:
    """Information available at one replay cursor (never future state)."""

    cursor: int
    cursor_time: pd.Timestamp | None
    visible_bars: pd.DataFrame
    range_observed_high: float | None
    range_observed_low: float | None
    range_frozen: bool
    range_frozen_high: float | None
    range_frozen_low: float | None
    open_position: Mapping[str, Any] | None
    closed_trades: tuple[Mapping[str, Any], ...] = ()
    realized_pnl: float = 0.0
    equity: pd.DataFrame = field(default_factory=pd.DataFrame)
    decision: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ReplayCheckpoint:
    """Lightweight resume point bounding seek work (P4-A7)."""

    cursor: int
    cursor_time: pd.Timestamp | None
    open_position: Mapping[str, Any] | None
    closed_trades: tuple[Mapping[str, Any], ...] = ()
    realized_pnl: float = 0.0


def _parse_time(value: Any) -> pd.Timestamp | None:
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
    events: list[Mapping[str, Any]],
    cursor: int,
    *,
    session_bars: pd.DataFrame,
    session_id: str,
    or_minutes: int,
    bar_minutes: int,
    start_from: ReplayCheckpoint | None = None,
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
    open_position: Mapping[str, Any] | None = None
    closed: list[Mapping[str, Any]] = []
    realized = 0.0
    cursor_time: pd.Timestamp | None = None
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
    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "minute_of_day",
        "is_opening_range",
    ]
    visible = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(e["bar_start"]),
                **{key: e.get(key) for key in columns if key != "timestamp"},
            }
            for e in observed
        ],
        columns=columns,
    )
    freezes = [e for e in applied if e.get("event_type") == "or_frozen"]
    frozen = bool(freezes)
    or_bars = visible.loc[visible["is_opening_range"]]
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
    events: list[Mapping[str, Any]], every: int = CHECKPOINT_EVERY
) -> list[ReplayCheckpoint]:
    """Sparse resume points over the event stream (position/P&L only)."""
    if every < 1:
        raise ValueError("Checkpoint interval must be positive")
    checkpoints = [
        ReplayCheckpoint(
            cursor=0,
            cursor_time=None,
            open_position=None,
            closed_trades=(),
            realized_pnl=0.0,
        )
    ]
    open_position: dict[str, Any] | None = None
    closed: list[dict[str, Any]] = []
    realized = 0.0
    cursor_time: pd.Timestamp | None = None
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
                    cursor=index,
                    cursor_time=cursor_time,
                    open_position=dict(open_position) if open_position else None,
                    closed_trades=tuple(dict(c) for c in closed),
                    realized_pnl=realized,
                )
            )
    return checkpoints


def nearest_checkpoint(
    checkpoints: list[ReplayCheckpoint], cursor: int
) -> ReplayCheckpoint:
    """Greatest checkpoint at or before *cursor* (cursor 0 always exists)."""
    best = checkpoints[0]
    for checkpoint in checkpoints:
        if checkpoint.cursor <= cursor and checkpoint.cursor >= best.cursor:
            best = checkpoint
    return best


def filter_session_events(
    events: list[Mapping[str, Any]], session_id: str
) -> list[Mapping[str, Any]]:
    """Trace events for one session, preserving sequence order."""
    return [e for e in events if e.get("session_id") == session_id]


@dataclass(frozen=True)
class Playback:
    cursor: int = 0
    playing: bool = False
    speed: float = 1.0
    next_due: float = 0.0


def playback_action(
    state: Playback, action: str, total: int, now: float, value: float | None = None
) -> Playback:
    """One scheduled action advances at most once; rerenders cannot catch up."""
    from dataclasses import replace

    if action == "reset":
        return Playback(speed=state.speed)
    if action == "pause":
        return replace(state, playing=False)
    if action == "play":
        return replace(
            state, playing=state.cursor < total, next_due=now + 1 / state.speed
        )
    if action == "speed":
        if value not in (0.5, 1.0, 2.0, 4.0, 8.0):
            raise ValueError("Unsupported playback speed")
        return replace(state, speed=value, next_due=now + 1 / value)
    if action in ("step", "seek"):
        cursor = min(
            total, max(0, state.cursor + 1 if action == "step" else int(value))
        )
        return replace(state, cursor=cursor, playing=False)
    if action == "tick":
        if state.playing and now >= state.next_due:
            cursor = min(total, state.cursor + 1)
            return replace(
                state,
                cursor=cursor,
                playing=cursor < total,
                next_due=now + 1 / state.speed,
            )
        return state
    raise ValueError("Unknown playback action")


class ReplayIndex:
    """Build once, seek with <=1,000 scalar events and indexed dataframe slices.

    OHLCV, trades and equity each have a single columnar index, not a copied
    history per checkpoint. Checkpoints retain only range/position/P&L scalars.
    Presentation copies only the requested prefix (or a bounded chart tail).
    """

    def __init__(self, events, every=1000):
        if every < 1:
            raise ValueError("Checkpoint interval must be positive")
        self.events = tuple(events)
        self.every = every
        self.checkpoints = {0: self._empty()}
        self.bar_at, self.trade_at, self.equity_at = [], [], []
        bars, trades, equity = [], [], []
        scalar = self._empty()
        for cursor, event in enumerate(events, 1):
            self._apply(scalar, event)
            kind = event["event_type"]
            if kind == "bar_observed":
                self.bar_at.append(cursor)
                bars.append(
                    {
                        "timestamp": pd.Timestamp(event["bar_start"]),
                        **{
                            k: event[k]
                            for k in (
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "minute_of_day",
                                "is_opening_range",
                            )
                        },
                    }
                )
            elif kind == "trade_closed":
                self.trade_at.append(cursor)
                trades.append(dict(event))
            elif kind == "equity_mark":
                self.equity_at.append(cursor)
                equity.append(
                    {
                        "timestamp": pd.Timestamp(event["bar_start"]),
                        "session_id": event["session_id"],
                        **{k: event[k] for k in ("cash", "position_value", "equity")},
                    }
                )
            if cursor % every == 0:
                self.checkpoints[cursor] = scalar.copy()
        self.bars = pd.DataFrame(
            bars,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "minute_of_day",
                "is_opening_range",
            ],
        )
        self.trades = tuple(trades)
        self.equity = pd.DataFrame(
            equity,
            columns=["timestamp", "session_id", "cash", "position_value", "equity"],
        )

    @staticmethod
    def _empty():
        return dict(
            time=None,
            position=None,
            pnl=0.0,
            high=None,
            low=None,
            frozen=False,
            frozen_high=None,
            frozen_low=None,
            decision=None,
        )

    @staticmethod
    def _apply(s, event):
        s["time"] = _parse_time(event.get("available_at")) or s["time"]
        kind = event["event_type"]
        if kind == "bar_observed" and event["is_opening_range"]:
            s["high"] = (
                event["high"] if s["high"] is None else max(s["high"], event["high"])
            )
            s["low"] = event["low"] if s["low"] is None else min(s["low"], event["low"])
        elif kind == "or_frozen":
            s.update(
                frozen=True,
                frozen_high=event.get("or_high"),
                frozen_low=event.get("or_low"),
            )
        elif kind == "trade_opened":
            s["position"] = dict(event)
        elif kind == "trade_closed":
            if s["position"] is not None and s["position"].get("trade_id") == event.get(
                "trade_id"
            ):
                s["position"] = None
            s["pnl"] += float(event.get("pnl_dollars", 0))
        if event.get("rule_id"):
            s["decision"] = dict(event)

    def seek(self, cursor: int, max_bars: int | None = None) -> ReplayState:
        from bisect import bisect_right

        cursor = max(0, min(len(self.events), cursor))
        start = cursor // self.every * self.every
        scalar = self.checkpoints[start].copy()
        for event in self.events[start:cursor]:
            self._apply(scalar, event)
        n = bisect_right(self.bar_at, cursor)
        first = max(0, n - max_bars) if max_bars is not None else 0
        return ReplayState(
            cursor,
            scalar["time"],
            self.bars.iloc[first:n].copy(),
            scalar["high"],
            scalar["low"],
            scalar["frozen"],
            scalar["frozen_high"],
            scalar["frozen_low"],
            scalar["position"],
            self.trades[: bisect_right(self.trade_at, cursor)],
            scalar["pnl"],
            self.equity.iloc[: bisect_right(self.equity_at, cursor)].copy(),
            scalar["decision"],
        )


# ---------------------------------------------------------------------------
# Comparison (P4-O3): explicit compatibility, aligned overlays
# ---------------------------------------------------------------------------


def diff_configs(
    config_a: Mapping[str, Any], config_b: Mapping[str, Any], prefix: str = ""
) -> list[tuple[str, Any, Any]]:
    """Recursive config diff as ``(dotted_path, value_a, value_b)`` rows."""
    rows: list[tuple[str, Any, Any]] = []
    keys = sorted(set(config_a) | set(config_b))
    for key in keys:
        path = f"{prefix}.{key}" if prefix else str(key)
        missing = object()
        value_a = config_a.get(key, missing)
        value_b = config_b.get(key, missing)
        if isinstance(value_a, Mapping) and isinstance(value_b, Mapping):
            rows.extend(diff_configs(value_a, value_b, path))
        elif value_a != value_b:
            rows.append(
                (
                    path,
                    None if value_a is missing else value_a,
                    None if value_b is missing else value_b,
                )
            )
    return rows


def metric_deltas(
    metrics_a: Mapping[str, Any], metrics_b: Mapping[str, Any]
) -> list[tuple[str, str, Any, Any, Any]]:
    """Numeric trade/portfolio metric deltas as ``(section, key, a, b, b-a)``."""
    rows: list[tuple[str, str, Any, Any, Any]] = []
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
) -> list[str]:
    """Human-readable input-compatibility labels (never silent overlays)."""
    notes: list[str] = []

    def inputs(manifest):
        datasets = manifest.get("datasets", {})
        processed = datasets.get("processed", {})
        return {
            **manifest.get("config", {}).get("data", {}),
            **datasets.get("raw", {}),
            **processed.get("identity", {}),
            **processed,
        }

    dataset_a, dataset_b = inputs(manifest_a), inputs(manifest_b)
    if manifest_a.get("source") != manifest_b.get("source"):
        notes.append("Different recorded source revisions/fingerprints.")
    if dataset_a.get("fingerprint") != dataset_b.get("fingerprint"):
        notes.append("Different data content fingerprints.")
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
        request_b.get("start_date"),
        request_b.get("end_date"),
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
    left = left.assign(
        timestamp=pd.to_datetime(left.timestamp, utc=True)
    ).drop_duplicates("timestamp", keep="last")
    right = right.assign(
        timestamp=pd.to_datetime(right.timestamp, utc=True)
    ).drop_duplicates("timestamp", keep="last")
    merged = pd.merge(
        left, right, on="timestamp", how="inner" if mode == "common" else "outer"
    )
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    if mode == "full":
        merged["overlap"] = merged["equity_a"].notna() & merged["equity_b"].notna()
    return merged


def normalized_returns(
    aligned: pd.DataFrame, initial_a: float, initial_b: float
) -> pd.DataFrame:
    """Add explicitly-baselined normalized return columns (no silent policy)."""
    if any(
        not isinstance(x, (int, float))
        or isinstance(x, bool)
        or not math.isfinite(x)
        or x <= 0
        for x in (initial_a, initial_b)
    ):
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
    snapshot: Mapping[str, Any],
    module: str,
    function: str | None = None,
    max_lines: int = 80,
) -> str:
    """Read only a verified historical snapshot; never consult the worktree."""
    from src.services.source_snapshot import historical_source

    return "\n".join(
        historical_source(snapshot, module, function).splitlines()[:max_lines]
    )
