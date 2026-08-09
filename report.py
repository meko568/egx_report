#!/usr/bin/env python3
"""
EGX Halal Daily Stock Report
Fetches halal-compliant EGX stock data from Yahoo Finance and sends to Telegram.
"""

import os
import sys
import requests
import yfinance as yf
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ─── Halal Whitelist: EGX tickers (Yahoo Finance format .CA) ───
# Verified sectors: real estate, food/consumer, telecom, industrials, healthcare, tech
# Excluded: banks, insurance, alcohol, gambling, conventional finance
HALAL_TICKERS = [
    "FWRY.CA",   # Fawry - FinTech/Payments (tech)
    "PHDC.CA",   # Palm Hills - Real Estate Development
    "JUFO.CA",   # Juhayna - Food & Beverage (dairy, juice)
    "ORHD.CA",   # Orascom Development - Real Estate/Tourism
    "CLHO.CA",   # Cleopatra Hospitals - Healthcare
    "MFPC.CA",   # Misr Fertilizers (Sinai Cement) - Industrials/Materials
    "EFID.CA",   # Edita Food Industries - Food/Consumer
    "ETEL.CA",   # Telecom Egypt - Telecommunications
    # HRHO.CA excluded - financial holding (conventional finance)
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def fetch_stock_data(ticker: str) -> Optional[Dict]:
    """
    Fetch daily close, previous close, % change, and volume for a ticker.
    Returns None if data unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        # Use 5d to ensure we get at least 2 trading days (2d often returns only 1 row)
        hist = stock.history(period="5d", interval="1d")

        if hist.empty or len(hist) < 2:
            return None

        # Most recent day (today or latest trading day)
        latest = hist.iloc[-1]
        previous = hist.iloc[-2]

        current_close = float(latest["Close"])
        prev_close = float(previous["Close"])
        volume = int(latest["Volume"]) if "Volume" in latest else 0

        pct_change = ((current_close - prev_close) / prev_close) * 100 if prev_close != 0 else 0.0

        return {
            "ticker": ticker,
            "price": current_close,
            "prev_close": prev_close,
            "change_pct": pct_change,
            "volume": volume,
        }
    except Exception as e:
        print(f"[WARN] Failed to fetch {ticker}: {e}", file=sys.stderr)
        return None


def build_report(all_data: List[Dict]) -> str:
    """Build the Telegram Markdown report."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"📊 *EGX Halal Daily Report* — {today}",
        "",
        "📋 *Watchlist*",
    ]

    # Section 1: Watchlist (all whitelist stocks)
    for d in all_data:
        ticker = d["ticker"].replace(".CA", "")
        price = f"{d['price']:.2f}"
        change = f"{d['change_pct']:+.2f}%"
        vol = f"{d['volume']:,}" if d['volume'] > 0 else "N/A"
        emoji = "🟢" if d['change_pct'] >= 0 else "🔴"
        lines.append(f"{emoji} `{ticker}`: {price} EGP ({change}) | Vol: {vol}")

    # Section 2: Top 3 Gainers
    sorted_data = sorted(all_data, key=lambda x: x["change_pct"], reverse=True)
    gainers = [d for d in sorted_data if d["change_pct"] > 0][:3]

    lines.extend(["", "📈 *Top Gainers*"])
    if gainers:
        for i, d in enumerate(gainers, 1):
            ticker = d["ticker"].replace(".CA", "")
            price = f"{d['price']:.2f}"
            change = f"{d['change_pct']:+.2f}%"
            lines.append(f"{i}. `{ticker}`: {price} EGP ({change})")
    else:
        lines.append("No gainers today.")

    # Section 3: Top 3 Losers
    losers = [d for d in sorted_data if d["change_pct"] < 0][-3:]
    losers.reverse()  # Most negative first

    lines.extend(["", "📉 *Top Losers*"])
    if losers:
        for i, d in enumerate(losers, 1):
            ticker = d["ticker"].replace(".CA", "")
            price = f"{d['price']:.2f}"
            change = f"{d['change_pct']:+.2f}%"
            lines.append(f"{i}. `{ticker}`: {price} EGP ({change})")
    else:
        lines.append("No losers today.")

    # Disclaimer
    lines.extend(["", "⚠️ *Not financial advice. Data only.*"])

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """Send message to Telegram via Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            print(f"[ERROR] Telegram API error: {result}", file=sys.stderr)
            return False
        print("[INFO] Report sent to Telegram successfully")
        return True
    except requests.RequestException as e:
        print(f"[ERROR] Failed to send Telegram message: {e}", file=sys.stderr)
        return False


def main() -> int:
    print(f"[INFO] Starting EGX Halal Daily Report for {len(HALAL_TICKERS)} tickers...")

    all_data = []
    failed = []

    for ticker in HALAL_TICKERS:
        print(f"[INFO] Fetching {ticker}...")
        data = fetch_stock_data(ticker)
        if data:
            all_data.append(data)
        else:
            failed.append(ticker)
            all_data.append({
                "ticker": ticker,
                "price": 0.0,
                "prev_close": 0.0,
                "change_pct": 0.0,
                "volume": 0,
            })

    if failed:
        print(f"[WARN] Failed to fetch data for: {', '.join(failed)}", file=sys.stderr)

    if not all_data:
        print("[ERROR] No data retrieved for any ticker", file=sys.stderr)
        return 1

    report = build_report(all_data)
    print("[INFO] Report built, sending to Telegram...")

    success = send_telegram(report)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())