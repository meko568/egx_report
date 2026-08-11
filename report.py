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
from typing import Dict, List, Optional

# ─── Halal Whitelist: EGX tickers (Yahoo Finance format .CA) ───
# Verified sectors: real estate, food/consumer, telecom, industrials, healthcare, tech
# Excluded: banks, insurance, alcohol, gambling, conventional finance
# NOTE: this is a sector-level heuristic list, not a full Shariah audit (debt/interest
# ratios not checked). Review periodically.
HALAL_TICKERS = [
    "FWRY.CA", "PHDC.CA", "JUFO.CA", "ORHD.CA", "CLHO.CA", "MFPC.CA",
    "EFID.CA", "ETEL.CA", "TMGH.CA", "EIPICO.CA", "OLFI.CA", "ISPH.CA",
    "ORWE.CA", "SWDY.CA", "MASR.CA", "SPMD.CA", "RMDA.CA", "EGCH.CA",
    "ACGC.CA", "POUL.CA", "DOMT.CA", "CIRA.CA", "ABUK.CA", "ESRS.CA",
    "EAST.CA", "ORAS.CA", "AMOC.CA",
]

# sector/industry substrings (lowercase) that disqualify a ticker in the
# heuristic /screen command. Not exhaustive, not a full fiqh audit.
EXCLUDED_KEYWORDS = [
    "bank", "insurance", "capital markets", "credit services",
    "asset management", "brewer", "wineries", "gambling", "casino",
    "tobacco", "mortgage", "diversified financial",
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # fallback single-chat mode (legacy/testing)

# ─── Job worker (processes /screen, /analyze, trial reports queued by bot.py) ───
TRIAL_TICKER_CAP = 12
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN", "https://meko568.pythonanywhere.com")
CRON_SECRET = os.getenv("CRON_SECRET")

JOB_STRINGS = {
    "trial_prefix": {"en": "\U0001F381 *One-time free sample report:*\n\n", "ar": "\U0001F381 *تقرير تجريبي مجاني لمرة واحدة:*\n\n"},
    "fetch_fail": {"en": "\u26A0\uFE0F Couldn't fetch data right now. Try again later.", "ar": "\u26A0\uFE0F معرفتش أجيب البيانات دلوقتي. جرب تاني بعدين."},
    "screen_error": {"en": "Couldn't fetch data for {ticker}. Check the ticker symbol.", "ar": "معرفتش أجيب بيانات {ticker}. تأكد من الكود."},
}


def _jt(key, lang, **kwargs):
    s = JOB_STRINGS.get(key, {}).get(lang) or JOB_STRINGS.get(key, {}).get("en", "")
    return s.format(**kwargs) if kwargs else s


# ─── Analyzers ───

def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
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


def calc_sma_cross(closes: List[float]) -> str:
    if len(closes) < 50:
        return "N/A"
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    arrow = "\U0001F53C" if sma20 > sma50 else "\U0001F53D"
    trend = "bullish" if sma20 > sma50 else "bearish"
    return f"{arrow} {trend} ({sma20:.2f}/{sma50:.2f})"


def _ema(vals: List[float], period: int) -> List[float]:
    k = 2 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def calc_macd(closes: List[float]) -> str:
    if len(closes) < 35:
        return "N/A"
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12[-len(ema26):], ema26)]
    signal = _ema(macd_line, 9)
    hist = macd_line[-1] - signal[-1]
    arrow = "\U0001F53C" if hist > 0 else "\U0001F53D"
    trend = "bullish" if hist > 0 else "bearish"
    return f"{arrow} {trend} (hist {hist:.3f})"


def calc_bollinger(closes: List[float]) -> str:
    if len(closes) < 20:
        return "N/A"
    window = closes[-20:]
    mean = sum(window) / 20
    var = sum((c - mean) ** 2 for c in window) / 20
    std = var ** 0.5
    upper, lower = mean + 2 * std, mean - 2 * std
    if upper == lower:
        return "N/A"
    pctb = (closes[-1] - lower) / (upper - lower) * 100
    return f"%B {pctb:.0f}% (band {lower:.2f}-{upper:.2f})"


