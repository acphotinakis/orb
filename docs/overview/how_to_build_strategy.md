For SPY, I would code the first version as a **clean, deterministic baseline** before adding ML or more advanced filters. That gives you something you can properly backtest and use as a benchmark.

### 1. Define the baseline strategy

Use the regular U.S. session:

* Market open: **9:30 AM ET**
* Opening range: **first 15 minutes**
* OR window: **9:30–9:45**
* Long breakout: price closes above OR High
* Short breakout: price closes below OR Low
* One trade per day
* Stop: opposite side of opening range
* Target: **2R**
* Force exit: **4:00 PM ET**
* No trades during the opening-range construction period

For example:

```text
9:30 ───────── 9:45 ───────────────────────── 4:00
       BUILD OR       │       TRADE            │
                      │                        EXIT
                      │
                 OR High ────────────────
                      │
                 OR Low  ────────────────
```

### 2. Data we need

For a proper SPY backtest, use intraday OHLCV data, preferably **1-minute bars**:

```text
timestamp
open
high
low
close
volume
```

A row might look like:

```text
2026-08-14 09:31:00
O = 641.20
H = 641.65
L = 641.10
C = 641.50
V = 183421
```

The important thing is that the data has to be sufficiently granular. Daily data cannot reproduce a 15-minute ORB.

### 3. Strategy logic

The algorithm is essentially:

```python
for each trading day:

    opening_bars = bars between 9:30 and 9:45

    or_high = max(opening_bars.high)
    or_low  = min(opening_bars.low)

    for each subsequent bar:

        if no position:

            if close > or_high:
                enter long

            elif close < or_low:
                enter short

        if long:
            stop = or_low
            target = entry + 2 * (entry - stop)

        if short:
            stop = or_high
            target = entry - 2 * (stop - entry)

        exit at:
            stop
            target
            4:00 PM
```

There is one important implementation detail: **how you handle a candle that touches both the stop and target**. With OHLCV data, you don't know the intrabar sequence. A conservative backtest should assume the less favorable outcome or use higher-frequency data.

---

## 4. A better baseline

I would actually make the strategy configurable rather than hard-code everything:

```python
ORBStrategy(
    opening_range_minutes=15,
    breakout_confirmation="close",
    breakout_buffer=0.0,
    stop_method="opposite_range",
    target_r=2.0,
    max_trades_per_day=1,
    force_exit_time="16:00",
)
```

Then we can run experiments like:

```text
ORB 5m
ORB 15m
ORB 30m
```

and:

```text
1R
1.5R
2R
3R
```

without rewriting the strategy.

---

# 5. Python implementation

A clean implementation using `pandas` could look like this:

