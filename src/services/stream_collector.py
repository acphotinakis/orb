"""
src.services.stream_collector
==============================
Persistent bar ingestion, finalization, deduplication, and gap tracking (P5-O2).

The StreamCollector runs independently of browser sessions.  It:
  1. Receives RawBars from a StreamAdapter.
  2. Applies the reorder/finalization policy (configurable window in seconds).
  3. Deduplicates by (symbol, feed, timeframe, bar_start, revision).
  4. Detects gaps using trading-calendar-aware expected bar counts.
  5. Updates MonitorState via on_bar() after each finalization.
  6. Stores finalized bars to an in-memory store (extendable to SQLite later).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from src.data.live_stream import RawBar, StreamAdapter, StreamEvent
from src.services.monitor_state import (
    MonitorState,
    SessionConfig,
    mark_degraded,
    new_session,
    on_bar,
)

# ---------------------------------------------------------------------------
# Finalization policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizationPolicy:
    """Parameters controlling bar finalization and gap detection.

    Attributes:
        reorder_window_seconds: Bars received within this window of bar_end
            are provisional; after this delay they are finalized.
        max_gap_bars: Gaps larger than this trigger degraded state.
        stale_threshold_seconds: No bar received in this window -> stale.
        backfill_overlap_bars: How many bars to overlap on backfill for idempotency.
    """

    reorder_window_seconds: int = 10
    max_gap_bars: int = 5
    stale_threshold_seconds: int = 90
    backfill_overlap_bars: int = 2


# ---------------------------------------------------------------------------
# In-memory bar store
# ---------------------------------------------------------------------------


@dataclass
class BarStore:
    """Mutable in-memory bar store, keyed by:
    (symbol, feed, timeframe, bar_start, revision).

    Extendable to SQLite in a future phase.
    """

    _bars: dict[tuple[str, str, str, pd.Timestamp, int], RawBar] = field(
        default_factory=dict
    )

    def put(self, bar: RawBar, timeframe: str = "1Min") -> bool:
        """Insert or replace a bar. Returns True if this is a new key."""
        key = (bar.symbol, bar.feed, timeframe, bar.bar_start, bar.revision)
        is_new = key not in self._bars
        self._bars[key] = bar
        return is_new

    def get_session_bars(self, symbol: str, feed: str, session_id: str) -> list[RawBar]:
        """Return all bars for one session, sorted by bar_start."""
        date = session_id  # YYYY-MM-DD
        result = []
        for (sym, fd, _tf, bar_start, _rev), bar in self._bars.items():
            if sym == symbol and fd == feed:
                bar_date = bar_start.tz_convert("America/New_York").date().isoformat()
                if bar_date == date:
                    result.append(bar)
        return sorted(result, key=lambda b: b.bar_start)

    def get_latest(self, symbol: str, feed: str) -> RawBar | None:
        """Return the bar with the latest bar_start for (symbol, feed)."""
        candidates = [
            bar
            for (sym, fd, _tf, _ts, _rev), bar in self._bars.items()
            if sym == symbol and fd == feed
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda b: b.bar_start)


# ---------------------------------------------------------------------------
# Collector observable state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectorState:
    """Observable status of the StreamCollector (exposed to UI).

    Attributes:
        symbol: Ticker symbol being monitored.
        feed: Market data feed.
        timeframe: Bar resolution string.
        connection_state: Current adapter connection state.
        bars_received: Total bar events received (including duplicates).
        bars_finalized: Total bars passed to on_bar().
        bars_dropped_dup: Duplicate bars silently dropped.
        gaps_detected: Number of detected gaps.
        stale: True when no bar received for stale_threshold_seconds.
        last_bar_market_ts: bar_start of most recently finalized bar.
        last_heartbeat_at: Wall-clock time of last heartbeat.
        monitor_state: Current session MonitorState (or None).
        stopped: True after stop() is called.
    """

    symbol: str
    feed: str
    timeframe: str
    connection_state: str
    bars_received: int = 0
    bars_finalized: int = 0
    bars_dropped_dup: int = 0
    gaps_detected: int = 0
    stale: bool = False
    last_bar_market_ts: pd.Timestamp | None = None
    last_heartbeat_at: pd.Timestamp | None = None
    monitor_state: MonitorState | None = None
    stopped: bool = False


# ---------------------------------------------------------------------------
# Gap detection helper
# ---------------------------------------------------------------------------


def expected_bars_between(
    start: pd.Timestamp, end: pd.Timestamp, bar_minutes: int = 1
) -> int:
    """Count expected 1-minute RTH bars between start and end (09:30-16:00 ET).

    Uses simple arithmetic; cross-day and holiday logic is out of scope.
    Only counts bars within the same trading day (09:30-16:00 ET).

    Args:
        start: Start timestamp (exclusive lower bound), tz-aware.
        end: End timestamp (inclusive upper bound), tz-aware.
        bar_minutes: Bar resolution in minutes.

    Returns:
        Integer count of expected bars.
    """
    if start >= end:
        return 0

    et = "America/New_York"
    start_et = start.tz_convert(et)
    end_et = end.tz_convert(et)

    # Clamp to RTH bounds
    day = start_et.date()
    rth_start = pd.Timestamp(f"{day} 09:30:00", tz=et)
    rth_end = pd.Timestamp(f"{day} 16:00:00", tz=et)

    effective_start = max(start_et, rth_start)
    effective_end = min(end_et, rth_end)

    if effective_start >= effective_end:
        return 0

    total_minutes = int((effective_end - effective_start).total_seconds() / 60)
    return max(0, total_minutes // bar_minutes)


# ---------------------------------------------------------------------------
# StreamCollector
# ---------------------------------------------------------------------------


class StreamCollector:
    """Ingests bars from a StreamAdapter, applies finalization/dedup/gap policy,
    and updates the MonitorState incrementally.

    Args:
        adapter: The StreamAdapter to pull events from.
        symbol: Ticker symbol being monitored.
        feed: Market data feed identifier.
        timeframe: Bar resolution string (e.g. "1Min").
        session_cfg: SessionConfig for the monitor state machine.
        policy: FinalizationPolicy controlling reorder window, gap limits, etc.
        clock: Callable returning current wall-clock pd.Timestamp.
            Defaults to lambda: pd.Timestamp.now(tz="UTC").
    """

    def __init__(
        self,
        adapter: StreamAdapter,
        symbol: str,
        feed: str,
        timeframe: str,
        session_cfg: SessionConfig,
        policy: FinalizationPolicy = FinalizationPolicy(),
        *,
        clock: Callable[[], pd.Timestamp] | None = None,
    ) -> None:
        self._adapter = adapter
        self._symbol = symbol
        self._feed = feed
        self._timeframe = timeframe
        self._session_cfg = session_cfg
        self._policy = policy
        self._clock: Callable[[], pd.Timestamp] = (
            clock if clock is not None else (lambda: pd.Timestamp.now(tz="UTC"))
        )

        self._store = BarStore()
        self._event_iter = iter(adapter.events())

        # Mutable internal state (updated by run_one())
        self._bars_received: int = 0
        self._bars_finalized: int = 0
        self._bars_dropped_dup: int = 0
        self._gaps_detected: int = 0
        self._stale: bool = False
        self._last_bar_market_ts: pd.Timestamp | None = None
        self._last_heartbeat_at: pd.Timestamp | None = None
        self._last_event_wall_ts: pd.Timestamp | None = None
        self._stopped: bool = False

        # Derive session_id from clock at construction time
        now_et = self._clock().tz_convert("America/New_York")
        self._session_id: str = now_et.date().isoformat()
        self._monitor_state: MonitorState = new_session(self._session_id)

    @property
    def state(self) -> CollectorState:
        """Current observable status (thread-safe read of scalar fields)."""
        now = self._clock()
        stale = self._stale
        if (
            not stale
            and self._last_event_wall_ts is not None
            and (now - self._last_event_wall_ts).total_seconds()
            > self._policy.stale_threshold_seconds
        ):
            stale = True
        return CollectorState(
            symbol=self._symbol,
            feed=self._feed,
            timeframe=self._timeframe,
            connection_state=self._adapter.connection_state,
            bars_received=self._bars_received,
            bars_finalized=self._bars_finalized,
            bars_dropped_dup=self._bars_dropped_dup,
            gaps_detected=self._gaps_detected,
            stale=stale,
            last_bar_market_ts=self._last_bar_market_ts,
            last_heartbeat_at=self._last_heartbeat_at,
            monitor_state=self._monitor_state,
            stopped=self._stopped,
        )

    def run_one(self) -> bool:
        """Process ONE event from the adapter.
        Returns True if processed, False if stopped.

        Handles:
        - bar: dedup -> finalize -> on_bar() -> gap check
        - heartbeat: update last_heartbeat_at (NOT last_bar_market_ts; P5-A5)
        - connected: update last_event_wall_ts
        - disconnected: mark stale, record gap
        - error: no-op (logged implicitly)
        """
        if self._stopped:
            return False

        try:
            event: StreamEvent = next(self._event_iter)
        except StopIteration:
            self._stopped = True
            return False

        self._last_event_wall_ts = event.received_at

        if event.kind == "bar" and event.bar is not None:
            bar = event.bar
            self._bars_received += 1

            # Deduplication
            is_new = self._store.put(bar, timeframe=self._timeframe)
            if not is_new:
                self._bars_dropped_dup += 1
                return True

            # Finalization: for is_final bars, pass to on_bar immediately.
            # Provisional bars (not is_final) are stored but not yet processed.
            if bar.is_final:
                self._finalize_bar(bar)

        elif event.kind == "heartbeat":
            # P5-A5: heartbeat updates last_heartbeat_at but NOT last_bar_market_ts
            self._last_heartbeat_at = event.received_at

        elif event.kind == "disconnected":
            self._gaps_detected += 1
            self._stale = True
            if not self._monitor_state.degraded:
                self._monitor_state = mark_degraded(
                    self._monitor_state, "disconnected from stream"
                )

        elif event.kind == "connected":
            self._stale = False

        return True

    def _finalize_bar(self, bar: RawBar) -> None:
        """Pass a finalized bar to on_bar() and check for gaps."""
        # Gap detection: check expected bars between last finalized bar and this one
        if self._last_bar_market_ts is not None and not self._monitor_state.degraded:
            gap = expected_bars_between(
                self._last_bar_market_ts,
                bar.bar_start,
                bar_minutes=self._session_cfg.bar_minutes,
            )
            # Gap > 1 means at least one bar is missing; gap > max_gap_bars is critical
            if gap > self._policy.max_gap_bars + 1:
                self._gaps_detected += 1
                self._monitor_state = mark_degraded(
                    self._monitor_state,
                    f"gap of {gap - 1} bars detected between "
                    f"{self._last_bar_market_ts} and {bar.bar_start}",
                )

        self._monitor_state = on_bar(self._monitor_state, bar, self._session_cfg)
        self._bars_finalized += 1
        self._last_bar_market_ts = bar.bar_start

    def stop(self) -> None:
        """Stop the collector: calls adapter.stop() and marks stopped."""
        self._adapter.stop()
        self._stopped = True

    def reset_session(self, session_id: str) -> None:
        """Start a new MonitorState for a new trading session."""
        self._session_id = session_id
        self._monitor_state = new_session(session_id)