def calc_volume_spike(volumes: List[float]) -> str:
    if len(volumes) < 21:
        return "N/A"
    avg20 = sum(volumes[-21:-1]) / 20
    today = volumes[-1]
    if avg20 == 0:
        return "N/A"
    ratio = today / avg20
    flag = "\u26A0\uFE0F spike" if ratio >= 2 else ("elevated" if ratio >= 1.5 else "normal")
    return f"{flag} ({ratio:.1f}x avg)"


ANALYZERS = {
    "rsi": lambda d: f"RSI: {d['rsi']}" if d.get("rsi") is not None else "RSI: N/A",
    "sma": lambda d: f"SMA: {calc_sma_cross(d.get('_closes', []))}",
    "macd": lambda d: f"MACD: {calc_macd(d.get('_closes', []))}",
    "bollinger": lambda d: f"Bollinger: {calc_bollinger(d.get('_closes', []))}",
    "volume_spike": lambda d: f"Volume: {calc_volume_spike(d.get('_volumes', []))}",
}


def screen_ticker(ticker: str) -> Dict:
    """Heuristic halal screen based on sector/industry keywords.
    NOT a full Shariah audit (no debt-ratio / interest-income check)."""
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not info or not (info.get("sector") or info.get("industry")):
        return {"ok": False, "error": "no data"}
    sector = (info.get("sector") or "").lower()
    industry = (info.get("industry") or "").lower()
    hit = next((kw for kw in EXCLUDED_KEYWORDS if kw in sector or kw in industry), None)
    return {
        "ok": True,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "N/A",
        "industry": info.get("industry") or "N/A",
        "passed": hit is None,
        "flag": hit,
    }


def fetch_stock_data(ticker: str) -> Optional[Dict]:
    """
    Fetch daily close, previous close, % change, volume, RSI, 52wk range
    position, and PE/target-price info for a ticker.
    Returns None if data unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo", interval="1d")  # 6mo so SMA50/MACD have data

        if hist.empty or len(hist) < 2:
            return None

        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        current_close = float(closes[-1])
        prev_close = float(closes[-2])
        volume = int(volumes[-1])

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
            "_closes": closes,
            "_volumes": volumes,
        }
    except Exception as e:
        print(f"[WARN] Failed to fetch {ticker}: {e}", file=sys.stderr)
        return None


def format_line(d: Dict, prefix: str = "", analyzers: Optional[List[str]] = None) -> str:
    ticker = d["ticker"].replace(".CA", "")
    price = f"{d['price']:.2f}"
    change = f"{d['change_pct']:+.2f}%"
    vol = f"{d['volume']:,}" if d.get("volume") else "N/A"
    rsi = f"{d['rsi']}" if d.get("rsi") is not None else "N/A"
    emoji = "\U0001F7E2" if d["change_pct"] >= 0 else "\U0001F534"
    lines = [
        f"{prefix}{emoji} `{ticker}`: {price} EGP ({change})",
        f"    Vol: {vol} | RSI: {rsi} | {d.get('range_str', 'N/A')}",
        f"    P/E: {d.get('pe_str', 'N/A')} | Target: {d.get('target_str', 'N/A')}",
    ]
    for key in (analyzers or []):
        if key == "rsi":
            continue  # already shown above
        fn = ANALYZERS.get(key)
        if fn:
            lines.append(f"    {fn(d)}")
    return "\n".join(lines)


def build_report(all_data: List[Dict], analyzers: Optional[List[str]] = None) -> str:
    """Build the Telegram Markdown report."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"\U0001F4CA *EGX Halal Daily Report* \u2014 {today}",
        "",
        "\U0001F4CB *Watchlist*",
    ]

    for d in all_data:
        lines.append(format_line(d, analyzers=analyzers))

    sorted_data = sorted(all_data, key=lambda x: x["change_pct"], reverse=True)
    gainers = [d for d in sorted_data if d["change_pct"] > 0][:3]

    lines.extend(["", "\U0001F4C8 *Top Gainers*"])
    if gainers:
        for i, d in enumerate(gainers, 1):
            lines.append(format_line(d, prefix=f"{i}. "))
    else:
        lines.append("No gainers today.")

    losers = [d for d in sorted_data if d["change_pct"] < 0][-3:]
    losers.reverse()

    lines.extend(["", "\U0001F4C9 *Top Losers*"])
    if losers:
        for i, d in enumerate(losers, 1):
            lines.append(format_line(d, prefix=f"{i}. "))
    else:
        lines.append("No losers today.")

    lines.extend(["", "\u26A0\uFE0F *Not financial advice. Data only.*"])

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
        payload = {"chat_id": target, "text": chunk, "parse_mode": "Markdown"}
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


