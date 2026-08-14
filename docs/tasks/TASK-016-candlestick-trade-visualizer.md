# TASK-016: Candlestick Trade Session Visualizer

## Objective

Implement the session-level candlestick trade visualization module in `src/visualization/candlestick_plotter.py` using `mplfinance` and `matplotlib` to render high-resolution PNG charts showing the opening range box, breakout entries, stop-loss / profit-target lines, and exit events for each executed trade.

## Scope

### In Scope

- Generation of intraday 1-minute candlestick charts for trading sessions containing an executed trade.
- Visual overlays and annotations:
  - Shaded rectangular region highlighting the 15-minute Opening Range ($09:30 - 09:44$ ET).
  - Blue dashed horizontal lines for $\text{OR High}$ and $\text{OR Low}$.
  - Black solid line for $\text{Entry Price}$.
  - Red dashed line for $\text{Stop Loss}$.
  - Green dashed line for $\text{Take Profit}$ ($2R$).
  - Upward/Downward arrows indicating entry timestamp and fill price.
  - Exit marker annotating exit timestamp, exit price, realized R-multiple, and exit reason (`TARGET`, `STOP`, `EOD`).
- Batch export of trade charts to `plots/trades/trade_{trade_id}_{date}_{direction}.png`.
- Support for plotting specific trade IDs or filtering by best/worst trades.

### Out of Scope

- Time-series portfolio curve charts (handled in TASK-017).

## Dependencies

- TASK-001
- TASK-002
- TASK-004
- TASK-008
- TASK-009
- TASK-011
- TASK-013

## Requirements

1. Use `mplfinance` or customized `matplotlib` subplots to plot 1-minute OHLC candles.
2. Clearly distinguish between winning trades (green title/border) and losing trades (red title/border).
3. Annotate trade metadata on the chart canvas (Date, Symbol, Direction, Entry Time, Exit Time, Gross P&L, Realized R-Multiple).
4. Save charts as high-DPI PNGs (minimum 150 DPI) in `plots/trades/`.

## Implementation Details

1. Create `src/visualization/candlestick_plotter.py`.
2. Implement `CandlestickTradePlotter` class:
   ```python
   class CandlestickTradePlotter:
       def __init__(self, output_dir: Path = Path("plots/trades")):
           self.output_dir = output_dir

       def plot_trade(
           self,
           trade: Trade,
           session_bars: pd.DataFrame,
           save_path: Optional[Path] = None,
       ) -> Path:
           ...

       def plot_all_trades(
           self,
           trades: list[Trade],
           processed_bars: pd.DataFrame,
           max_plots: Optional[int] = None,
       ) -> list[Path]:
           ...
   ```
3. Ensure timezone awareness is formatted cleanly on x-axis tick labels (e.g. `09:30`, `10:00`, `11:00`, `16:00`).

## Interfaces / Contracts

```python
# src/visualization/candlestick_plotter.py

import pandas as pd
from pathlib import Path
from typing import List, Optional
from src.backtest.models import Trade

class CandlestickTradePlotter:
    def __init__(self, output_dir: str = "plots/trades"):
        self.output_dir = Path(output_dir)

    def plot_trade_session(
        self,
        trade: Trade,
        session_df: pd.DataFrame,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Renders session OHLC with OR box, SL/TP levels, entry/exit markers.
        Saves to plots/trades/ and returns saved file path.
        """
        ...
```

## Data / File Changes

- Create `src/visualization/candlestick_plotter.py`
- Writes image files to `plots/trades/`

## Validation

1. Pass a sample Long trade and its corresponding 1-day 1-minute bar DataFrame to `plot_trade_session()`.
2. Verify that the generated PNG exists on disk, is non-empty, and contains valid candlestick elements, shaded OR box, and SL/TP lines.
3. Pass a Short trade and verify that annotations and marker orientations adjust appropriately.

## Acceptance Criteria

- [ ] High-resolution PNGs are generated under `plots/trades/`.
- [ ] Opening range window ($09:30–09:44$) is visually highlighted.
- [ ] Entry, Stop Loss, and Take Profit levels are clearly visible with correct labels.
- [ ] Exit reason and realized R-multiple are accurately annotated.

## Notes

- Visualizing individual trade executions is the fastest way to audit strategy mechanics and spot potential execution anomalies.
