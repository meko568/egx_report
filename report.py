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
    "TMGH.CA",   # Talaat Moustafa Group - طلعت مصطفى
    "EIPICO.CA", # EIPICO - ايبيكو (verify ticker resolves)
    "OLFI.CA",   # Obour Land - عبور لاند
    "ISPH.CA",   # Ibnsina Pharma - ابن سينا
    # HRHO.CA excluded - financial holding (conventional finance)
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # fallback single-chat mode (legacy/testing)


def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Standard 14-period RSI using Wilder's smoothing."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def fetch_stock_data(ticker: str) -> Optional[Dict]:
    """
    Fetch daily close, previous close, % change, volume, RSI, 52wk range
    position, and PE/target-price info for a ticker.
    Returns None if data unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        # 3mo history so RSI(14) has enough data points
        hist = stock.history(period="3mo", interval="1d")

        if hist.empty or len(hist) < 2:
            return None

        closes = hist["Close"].tolist()
        current_close = float(closes[-1])
        prev_close = float(closes[-2])
        volume = int(hist["Volume"].tolist()[-1])

        pct_change = ((current_close - prev_close) / prev_close) * 100 if prev_close != 0 else 0.0
        rsi = calc_rsi(closes)

        info = {}
        try:
            info = stock.info
        except Exception:
            info = {}

        wk_high = info.get("fiftyTwoWeekHigh")
        wk_low = info.get("fiftyTwoWeekLow")
        if wk_high and wk_low and wk_high != wk_low:
            range_pos = round(((current_close - wk_low) / (wk_high - wk_low)) * 100)
            range_str = f"{range_pos}% of 52wk range"
        else:
            range_str = "N/A"

        fwd_pe = info.get("forwardPE") or info.get("trailingPE")
        pe_str = f"{fwd_pe:.1f}" if fwd_pe else "N/A"

        target = info.get("targetMeanPrice")
        target_str = f"{target:.2f}" if target else "N/A"

        return {
            "ticker": ticker,
            "price": current_close,
            "prev_close": prev_close,
            "change_pct": pct_change,
            "volume": volume,
            "rsi": rsi,
            "range_str": range_str,
            "pe_str": pe_str,
            "target_str": target_str,
        }
    except Exception as e:
        print(f"[WARN] Failed to fetch {ticker}: {e}", file=sys.stderr)
        return None


def format_line(d: Dict, prefix: str = "") -> str:
    ticker = d["ticker"].replace(".CA", "")
    price = f"{d['price']:.2f}"
    change = f"{d['change_pct']:+.2f}%"
    vol = f"{d['volume']:,}" if d.get("volume") else "N/A"
    rsi = f"{d['rsi']}" if d.get("rsi") is not None else "N/A"
    emoji = "🟢" if d["change_pct"] >= 0 else "🔴"
    line1 = f"{prefix}{emoji} `{ticker}`: {price} EGP ({change})"
    line2 = f"    Vol: {vol} | RSI: {rsi} | {d.get('range_str', 'N/A')}"
    line3 = f"    P/E: {d.get('pe_str', 'N/A')} | Target: {d.get('target_str', 'N/A')}"
    return "\n".join([line1, line2, line3])


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
        lines.append(format_line(d))

    # Section 2: Top 3 Gainers
    sorted_data = sorted(all_data, key=lambda x: x["change_pct"], reverse=True)
    gainers = [d for d in sorted_data if d["change_pct"] > 0][:3]

    lines.extend(["", "📈 *Top Gainers*"])
    if gainers:
        for i, d in enumerate(gainers, 1):
            lines.append(format_line(d, prefix=f"{i}. "))
    else:
        lines.append("No gainers today.")

    # Section 3: Top 3 Losers
    losers = [d for d in sorted_data if d["change_pct"] < 0][-3:]
    losers.reverse()  # Most negative first

    lines.extend(["", "📉 *Top Losers*"])
    if losers:
        for i, d in enumerate(losers, 1):
            lines.append(format_line(d, prefix=f"{i}. "))
    else:
        lines.append("No losers today.")

    # Disclaimer
    lines.extend(["", "⚠️ *Not financial advice. Data only.*"])

    return "\n".join(lines)


def send_telegram(message: str, chat_id: Optional[str] = None) -> bool:
    """Send message to Telegram via Bot API. Splits into chunks under 4096 chars."""
    target = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target:
        print("[ERROR] TELEGRAM_BOT_TOKEN or chat_id not set", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i + 4000] for i in range(0, len(message), 4000)] or [message]

    ok_all = True
    for chunk in chunks:
        payload = {
            "chat_id": target,
            "text": chunk,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("ok"):
                print(f"[ERROR] Telegram API error for {target}: {result}", file=sys.stderr)
                ok_all = False
        except requests.RequestException as e:
            print(f"[ERROR] Failed to send Telegram message to {target}: {e}", file=sys.stderr)
            ok_all = False

    return ok_all


def get_active_subscribers() -> Dict[str, List[str]]:
    """Return {telegram_id: [tickers]} for users with subscribed=TRUE and expiry not passed."""
    import pymysql

    conn = pymysql.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        database=os.environ["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
    )
    watchlists: Dict[str, List[str]] = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE subscribed=TRUE AND expiry >= CURDATE()")
            ids = [r["telegram_id"] for r in cur.fetchall()]
            for tg_id in ids:
                cur.execute("SELECT ticker FROM watchlist WHERE telegram_id=%s", (tg_id,))
                watchlists[tg_id] = [r["ticker"] for r in cur.fetchall()]
    finally:
        conn.close()
    return watchlists


def main() -> int:
    # Legacy/manual mode: no DB env vars set -> broadcast full report to TELEGRAM_CHAT_ID
    if not os.environ.get("DB_USER"):
        print(f"[INFO] DB not configured — legacy single-chat mode, {len(HALAL_TICKERS)} tickers...")
        all_data = [d for d in (fetch_stock_data(t) for t in HALAL_TICKERS) if d]
        if not all_data:
            print("[ERROR] No data retrieved for any ticker", file=sys.stderr)
            return 1
        report = build_report(all_data)
        return 0 if send_telegram(report) else 1

    # Normal mode: per-subscriber personalized reports
    print("[INFO] Fetching active subscribers from DB...")
    watchlists = get_active_subscribers()
    if not watchlists:
        print("[INFO] No active subscribers today.")
        return 0

    all_tickers = sorted({t for lst in watchlists.values() for t in lst})
    print(f"[INFO] Fetching {len(all_tickers)} unique tickers for {len(watchlists)} subscribers...")
    cache: Dict[str, Dict] = {}
    for ticker in all_tickers:
        data = fetch_stock_data(ticker)
        if data:
            cache[ticker] = data
        else:
            print(f"[WARN] Failed to fetch {ticker}", file=sys.stderr)

    ok_all = True
    for tg_id, tickers in watchlists.items():
        user_data = [cache[t] for t in tickers if t in cache]
        if not user_data:
            continue
        report = build_report(user_data)
        if not send_telegram(report, chat_id=str(tg_id)):
            ok_all = False

    print("[INFO] Daily reports done.")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
