"""
src.backtest.engine
===================
Event-driven sequential backtesting engine & conservative dual-touch resolver.

Replays 1-minute historical bars session-by-session, manages position state
transitions, enforces conservative dual-touch stop/target resolution (stopping
out first if both levels are touched in a single bar), handles 15:59:00 ET
force-flattening, and produces trade ledgers and minute-by-minute equity curves.

Timestamp semantics (P1-O4)
---------------------------
Bar ``timestamp`` values are bar-OPEN times.  Entry/exit decisions evaluate a
bar's CLOSE, so a decision recorded against a bar was available one bar
duration later (availability = bar start + timeframe duration), never at bar
open.  Recorded ``entry_time``/``exit_time`` identify the deciding bar; the
availability instant is derived, not stored.  See
``docs/dashboard-analysis/validation/phase-01.md`` (P1-O4) for the supported
calendar/timeframe policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

from src.common.config import AppConfig
from src.backtest.models import Position, Trade, PositionSide, ExitReason
from src.backtest.execution_model import ExecutionModel
from src.strategy.opening_range import OpeningRangeCalculator, OpeningRange
from src.strategy.signals import SignalGenerator, Signal
from src.common.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestResult:
    """Container holding all backtest execution artifacts."""

    trades: List[Trade]
    trades_df: pd.DataFrame
    equity_curve: pd.DataFrame
    daily_summary: pd.DataFrame
    initial_capital: float
    final_capital: float
    cancelled: bool = False


class BacktestEngine:
    """Sequential bar-by-bar backtest simulation engine."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.or_calculator = OpeningRangeCalculator(
            or_minutes=config.strategy.opening_range_minutes,
            timeframe=config.data.timeframe,
        )
        self.signal_generator = SignalGenerator(config=config.strategy)
        self.execution_model = ExecutionModel(config=config.execution)

    def run(
        self,
        df_processed: pd.DataFrame,
        *,
        progress: Optional[callable] = None,
        cancel_requested: Optional[callable] = None,
    ) -> BacktestResult:
        """Executes full backtest across all sessions in df_processed.

        Args:
            df_processed: Canonical processed bars.
            progress: Optional ``event -> None`` callback invoked at session
                boundaries (``session_started``/``session_completed`` with
                completed/total counts).  ``None`` preserves plain CLI use.
            cancel_requested: Optional ``() -> bool`` polled between sessions
                and every 64 bars.  When true at a boundary, the run stops and
                the partial result is returned with ``cancelled=True`` on the
                result container (trades/equity so far are retained, capital
                reflects only closed trades).
        """
        if df_processed.empty:
            logger.warning("Empty DataFrame passed to BacktestEngine.run().")
            return self._empty_result()

        # Sort dataset chronologically
        df = df_processed.sort_values("timestamp").reset_index(drop=True)
        session_groups = df.groupby("session_id", sort=False)

        current_capital = self.config.execution.initial_capital
        all_trades: List[Trade] = []
        equity_records: List[dict] = []
        daily_records: List[dict] = []
        trade_id_counter = 1
        cancelled = False

        def _emit(event: dict) -> None:
            if progress is None:
                return
            try:
                progress(event)
            except Exception as exc:
                logger.warning("Progress callback failed (%s); continuing.", exc)

        def _cancelled() -> bool:
            try:
                return bool(cancel_requested and cancel_requested())
            except Exception as exc:
                logger.warning("Cancel hook failed (%s); continuing.", exc)
                return False

        total_sessions = session_groups.ngroups
        completed_sessions = 0
        for session_id, session_bars in session_groups:
            session_id_str = str(session_id)
            if _cancelled():
                cancelled = True
                logger.info("Cancellation acknowledged before session %s.", session_id_str)
                break
            _emit({"event_type": "session_started", "session_id": session_id_str,
                   "completed_sessions": completed_sessions, "total_sessions": total_sessions})
            session_bars = session_bars.sort_values("timestamp").reset_index(drop=True)

            # 1. Opening Range phase: Compute & Freeze OR
            or_obj = self.or_calculator.calculate_session_or(session_bars)
            if not or_obj.is_valid:
                logger.warning(
                    "Session %s has invalid OR; skipping session trading.",
                    session_id_str,
                )
                # Still record equity curve as flat
                for _, bar in session_bars.iterrows():
                    equity_records.append(
                        {
                            "timestamp": bar["timestamp"],
                            "session_id": session_id_str,
                            "cash": current_capital,
                            "position_value": 0.0,
                            "equity": current_capital,
                        }
                    )
                daily_records.append(
                    {
                        "date": session_id_str,
                        "trades_count": 0,
                        "daily_pnl": 0.0,
                        "ending_equity": current_capital,
                    }
                )
                continue

            # 2. Sequential bar replay
            active_position: Optional[Position] = None
            session_trades_count = 0
            session_start_capital = current_capital
            max_trades = self.config.strategy.max_trades_per_day

            # Replay all bars in the session
            for bar_num, (_, bar) in enumerate(session_bars.iterrows()):
                if bar_num % 64 == 0 and _cancelled():
                    cancelled = True
                    logger.info(
                        "Cancellation acknowledged at bar %d of session %s.",
                        bar_num, session_id_str,
                    )
                    break
                ts = pd.Timestamp(bar["timestamp"])
                high_p = float(bar["high"])
                low_p = float(bar["low"])
                close_p = float(bar["close"])
                is_or = bool(bar.get("is_opening_range", False))
                is_fe = bool(bar.get("is_force_exit", False))

                # Check if we have an active position to evaluate
                if active_position is not None:
                    # Evaluate in-position bracket orders on this bar
                    active_position, closed_trade = self._evaluate_position_bar(
                        bar=bar,
                        position=active_position,
                    )
                    if closed_trade is not None:
                        all_trades.append(closed_trade)
                        current_capital += closed_trade.pnl_dollars
                        active_position = None

                # If flat and inside trading window (and haven't exceeded daily trade limit)
                elif not is_or and not is_fe and session_trades_count < max_trades:
                    # Check for breakout signal on bar close
                    # We evaluate bar breakout against frozen OR
                    signal = self._check_signal_at_bar(bar, or_obj)
                    if signal is not None:
                        # Attempt entry execution
                        pos = self._enter_position(
                            signal=signal,
                            capital=current_capital,
                            trade_id=trade_id_counter,
                            bar=bar,
                        )
                        if pos is not None:
                            active_position = pos
                            session_trades_count += 1
                            trade_id_counter += 1

                # Record minute-by-minute equity mark-to-market
                unrealized_pnl = 0.0
                if active_position is not None:
                    if active_position.side == PositionSide.LONG:
                        unrealized_pnl = (
                            close_p - active_position.entry_price
                        ) * active_position.shares
                    elif active_position.side == PositionSide.SHORT:
                        unrealized_pnl = (
                            active_position.entry_price - close_p
                        ) * active_position.shares

                mtm_equity = current_capital + unrealized_pnl
                equity_records.append(
                    {
                        "timestamp": ts,
                        "session_id": session_id_str,
                        "cash": current_capital,
                        "position_value": unrealized_pnl,
                        "equity": mtm_equity,
                    }
                )

            # End of session audit: Ensure no position left open
            if active_position is not None:
                last_bar = session_bars.iloc[-1]
                closed_trade = self._force_close_position(
                    position=active_position,
                    bar=last_bar,
                    reason=ExitReason.EOD,
                )
                all_trades.append(closed_trade)
                current_capital += closed_trade.pnl_dollars
                active_position = None
                # P1-A4: the fallback close executes after the last per-bar
                # mark, so record the flattened state explicitly. Without this,
                # ending equity disagrees with final capital (U1).
                equity_records.append(
                    {
                        "timestamp": pd.Timestamp(last_bar["timestamp"]),
                        "session_id": session_id_str,
                        "cash": current_capital,
                        "position_value": 0.0,
                        "equity": current_capital,
                    }
                )

            daily_pnl = current_capital - session_start_capital
            daily_records.append(
                {
                    "date": session_id_str,
                    "trades_count": session_trades_count,
                    "daily_pnl": daily_pnl,
                    "ending_equity": current_capital,
                }
            )
            if cancelled:
                # Partial session retained as computed; not counted complete.
                break
            completed_sessions += 1
            _emit({"event_type": "session_completed", "session_id": session_id_str,
                   "completed_sessions": completed_sessions, "total_sessions": total_sessions,
                   "trades_so_far": len(all_trades)})

        # Assemble Output DataFrames
        trades_df = (
            pd.DataFrame([t.to_dict() for t in all_trades])
            if all_trades
            else pd.DataFrame(
                columns=[
                    "trade_id",
                    "date",
                    "symbol",
                    "direction",
                    "entry_time",
                    "exit_time",
                    "entry_price",
                    "exit_price",
                    "stop_price",
                    "target_price",
                    "or_high",
                    "or_low",
                    "or_width",
                    "shares",
                    "pnl_dollars",
                    "return_pct",
                    "r_multiple",
                    "exit_reason",
                    "slippage_paid",
                    "commission_paid",
                ]
            )
        )
        equity_df = pd.DataFrame(equity_records)
        daily_df = pd.DataFrame(daily_records)

        return BacktestResult(
            trades=all_trades,
            trades_df=trades_df,
            equity_curve=equity_df,
            daily_summary=daily_df,
            initial_capital=self.config.execution.initial_capital,
            final_capital=current_capital,
            cancelled=cancelled,
        )

    def _check_signal_at_bar(
        self,
        bar: pd.Series,
        opening_range: OpeningRange,
    ) -> Optional[Signal]:
        """Evaluates single bar close against OR boundaries."""
        close_p = float(bar["close"])
        ts = pd.Timestamp(bar["timestamp"])
        symbol = self.config.strategy.ticker

        or_high = opening_range.or_high
        or_low = opening_range.or_low
        or_width = opening_range.or_width
        buffer_val = or_width * self.config.strategy.breakout_buffer_pct
        target_r = self.config.strategy.target_r
        direction_mode = self.config.strategy.direction_mode

        # Long breakout
        if direction_mode in ("both", "long_only") and close_p > (or_high + buffer_val):
            stop_loss = or_low
            risk_amt = close_p - stop_loss
            if risk_amt <= 0:
                return None
            tp = close_p + (target_r * risk_amt)
            return Signal(
                session_id=opening_range.session_id,
                timestamp=ts,
                symbol=symbol,
                direction="LONG",
                entry_price=close_p,
                stop_loss=stop_loss,
                take_profit=tp,
                risk_amount=risk_amt,
                or_high=or_high,
                or_low=or_low,
                or_width=or_width,
            )

        # Short breakout
        elif direction_mode in ("both", "short_only") and close_p < (
            or_low - buffer_val
        ):
            stop_loss = or_high
            risk_amt = stop_loss - close_p
            if risk_amt <= 0:
                return None
            tp = close_p - (target_r * risk_amt)
            return Signal(
                session_id=opening_range.session_id,
                timestamp=ts,
                symbol=symbol,
                direction="SHORT",
                entry_price=close_p,
                stop_loss=stop_loss,
                take_profit=tp,
                risk_amount=risk_amt,
                or_high=or_high,
                or_low=or_low,
                or_width=or_width,
            )

        return None

    def _enter_position(
        self,
        signal: Signal,
        capital: float,
        trade_id: int,
        bar: pd.Series,
    ) -> Optional[Position]:
        """Calculates sizing and creates active Position with execution frictions."""
        shares = self.execution_model.calculate_position_size(
            capital=capital,
            entry_price=signal.entry_price,
            stop_price=signal.stop_loss,
        )
        if shares <= 0:
            logger.debug("Position size 0 calculated; skipping trade entry.")
            return None

        fill_price, entry_slip, entry_comm = (
            self.execution_model.calculate_entry_execution(
                direction=signal.direction,
                price=signal.entry_price,
                shares=shares,
            )
        )

        side = PositionSide.LONG if signal.direction == "LONG" else PositionSide.SHORT
        initial_risk_per_share = abs(fill_price - signal.stop_loss)

        return Position(
            trade_id=trade_id,
            date=signal.session_id,
            symbol=signal.symbol,
            side=side,
            entry_time=signal.timestamp,
            entry_price=fill_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            shares=shares,
            initial_risk_per_share=initial_risk_per_share,
            or_high=signal.or_high,
            or_low=signal.or_low,
            or_width=signal.or_width,
        )

    def _evaluate_position_bar(
        self,
        bar: pd.Series,
        position: Position,
    ) -> Tuple[Optional[Position], Optional[Trade]]:
        """Evaluates bar high/low against position TP and SL brackets.

        Conservative Dual-Touch Rule:
        If both Stop Loss and Take Profit levels are touched in the same bar,
        STOP LOSS is always assumed to have occurred first.
        """
        ts = pd.Timestamp(bar["timestamp"])
        high_p = float(bar["high"])
        low_p = float(bar["low"])
        is_fe = bool(bar.get("is_force_exit", False))

        # Check Force Exit (15:59:00 ET)
        if is_fe:
            closed = self._force_close_position(position, bar, ExitReason.EOD)
            return None, closed

        if position.side == PositionSide.LONG:
            hit_stop = low_p <= position.stop_loss
            hit_target = high_p >= position.take_profit

            # Dual touch or pure Stop -> STOP LOSS FIRST
            if hit_stop and hit_target:
                logger.debug(
                    "Dual-touch bar detected on LONG position; resolving conservatively to STOP."
                )
                closed = self._execute_exit(
                    position, ts, position.stop_loss, ExitReason.STOP
                )
                return None, closed
            elif hit_stop:
                closed = self._execute_exit(
                    position, ts, position.stop_loss, ExitReason.STOP
                )
                return None, closed
            elif hit_target:
                closed = self._execute_exit(
                    position, ts, position.take_profit, ExitReason.TARGET
                )
                return None, closed

        elif position.side == PositionSide.SHORT:
            hit_stop = high_p >= position.stop_loss
            hit_target = low_p <= position.take_profit

            # Dual touch or pure Stop -> STOP LOSS FIRST
            if hit_stop and hit_target:
                logger.debug(
                    "Dual-touch bar detected on SHORT position; resolving conservatively to STOP."
                )
                closed = self._execute_exit(
                    position, ts, position.stop_loss, ExitReason.STOP
                )
                return None, closed
            elif hit_stop:
                closed = self._execute_exit(
                    position, ts, position.stop_loss, ExitReason.STOP
                )
                return None, closed
            elif hit_target:
                closed = self._execute_exit(
                    position, ts, position.take_profit, ExitReason.TARGET
                )
                return None, closed

        return position, None

    def _execute_exit(
        self,
        position: Position,
        exit_time: pd.Timestamp,
        price: float,
        reason: ExitReason,
    ) -> Trade:
        """Applies exit slippage/commissions and closes position."""
        fill_price, exit_slip, exit_comm = (
            self.execution_model.calculate_exit_execution(
                direction=position.side.value,
                price=price,
                shares=position.shares,
                reason=reason,
            )
        )

        # Accrue total entry + exit slippage and commissions
        # Entry slippage
        entry_slip = self.config.execution.slippage_per_share * position.shares
        total_slippage = entry_slip + exit_slip
        total_commission = self.execution_model.calculate_total_commission(
            position.shares
        )

        return position.close(
            exit_time=exit_time,
            exit_price=fill_price,
            reason=reason,
            slippage_paid=total_slippage,
            commission_paid=total_commission,
        )

    def _force_close_position(
        self,
        position: Position,
        bar: pd.Series,
        reason: ExitReason = ExitReason.EOD,
    ) -> Trade:
        """Force-closes position at bar close price (e.g. at 15:59 ET)."""
        ts = pd.Timestamp(bar["timestamp"])
        close_p = float(bar["close"])
        return self._execute_exit(position, ts, close_p, reason)

    def _empty_result(self) -> BacktestResult:
        """Returns empty BacktestResult structure."""
        init_cap = self.config.execution.initial_capital
        return BacktestResult(
            trades=[],
            trades_df=pd.DataFrame(),
            equity_curve=pd.DataFrame(),
            daily_summary=pd.DataFrame(),
            initial_capital=init_cap,
            final_capital=init_cap,
        )