```python
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ORBConfig:
    opening_range_minutes: int = 15
    target_r: float = 2.0
    breakout_buffer: float = 0.0
    max_trades_per_day: int = 1
    force_exit_time: str = "16:00"


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # Convert to Eastern Time
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = (
            df["timestamp"]
            .dt.tz_localize("America/New_York")
        )
    else:
        df["timestamp"] = (
            df["timestamp"]
            .dt.tz_convert("America/New_York")
        )

    df = df.sort_values("timestamp")

    return df


def backtest_orb(
    df: pd.DataFrame,
    config: ORBConfig,
) -> pd.DataFrame:

    df = prepare_data(df)

    trades = []

    df["date"] = df["timestamp"].dt.date

    for date, day in df.groupby("date"):

        day = day.copy()

        # ---------------------------------------------------------
        # Opening range
        # ---------------------------------------------------------

        market_open = pd.Timestamp(
            f"{date} 09:30",
            tz="America/New_York"
        )

        range_end = (
            market_open
            + pd.Timedelta(
                minutes=config.opening_range_minutes
            )
        )

        opening_range = day[
            (day["timestamp"] >= market_open)
            & (day["timestamp"] < range_end)
        ]

        if opening_range.empty:
            continue

        or_high = opening_range["high"].max()
        or_low = opening_range["low"].min()

        or_width = or_high - or_low

        if or_width <= 0:
            continue

        # ---------------------------------------------------------
        # Trading period
        # ---------------------------------------------------------

        trading_day = day[
            (day["timestamp"] >= range_end)
            & (
                day["timestamp"]
                < pd.Timestamp(
                    f"{date} {config.force_exit_time}",
                    tz="America/New_York"
                )
            )
        ]

        position = None
        trades_today = 0

        entry_price = None
        stop_price = None
        target_price = None
        entry_time = None

        for _, bar in trading_day.iterrows():

            if trades_today >= config.max_trades_per_day:
                break

            high = bar["high"]
            low = bar["low"]
            close = bar["close"]
            timestamp = bar["timestamp"]

            # -----------------------------------------------------
            # Entry
            # -----------------------------------------------------

            if position is None:

                long_breakout = (
                    close
                    > or_high + config.breakout_buffer
                )

                short_breakout = (
                    close
                    < or_low - config.breakout_buffer
                )

                if long_breakout:

                    position = "LONG"

                    entry_price = close
                    entry_time = timestamp

                    stop_price = or_low

                    risk = entry_price - stop_price

                    if risk <= 0:
                        position = None
                        continue

                    target_price = (
                        entry_price
                        + config.target_r * risk
                    )

                    trades_today += 1

                elif short_breakout:

                    position = "SHORT"

                    entry_price = close
                    entry_time = timestamp

                    stop_price = or_high

                    risk = stop_price - entry_price

                    if risk <= 0:
                        position = None
                        continue

                    target_price = (
                        entry_price
                        - config.target_r * risk
                    )

                    trades_today += 1

                continue

            # -----------------------------------------------------
            # Long exit
            # -----------------------------------------------------

            if position == "LONG":

                stop_hit = low <= stop_price
                target_hit = high >= target_price

                if stop_hit and target_hit:
                    # Conservative assumption
                    exit_price = stop_price
                    exit_reason = "STOP"

                elif stop_hit:
                    exit_price = stop_price
                    exit_reason = "STOP"

                elif target_hit:
                    exit_price = target_price
                    exit_reason = "TARGET"

                else:
                    continue

                pnl = exit_price - entry_price

                trades.append({
                    "date": date,
                    "direction": "LONG",
                    "entry_time": entry_time,
                    "exit_time": timestamp,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "stop_price": stop_price,
                    "target_price": target_price,
                    "or_high": or_high,
                    "or_low": or_low,
                    "or_width": or_width,
                    "pnl": pnl,
                    "r_multiple": pnl / (
                        entry_price - stop_price
                    ),
                    "exit_reason": exit_reason,
                })

                position = None
                continue

            # -----------------------------------------------------
            # Short exit
            # -----------------------------------------------------

            if position == "SHORT":

                stop_hit = high >= stop_price
                target_hit = low <= target_price

                if stop_hit and target_hit:
                    exit_price = stop_price
                    exit_reason = "STOP"

                elif stop_hit:
                    exit_price = stop_price
                    exit_reason = "STOP"

                elif target_hit:
                    exit_price = target_price
                    exit_reason = "TARGET"

                else:
                    continue

                pnl = entry_price - exit_price

                trades.append({
                    "date": date,
                    "direction": "SHORT",
                    "entry_time": entry_time,
                    "exit_time": timestamp,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "stop_price": stop_price,
                    "target_price": target_price,
                    "or_high": or_high,
                    "or_low": or_low,
                    "or_width": or_width,
                    "pnl": pnl,
                    "r_multiple": pnl / (
                        stop_price - entry_price
                    ),
                    "exit_reason": exit_reason,
                })

                position = None

        # ---------------------------------------------------------
        # Force EOD exit
        # ---------------------------------------------------------

        if position is not None:

            last_bar = trading_day.iloc[-1]

            exit_price = last_bar["close"]
            exit_time = last_bar["timestamp"]

            if position == "LONG":

                pnl = exit_price - entry_price
                risk = entry_price - stop_price

            else:

                pnl = entry_price - exit_price
                risk = stop_price - entry_price

            trades.append({
                "date": date,
                "direction": position,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "or_high": or_high,
                "or_low": or_low,
                "or_width": or_width,
                "pnl": pnl,
                "r_multiple": pnl / risk,
                "exit_reason": "EOD",
            })

    return pd.DataFrame(trades)
```

Then:

```python
config = ORBConfig(
    opening_range_minutes=15,
    target_r=2.0,
)

trades = backtest_orb(
    spy_data,
    config,
)

print(trades)
```

---

# 6. Then calculate performance

Don't stop at total P&L.

I'd calculate:

```python
def performance_summary(trades):

    if trades.empty:
        return {}

    returns = trades["r_multiple"]

    return {
        "trades": len(trades),
        "win_rate": (returns > 0).mean(),
        "avg_R": returns.mean(),
        "median_R": returns.median(),
        "total_R": returns.sum(),
        "profit_factor": (
            returns[returns > 0].sum()
            / abs(returns[returns < 0].sum())
        ),
        "best_trade_R": returns.max(),
        "worst_trade_R": returns.min(),
    }
```

This lets us answer:

> Does SPY ORB actually have positive expectancy?

rather than simply:

> Did this particular backtest make money?

---

# 7. The next version I'd build

Once the baseline works, I'd add features **one at a time**:

### Version 1 — Baseline

```text
15-minute ORB
       ↓
Breakout
       ↓
2R target
       ↓
Opposite-side stop
```

### Version 2 — Volume

```text
ORB breakout
+
RVOL > threshold
```

### Version 3 — VWAP

```text
Long:
break OR High
+
price > VWAP

Short:
break OR Low
+
price < VWAP
```

### Version 4 — Market regime

```text
SPY ORB
+
ATR regime
+
market trend
+
volatility regime
```

### Version 5 — Multi-feature model

```text
             ORB
              │
       ┌──────┼──────┐
       ↓      ↓      ↓
      Gap    RVOL   VWAP
       ↓      ↓      ↓
       └──────┼──────┘
              ↓
          ML Model
              ↓
       P(successful ORB)
              ↓
         Risk Engine
```

That last version is where I would eventually take this if the objective is **quantitative research rather than simply building a TradingView-style indicator**.

The important thing is to **freeze Version 1 first**. Establish a trustworthy baseline, then add filters and determine whether each modification improves **out-of-sample expectancy**, rather than optimizing everything simultaneously.
