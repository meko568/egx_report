#!/usr/bin/env python3
"""
EGX Halal Report — Telegram bot (webhook mode, for PythonAnywhere free tier)

Env vars required:
  TELEGRAM_BOT_TOKEN   - from @BotFather
  ADMIN_TELEGRAM_ID    - your telegram numeric id (get from @userinfobot)
  WEBHOOK_SECRET       - random string, part of the webhook URL path
  WEBHOOK_DOMAIN       - e.g. https://yourname.pythonanywhere.com
  DB_HOST              - e.g. yourname.mysql.pythonanywhere-services.com
  DB_USER              - e.g. yourname
  DB_PASS
  DB_NAME              - e.g. yourname$egxbot
  CRON_SECRET          - random string, protects /run-daily-report endpoint
  VODAFONE_CASH_NUMBER - number shown to users on /subscribe
"""

import os
import logging
from datetime import date, timedelta

from flask import Flask, request
import requests
import pymysql

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("egx_bot")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
VODAFONE_CASH_NUMBER = os.environ.get("VODAFONE_CASH_NUMBER", "01xxxxxxxxx")
SUB_PRICE_EGP = 100
SUB_DAYS = 30
TRIAL_TICKER_CAP = 12  # keep trial report light

app = Flask(__name__)


def db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        database=os.environ["DB_NAME"],
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def send_message(chat_id, text, parse_mode="Markdown"):
    try:
        requests.post(
            f"{API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        log.error(f"send_message failed: {e}")


def forward_photo(file_id, from_user):
    uname = from_user.get("username") or "no_username"
    caption = (
        f"\U0001F4B0 Payment proof\n"
        f"From: @{uname} (id: {from_user['id']})\n"
        f"Approve: /approve {from_user['id']}"
    )
    try:
        requests.post(
            f"{API_URL}/sendPhoto",
            json={"chat_id": ADMIN_ID, "photo": file_id, "caption": caption},
            timeout=10,
        )
    except Exception as e:
        log.error(f"forward_photo failed: {e}")


def get_or_create_user(tg_id, username):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id=%s", (tg_id,))
            user = cur.fetchone()
            if not user:
                cur.execute(
                    "INSERT INTO users (telegram_id, username, subscribed, trial_used) "
                    "VALUES (%s,%s,FALSE,FALSE)",
                    (tg_id, username),
                )
                cur.execute("SELECT ticker FROM halal_stocks")
                for row in cur.fetchall():
                    cur.execute(
                        "INSERT IGNORE INTO watchlist (telegram_id, ticker) VALUES (%s,%s)",
                        (tg_id, row["ticker"]),
                    )
                cur.execute("SELECT * FROM users WHERE telegram_id=%s", (tg_id,))
                user = cur.fetchone()
        return user
    finally:
        conn.close()


def is_subscribed(user):
    return bool(user["subscribed"]) and user["expiry"] and user["expiry"] >= date.today()


def get_watchlist(tg_id):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker FROM watchlist WHERE telegram_id=%s ORDER BY ticker", (tg_id,)
            )
            return [r["ticker"] for r in cur.fetchall()]
    finally:
        conn.close()


def ticker_exists(ticker):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM halal_stocks WHERE ticker=%s", (ticker,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def add_ticker(tg_id, ticker):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO watchlist (telegram_id, ticker) VALUES (%s,%s)",
                (tg_id, ticker),
            )
    finally:
        conn.close()


def remove_ticker(tg_id, ticker):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM watchlist WHERE telegram_id=%s AND ticker=%s", (tg_id, ticker)
            )
    finally:
        conn.close()


def mark_trial_used(tg_id):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET trial_used=TRUE WHERE telegram_id=%s", (tg_id,))
    finally:
        conn.close()


def approve_user(tg_id, days=SUB_DAYS):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT expiry FROM users WHERE telegram_id=%s", (tg_id,))
            row = cur.fetchone()
            if not row:
                return None
            base = row["expiry"] if row["expiry"] and row["expiry"] >= date.today() else date.today()
            new_expiry = base + timedelta(days=days)
            cur.execute(
                "UPDATE users SET subscribed=TRUE, expiry=%s WHERE telegram_id=%s",
                (new_expiry, tg_id),
            )
        return new_expiry
    finally:
        conn.close()


def send_trial_report(tg_id):
    from report import fetch_stock_data, build_report  # local import, avoid circular

    tickers = get_watchlist(tg_id)[:TRIAL_TICKER_CAP]
    data = [d for d in (fetch_stock_data(t) for t in tickers) if d]
    if not data:
        send_message(tg_id, "Couldn't fetch sample data right now — try /subscribe, daily reports will still work.")
        return
    report = build_report(data)
    send_message(tg_id, "\U0001F381 *One-time free sample report:*\n\n" + report)
    mark_trial_used(tg_id)


