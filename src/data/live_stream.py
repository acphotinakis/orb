"""
src.data.live_stream
====================
Injectable market-data streaming interface (P5-O1).

The abstract StreamAdapter defines the contract; FakeStreamAdapter provides
a deterministic, credential-free implementation for testing.  Real provider
adapters are instantiated only when credentials and connection are explicitly
requested -- never at import or at class definition time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RawBar:
    """One received market bar from the streaming adapter.

    Attributes:
        symbol: Ticker symbol.
        feed: Market data feed (e.g. "sip", "iex").
        bar_start: Timezone-aware bar open timestamp.
        bar_end: Timezone-aware bar close timestamp.
        open/high/low/close/volume: OHLCV prices.
        received_at: Wall-clock receipt time. NEVER used for market logic.
        revision: 0 = initial; >0 = correction.
        is_final: True once the finalization window has elapsed.
    """

    symbol: str
    feed: str
    bar_start: pd.Timestamp
    bar_end: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    received_at: pd.Timestamp
    revision: int = 0
    is_final: bool = False


@dataclass(frozen=True)
class StreamEvent:
    """Discriminated union wrapper for all StreamAdapter events.

    Attributes:
        kind: One of: bar, heartbeat, connected, disconnected, error.
        received_at: Wall-clock time this event was received.
        bar: RawBar payload (only when kind==bar).
        message: Error/status text, already redacted of credentials.
    """

    kind: str
    received_at: pd.Timestamp
    bar: RawBar | None = None
    message: str | None = None


class StreamAdapter(ABC):
    """Injectable streaming market-data interface (P5-O1).

    Second subscribe() for the same (symbol, feed, timeframe) is a no-op.
    """

    @abstractmethod
    def subscribe(self, symbol: str, feed: str, timeframe: str) -> None:
        """Register a market-data stream (deduplicated)."""

    @abstractmethod
    def unsubscribe(self, symbol: str, feed: str, timeframe: str) -> None:
        """Cancel a subscription."""

    @abstractmethod
    def events(self) -> Iterator[StreamEvent]:
        """Yield events; blocks until one arrives; stoppable via stop()."""

    @abstractmethod
    def stop(self) -> None:
        """Release subscription and persist stopped state."""

    @property
    @abstractmethod
    def connection_state(self) -> str:
        """One of: connected, connecting, disconnected, recovering, stopped."""

    @property
    @abstractmethod
    def last_heartbeat_at(self) -> pd.Timestamp | None:
        """Wall-clock time of the most recent heartbeat, or None."""

    @property
    @abstractmethod
    def last_bar_at(self) -> pd.Timestamp | None:
        """Market bar_start of last received bar, or None.
        Heartbeats must NOT update this property (P5-A5).
        """


class FakeStreamAdapter(StreamAdapter):
    """Deterministic, credential-free test double for StreamAdapter.

    Args:
        bars: Ordered list of RawBar instances to emit.
        clock: Callable returning current wall-clock pd.Timestamp.
            Defaults to lambda: pd.Timestamp.now(tz="UTC").
        drop_indices: Zero-based bar indices to silently skip (simulates gaps).
        disconnect_after: Emit disconnected after this many bars emitted, then resume.
        duplicate_indices: Indices to emit twice (tests deduplication).
        heartbeat_every: Emit heartbeat every N bars (default 30).
    """

    def __init__(
        self,
        bars: list[RawBar],
        *,
        clock: Callable[[], pd.Timestamp] | None = None,
        drop_indices: set[int] | None = None,
        disconnect_after: int | None = None,
        duplicate_indices: set[int] | None = None,
        heartbeat_every: int = 30,
    ) -> None:
        self._bars = bars
        self._clock: Callable[[], pd.Timestamp] = (
            clock if clock is not None else (lambda: pd.Timestamp.now(tz="UTC"))
        )
        self._drop_indices: set[int] = drop_indices or set()
        self._disconnect_after: int | None = disconnect_after
        self._duplicate_indices: set[int] = duplicate_indices or set()
        self._heartbeat_every: int = heartbeat_every
        self._stopped: bool = False
        self._connection_state: str = "connecting"
        self._last_heartbeat_at: pd.Timestamp | None = None
        self._last_bar_at: pd.Timestamp | None = None
        self._subscriptions: set[tuple] = set()

    def subscribe(self, symbol: str, feed: str, timeframe: str) -> None:
        self._subscriptions.add((symbol, feed, timeframe))

    def unsubscribe(self, symbol: str, feed: str, timeframe: str) -> None:
        self._subscriptions.discard((symbol, feed, timeframe))

    def events(self) -> Iterator[StreamEvent]:
        """Yield events deterministically from the configured bar sequence.

        Emits:
        1. connected event on first entry.
        2. For each bar (skipping drop_indices):
           - disconnected/connected pair at disconnect_after threshold.
           - heartbeat every heartbeat_every bars (not at bar 0).
           - bar event (twice if index in duplicate_indices).
        """
        if self._stopped:
            return

        self._connection_state = "connected"
        yield StreamEvent(
            kind="connected",
            received_at=self._clock(),
            message="FakeStreamAdapter connected",
        )

        bars_emitted = 0
        disconnected_once = False

        for idx, bar in enumerate(self._bars):
            if self._stopped:
                return

            if idx in self._drop_indices:
                continue

            # Disconnect threshold (before emitting bar)
            if (
                self._disconnect_after is not None
                and bars_emitted >= self._disconnect_after
                and not disconnected_once
            ):
                disconnected_once = True
                self._connection_state = "disconnected"
                yield StreamEvent(
                    kind="disconnected",
                    received_at=self._clock(),
                    message="FakeStreamAdapter simulated disconnect",
                )
                if self._stopped:
                    return
                self._connection_state = "connected"
                yield StreamEvent(
                    kind="connected",
                    received_at=self._clock(),
                    message="FakeStreamAdapter reconnected",
                )
                if self._stopped:
                    return

            # Heartbeat at interval boundary
            if (
                self._heartbeat_every > 0
                and bars_emitted > 0
                and bars_emitted % self._heartbeat_every == 0
            ):
                now = self._clock()
                self._last_heartbeat_at = now
                yield StreamEvent(
                    kind="heartbeat", received_at=now, message="heartbeat"
                )
                if self._stopped:
                    return

            # Emit bar (twice if in duplicate_indices)
            emit_count = 2 if idx in self._duplicate_indices else 1
            for _ in range(emit_count):
                if self._stopped:
                    return
                now = self._clock()
                self._last_bar_at = bar.bar_start
                yield StreamEvent(kind="bar", received_at=now, bar=bar)

            bars_emitted += 1

        if not self._stopped:
            self._stopped = True
            self._connection_state = "stopped"

    def stop(self) -> None:
        self._stopped = True
        self._connection_state = "stopped"

    @property
    def connection_state(self) -> str:
        return self._connection_state

    @property
    def last_heartbeat_at(self) -> pd.Timestamp | None:
        return self._last_heartbeat_at

    @property
    def last_bar_at(self) -> pd.Timestamp | None:
        return self._last_bar_at


def make_fake_bars(
    session_id: str,
    or_minutes: int = 15,
    total_bars: int = 390,
    or_high: float = 502.0,
    or_low: float = 498.0,
    breakout_bar: int | None = None,
    breakout_direction: str = "LONG",
    base_price: float = 500.0,
) -> list[RawBar]:
    """Generate a deterministic full RTH session of 1-minute RawBars for testing.

    OR bars (first or_minutes): prices oscillate between or_low and or_high.
    Trading-window bars: flat near base_price unless breakout_bar is set.
    All timestamps are ET-tz-aware starting at 09:30:00 on session_id.
    All bars have is_final=True and revision=0.

    Args:
        session_id: Date string YYYY-MM-DD.
        or_minutes: Opening range bar count.
        total_bars: Total 1-minute bars to generate.
        or_high: OR high boundary.
        or_low: OR low boundary.
        breakout_bar: 0-based index; that bar gets a breakout close price.
        breakout_direction: LONG (close > or_high+0.1) or SHORT (< or_low-0.1).
        base_price: Flat price for trading-window bars without breakout.

    Returns:
        List of RawBar instances in chronological order.
    """
    tz = "America/New_York"
    session_open = pd.Timestamp(f"{session_id} 09:30:00", tz=tz)
    fixed_received_at = pd.Timestamp(f"{session_id} 14:30:00", tz="UTC")

    bars: list[RawBar] = []
    for i in range(total_bars):
        bar_start = session_open + pd.Timedelta(minutes=i)
        bar_end = bar_start + pd.Timedelta(minutes=1)
        is_or = i < or_minutes

        if is_or:
            mid = (or_high + or_low) / 2.0
            if i % 2 == 0:
                open_ = mid
                high = or_high
                low = mid - 0.5
                close = or_high - 0.1
            else:
                open_ = mid
                high = mid + 0.5
                low = or_low
                close = or_low + 0.1
        else:
            open_ = base_price
            high = base_price + 0.1
            low = base_price - 0.1
            close = base_price

        if breakout_bar is not None and i == breakout_bar and not is_or:
            if breakout_direction == "LONG":
                close = or_high + 0.2
                high = or_high + 0.3
                open_ = base_price
                low = base_price - 0.1
            else:
                close = or_low - 0.2
                low = or_low - 0.3
                open_ = base_price
                high = base_price + 0.1

        bars.append(
            RawBar(
                symbol="SPY",
                feed="sip",
                bar_start=bar_start,
                bar_end=bar_end,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=10_000.0,
                received_at=fixed_received_at,
                revision=0,
                is_final=True,
            )
        )

    return bars
