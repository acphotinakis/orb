#!/usr/bin/env python3
"""
Fetch 1-minute historical bars for AAPL (or any symbol) for the past 90 days
using the official Alpaca Data API (`alpaca-py`).
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
except ImportError:
    print(
        "Error: 'alpaca-py' is not installed. Please install dependencies with:\n"
        "  pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)


def get_alpaca_credentials(ispaper: bool) -> tuple[str, str]:
    """Load and validate Alpaca API credentials from environment or .env file."""
    load_dotenv()

    if ispaper:
        api_key = (
            os.getenv("PAPER_APCA_API_KEY_ID")
        )
        secret_key = (
            os.getenv("PAPER_APCA_API_SECRET_KEY")
        )
    else:
        api_key = (
            os.getenv("APCA_API_KEY_ID")
        )
        secret_key = (
            os.getenv("APCA_API_SECRET_KEY")
        )

    missing = []
    if not api_key:
        missing.append("ALPACA_API_KEY (or ALPACA_KEY / APCA_API_KEY_ID)")
    if not secret_key:
        missing.append("ALPACA_SECRET_KEY (or ALPACA_SECRET / APCA_API_SECRET_KEY)")

    if missing:
        print(
            f"Error: Missing Alpaca API credentials in environment or .env:\n  - "
            + "\n  - ".join(missing),
            file=sys.stderr,
        )
        print(
            "\nPlease ensure your .env file contains:\n"
            "ALPACA_API_KEY=your_api_key_here\n"
            "ALPACA_SECRET_KEY=your_secret_key_here\n",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"API_KEY = {api_key}")
    print(f"API_SECRET_KEY = {secret_key}")

    return api_key, secret_key


def fetch_historical_bars(
    symbol: str = "AAPL",
    days: int = 90,
    feed: str = "iex",
    output_file: str | None = None,
    ispaper: bool = True
):
    """
    Fetches 1-minute bars for the specified symbol over the past `days` days.
    """
    print(f"ispaper = {ispaper}")
    api_key, secret_key = get_alpaca_credentials(ispaper)

    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)

    # end_time = datetime.now(timezone.utc)
    # start_time = end_time - timedelta(days=days)
    end_time = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_time = end_time - timedelta(days=days)

    feed_enum = DataFeed.SIP if feed.lower() == "sip" else DataFeed.IEX

    print(f"Fetching 1-minute bars for {symbol}...")
    print(f"Range: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')} to {end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Data Feed: {feed_enum.value.upper()}")

    request_params = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start_time,
        end=end_time,
        feed=feed_enum,
    )

    try:
        bars = client.get_stock_bars(request_params)
    except Exception as e:
        print(f"API Error while requesting stock bars: {e}", file=sys.stderr)
        sys.exit(1)

    df = bars.df

    if df.empty:
        print(f"No bars returned for {symbol} within the specified timeframe.")
        return df

    # Reset MultiIndex (symbol, timestamp) to standard columns
    df = df.reset_index()

    # Convert timestamp column to UTC / clean datetime
    df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")

    # Sort in chronological order
    df = df.sort_values(by="timestamp").reset_index(drop=True)

    print(f"\nSuccessfully retrieved {len(df):,} bars for {symbol}.")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print("\nSample Data (First 5 bars):")
    print(df.head())
    print("\nSample Data (Last 5 bars):")
    print(df.tail())

    # Save to CSV if requested
    if output_file:
        df.to_csv(output_file, index=False)
        print(f"\nSaved historical data to: {output_file}")

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Fetch 1-minute historical bars for a stock symbol from Alpaca API for the past N days."
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="AAPL",
        help="Stock ticker symbol (default: AAPL)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of days in the past to fetch (default: 90)",
    )
    parser.add_argument(
        "--feed",
        type=str,
        choices=["iex", "sip"],
        default="iex",
        help="Market data feed: 'iex' (free tier default) or 'sip' (paid unlimited plan)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="aapl_1min_90d.csv",
        help="Output CSV file path (default: aapl_1min_90d.csv)",
    )
    parser.add_argument(
        "--ispaper",
        type=bool,
        default=True,
        help="Decides whether or not to use the paper trading keys/secret",
    )


    args = parser.parse_args()

    fetch_historical_bars(
        symbol=args.symbol.upper(),
        days=args.days,
        feed=args.feed,
        output_file=args.output,
        ispaper=args.ispaper
    )


if __name__ == "__main__":
    main()