def get_active_subscribers() -> Dict[int, Dict]:
    """Return {telegram_id: {"tickers":[...], "preferred_time":"HH:MM", "analyzers":[...]}}
    for users with subscribed=1 and expiry not passed."""
    import sqlite3
    from datetime import date as _date

    db_path = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "egxbot.db"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    out: Dict[int, Dict] = {}
    try:
        today = _date.today().isoformat()
        rows = conn.execute(
            "SELECT telegram_id, preferred_time, analyzers FROM users "
            "WHERE subscribed=1 AND expiry >= ?", (today,)
        ).fetchall()
        for r in rows:
            tickers = [
                x["ticker"] for x in conn.execute(
                    "SELECT ticker FROM watchlist WHERE telegram_id=?", (r["telegram_id"],)
                ).fetchall()
            ]
            analyzers = [a for a in (r["analyzers"] or "rsi").split(",") if a]
            out[r["telegram_id"]] = {
                "tickers": tickers,
                "preferred_time": r["preferred_time"] or "09:00",
                "analyzers": analyzers,
            }
    finally:
        conn.close()
    return out


def _process_screen_job(tg_id, ticker, lang):
    result = screen_ticker(ticker)
    if not result.get("ok"):
        send_telegram(_jt("screen_error", lang, ticker=ticker.replace(".CA", "")), chat_id=str(tg_id))
        return
    verdict = "\u2705 Passes sector screen" if result["passed"] else f"\u274C Flagged: {result['flag']}"
    msg = (
        f"{result['name']} ({ticker.replace('.CA', '')})\n"
        f"Sector: {result['sector']}\n"
        f"Industry: {result['industry']}\n\n"
        f"{verdict}\n\n"
        "\u26A0\uFE0F Heuristic sector screen only — not a full Shariah audit "
        "(debt/interest ratios not checked). Verify manually."
    )
    send_telegram(msg, chat_id=str(tg_id))