@app.route(f"/{os.environ.get('WEBHOOK_SECRET', 'hook')}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    msg = update.get("message")
    if not msg:
        return "ok"

    chat_id = msg["chat"]["id"]
    from_user = msg.get("from", {})
    text = msg.get("text", "") or ""

    user = get_or_create_user(chat_id, from_user.get("username", ""))

    if "photo" in msg:
        file_id = msg["photo"][-1]["file_id"]
        forward_photo(file_id, from_user)
        send_message(chat_id, "Got it! Sent to admin for review. You'll be notified once approved.")
        return "ok"

    cmd = text.split()[0].lower() if text else ""

    if cmd == "/start":
        send_message(
            chat_id,
            "\U0001F44B Welcome to EGX Halal Report Bot.\n\n"
            "Commands:\n"
            "/mystocks - your watchlist\n"
            "/add TICKER - add a halal stock\n"
            "/remove TICKER - remove a stock\n"
            "/list - all available halal stocks\n"
            "/subscribe - get daily reports (100 EGP/month)",
        )
        if not user["trial_used"] and not is_subscribed(user):
            send_trial_report(chat_id)
        return "ok"

    if cmd == "/list":
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT ticker, name FROM halal_stocks ORDER BY ticker")
                rows = cur.fetchall()
        finally:
            conn.close()
        lines = [f"`{r['ticker'].replace('.CA', '')}` - {r['name']}" for r in rows]
        send_message(chat_id, "\U0001F4CB *Halal Stocks Available*\n" + "\n".join(lines))
        return "ok"

    if cmd == "/mystocks":
        tickers = get_watchlist(chat_id)
        if not tickers:
            send_message(chat_id, "Your watchlist is empty. Use /add TICKER or /list to see options.")
        else:
            clean = [t.replace(".CA", "") for t in tickers]
            status = (
                f"\u2705 Subscribed until {user['expiry']}"
                if is_subscribed(user)
                else "\u274C Not subscribed - use /subscribe"
            )
            send_message(chat_id, f"\U0001F4CA *Your Watchlist*\n{', '.join(clean)}\n\n{status}")
        return "ok"

    if cmd == "/add":
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Usage: /add TICKER (e.g. /add FWRY)")
            return "ok"
        raw = parts[1].upper()
        ticker = raw if raw.endswith(".CA") else raw + ".CA"
        if not ticker_exists(ticker):
            send_message(chat_id, f"{raw} not in halal list. Check /list.")
        else:
            add_ticker(chat_id, ticker)
            send_message(chat_id, f"\u2705 Added {raw}")
        return "ok"

    if cmd == "/remove":
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Usage: /remove TICKER")
            return "ok"
        raw = parts[1].upper()
        ticker = raw if raw.endswith(".CA") else raw + ".CA"
        remove_ticker(chat_id, ticker)
        send_message(chat_id, f"\U0001F5D1 Removed {raw}")
        return "ok"

    if cmd == "/subscribe":
        send_message(
            chat_id,
            f"\U0001F4B3 Send {SUB_PRICE_EGP} EGP via Vodafone Cash to: {VODAFONE_CASH_NUMBER}\n"
            "Then send a screenshot of the transaction here as a photo.\n"
            "Admin will approve within 24h.",
        )
        return "ok"

    if cmd == "/approve":
        if chat_id != ADMIN_ID:
            return "ok"
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Usage: /approve TELEGRAM_ID")
            return "ok"
        try:
            target_id = int(parts[1])
        except ValueError:
            send_message(chat_id, "Invalid id.")
            return "ok"
        new_expiry = approve_user(target_id)
        if new_expiry:
            send_message(chat_id, f"\u2705 Approved {target_id} until {new_expiry}")
            send_message(target_id, f"\U0001F389 Subscription active until {new_expiry}. Daily reports start tonight!")
        else:
            send_message(chat_id, "User not found (they must /start first).")
        return "ok"

    send_message(chat_id, "Unknown command. Try /mystocks, /add, /remove, /list, /subscribe")
    return "ok"


@app.route("/setwebhook")
def setwebhook():
    """Visit this URL once (in browser) after deploy to register the webhook."""
    secret = os.environ.get("WEBHOOK_SECRET", "hook")
    domain = os.environ["WEBHOOK_DOMAIN"]
    url = f"{domain}/{secret}"
    r = requests.post(f"{API_URL}/setWebhook", json={"url": url}, timeout=10)
    return r.json()


@app.route("/run-daily-report")
def run_daily_report():
    """Triggered by PythonAnywhere scheduled Task (or GitHub Actions fallback ping)."""
    token = request.args.get("token")
    if token != os.environ.get("CRON_SECRET"):
        return "forbidden", 403
    import report

    report.main()
    return "sent"


if __name__ == "__main__":
    app.run(debug=True)