def _process_analyze_job(conn, tg_id, lang):
    tickers = [r["ticker"] for r in conn.execute(
        "SELECT ticker FROM watchlist WHERE telegram_id=?", (tg_id,)
    ).fetchall()]
    if not tickers:
        return
    row = conn.execute("SELECT analyzers FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    analyzers = [a for a in ((row["analyzers"] if row else None) or "rsi").split(",") if a]
    data = [d for d in (fetch_stock_data(tk) for tk in tickers) if d]
    if not data:
        send_telegram(_jt("fetch_fail", lang), chat_id=str(tg_id))
        return
    rpt = build_report(data, analyzers=analyzers)
    send_telegram(rpt, chat_id=str(tg_id))


def _process_trial_job(conn, tg_id, lang):
    tickers = [r["ticker"] for r in conn.execute(
        "SELECT ticker FROM watchlist WHERE telegram_id=?", (tg_id,)
    ).fetchall()][:TRIAL_TICKER_CAP]
    if not tickers:
        return
    data = [d for d in (fetch_stock_data(tk) for tk in tickers) if d]
    if not data:
        send_telegram(_jt("fetch_fail", lang), chat_id=str(tg_id))
        return
    rpt = build_report(data)
    send_telegram(_jt("trial_prefix", lang) + rpt, chat_id=str(tg_id))


def _ack_jobs(ids: List[int]) -> None:
    if not ids or not CRON_SECRET:
        return
    try:
        r = requests.post(
            f"{WEBHOOK_DOMAIN}/ack-jobs?token={CRON_SECRET}",
            json={"ids": ids},
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] Failed to ack jobs {ids}: {e}", file=sys.stderr)


def process_job_queue() -> int:
    """Process pending /screen, /analyze, and trial-report jobs queued by
    bot.py on PythonAnywhere (which can't reach Yahoo Finance / Telegram)."""
    import sqlite3

    db_path = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "egxbot.db"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        jobs = conn.execute("SELECT * FROM job_queue ORDER BY id").fetchall()
        if not jobs:
            print("[INFO] No pending jobs.")
            return 0
        print(f"[INFO] Processing {len(jobs)} pending job(s)...")
        processed_ids = []
        for job in jobs:
            tg_id, lang, kind = job["telegram_id"], (job["lang"] or "en"), job["kind"]
            try:
                if kind == "screen":
                    _process_screen_job(tg_id, job["payload"], lang)
                elif kind == "analyze":
                    _process_analyze_job(conn, tg_id, lang)
                elif kind == "trial":
                    _process_trial_job(conn, tg_id, lang)
                else:
                    print(f"[WARN] Unknown job kind: {kind}", file=sys.stderr)
                processed_ids.append(job["id"])
            except Exception as e:
                print(f"[ERROR] Job {job['id']} ({kind}) failed: {e}", file=sys.stderr)
                # Don't silently drop the job - tell the user it failed so
                # they're not left waiting forever, then ack it so it doesn't
                # retry-loop forever on the same bad job.
                try:
                    send_telegram(_jt("fetch_fail", lang), chat_id=str(tg_id))
                except Exception as notify_err:
                    print(f"[ERROR] Also failed to notify user {tg_id}: {notify_err}", file=sys.stderr)
                processed_ids.append(job["id"])
    finally:
        conn.close()

    _ack_jobs(processed_ids)
    print(f"[INFO] Job worker done, acked {len(processed_ids)} job(s).")
    return 0


def main() -> int:
    db_path = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "egxbot.db"))
    if not os.path.exists(db_path):
        print(f"[INFO] DB not configured — legacy single-chat mode, {len(HALAL_TICKERS)} tickers...")
        all_data = [d for d in (fetch_stock_data(t) for t in HALAL_TICKERS) if d]
        if not all_data:
            print("[ERROR] No data retrieved for any ticker", file=sys.stderr)
            return 1
        report = build_report(all_data)
        return 0 if send_telegram(report) else 1

    # Normal mode: fire hourly, only send to subscribers whose preferred_time
    # hour matches the current hour in Cairo. External cron (cron-job.org /
    # GitHub Actions) must trigger this endpoint every hour, not once a day.
    try:
        from zoneinfo import ZoneInfo
        current_hour = datetime.now(ZoneInfo("Africa/Cairo")).strftime("%H")
    except Exception:
        current_hour = datetime.utcnow().strftime("%H")  # fallback, no tz lib

    print("[INFO] Fetching active subscribers from DB...")
    subs = get_active_subscribers()
    due = {tg: info for tg, info in subs.items() if (info["preferred_time"].split(":")[0]) == current_hour}
    if not due:
        print(f"[INFO] No subscribers due this hour ({current_hour}:00 Cairo).")
        return 0

    all_tickers = sorted({t for info in due.values() for t in info["tickers"]})
    print(f"[INFO] Fetching {len(all_tickers)} unique tickers for {len(due)} subscribers due this hour...")
    cache: Dict[str, Dict] = {}
    for ticker in all_tickers:
        data = fetch_stock_data(ticker)
        if data:
            cache[ticker] = data
        else:
            print(f"[WARN] Failed to fetch {ticker}", file=sys.stderr)

    ok_all = True
    for tg_id, info in due.items():
        user_data = [cache[t] for t in info["tickers"] if t in cache]
        if not user_data:
            continue
        report = build_report(user_data, analyzers=info["analyzers"])
        if not send_telegram(report, chat_id=str(tg_id)):
            ok_all = False

    print("[INFO] Hourly report run done.")
    return 0 if ok_all else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "jobs":
        sys.exit(process_job_queue())
    sys.exit(main())
