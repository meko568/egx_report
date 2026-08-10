#!/usr/bin/env python3
"""
EGX Halal Report — Telegram bot (webhook mode, PythonAnywhere free tier, SQLite)

Env vars required:
  TELEGRAM_BOT_TOKEN   - from @BotFather
  ADMIN_TELEGRAM_ID    - your telegram numeric id (get from @userinfobot)
  WEBHOOK_SECRET       - random string, part of the webhook URL path
  WEBHOOK_DOMAIN       - e.g. https://yourname.pythonanywhere.com
  DB_PATH              - e.g. /home/yourname/egx_report/egxbot.db
  CRON_SECRET          - random string, protects /run-daily-report endpoint
  VODAFONE_CASH_NUMBER - number shown to users on /subscribe
"""

import os
import sqlite3
import logging
from datetime import date, timedelta

from flask import Flask, request
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("egx_bot")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
VODAFONE_CASH_NUMBER = os.environ.get("VODAFONE_CASH_NUMBER", "01xxxxxxxxx")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "egxbot.db"))
SUB_PRICE_EGP = 100
SUB_DAYS = 30
TRIAL_TICKER_CAP = 12

app = Flask(__name__)


# ─── DB ───

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_language_column(conn)
    return conn


def _ensure_language_column(conn):
    """Auto-migrate: add language col if missing. Safe to call every connect."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "language" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'en'")
        conn.commit()


# ─── i18n ───
# Key-based templates, not blind text replace — safe regardless of message content.

STRINGS = {
    "welcome": {
        "en": (
            "\U0001F44B Welcome to EGX Halal Report Bot.\n\n"
            "Commands:\n"
            "/mystocks - your watchlist\n"
            "/add TICKER - add a halal stock\n"
            "/remove TICKER - remove a stock\n"
            "/list - all available halal stocks\n"
            "/subscribe - get daily reports (100 EGP/month)\n"
            "/language - switch language"
        ),
        "ar": (
            "\U0001F44B اهلاً بيك في بوت تقارير الأسهم الحلال بالبورصة المصرية.\n\n"
            "الأوامر:\n"
            "/mystocks - قائمة أسهمك\n"
            "/add TICKER - إضافة سهم حلال\n"
            "/remove TICKER - حذف سهم\n"
            "/list - كل الأسهم الحلال المتاحة\n"
            "/subscribe - اشتراك تقارير يومية (100 جنيه/شهر)\n"
            "/language - تغيير اللغة"
        ),
    },
    "list_header": {"en": "\U0001F4CB *Halal Stocks Available*", "ar": "\U0001F4CB *الأسهم الحلال المتاحة*"},
    "watchlist_empty": {
        "en": "Your watchlist is empty. Use /add TICKER or /list to see options.",
        "ar": "قائمتك فاضية. استخدم /add TICKER أو /list عشان تشوف الأسهم المتاحة.",
    },
    "watchlist_header": {"en": "\U0001F4CA *Your Watchlist*", "ar": "\U0001F4CA *قائمة أسهمك*"},
    "subscribed_until": {"en": "\u2705 Subscribed until {expiry}", "ar": "\u2705 مشترك لحد {expiry}"},
    "not_subscribed": {"en": "\u274C Not subscribed - use /subscribe", "ar": "\u274C مش مشترك - استخدم /subscribe"},
    "add_usage": {"en": "Usage: /add TICKER (e.g. /add FWRY)", "ar": "الاستخدام: /add TICKER (مثال: /add FWRY)"},
    "add_not_halal": {"en": "{raw} not in halal list. Check /list.", "ar": "{raw} مش موجود في قائمة الأسهم الحلال. شوف /list."},
    "add_ok": {"en": "\u2705 Added {raw}", "ar": "\u2705 اتضاف {raw}"},
    "remove_usage": {"en": "Usage: /remove TICKER", "ar": "الاستخدام: /remove TICKER"},
    "remove_ok": {"en": "\U0001F5D1 Removed {raw}", "ar": "\U0001F5D1 اتحذف {raw}"},
    "subscribe_info": {
        "en": (
            "\U0001F4B3 Send {price} EGP via Vodafone Cash to: {number}\n"
            "Then send a screenshot of the transaction here as a photo.\n"
            "Admin will approve within 24h."
        ),
        "ar": (
            "\U0001F4B3 ابعت {price} جنيه فودافون كاش على: {number}\n"
            "بعدين ابعت هنا صورة إثبات التحويل.\n"
            "الأدمن هيوافق خلال 24 ساعة."
        ),
    },
    "approve_usage": {"en": "Usage: /approve TELEGRAM_ID", "ar": "الاستخدام: /approve TELEGRAM_ID"},
    "approve_invalid_id": {"en": "Invalid id.", "ar": "رقم غير صحيح."},
    "approve_ok_admin": {"en": "\u2705 Approved {id} until {expiry}", "ar": "\u2705 تمت الموافقة على {id} لحد {expiry}"},
    "approve_ok_user": {
        "en": "\U0001F389 Subscription active until {expiry}. Daily reports start tonight!",
        "ar": "\U0001F389 اشتراكك فعال لحد {expiry}. التقارير اليومية هتبدأ الليلة!",
    },
    "approve_not_found": {"en": "User not found (they must /start first).", "ar": "المستخدم مش موجود (لازم يعمل /start الأول)."},
    "unknown_cmd": {
        "en": "Unknown command. Try /mystocks, /add, /remove, /list, /subscribe",
        "ar": "أمر غير معروف. جرب /mystocks, /add, /remove, /list, /subscribe",
    },
    "photo_received": {
        "en": "Got it! Sent to admin for review. You'll be notified once approved.",
        "ar": "استلمنا الصورة! اتبعتت للأدمن للمراجعة. هتتبلغ لما يتم الموافقة.",
    },
    "trial_report_prefix": {"en": "\U0001F381 *One-time free sample report:*\n\n", "ar": "\U0001F381 *تقرير تجريبي مجاني لمرة واحدة:*\n\n"},
    "trial_report_fail": {
        "en": "Couldn't fetch sample data right now — try /subscribe, daily reports will still work.",
        "ar": "معرفناش نجيب بيانات تجريبية دلوقتي — جرب /subscribe، التقارير اليومية هتشتغل عادي.",
    },
    "language_usage": {
        "en": "Usage: /language en | ar\nCurrent: {current}",
        "ar": "الاستخدام: /language en | ar\nاللغة الحالية: {current}",
    },
    "language_set": {"en": "\u2705 Language set to English.", "ar": "\u2705 اتغيرت اللغة للعربي."},
}


def t(key, lang, **kwargs):
    lang = lang if lang in ("en", "ar") else "en"
    template = STRINGS.get(key, {}).get(lang) or STRINGS.get(key, {}).get("en", key)
    return template.format(**kwargs) if kwargs else template


def get_lang(tg_id):
    conn = db()
    try:
        row = conn.execute("SELECT language FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        return row["language"] if row and row["language"] else "en"
    finally:
        conn.close()


def set_lang(tg_id, lang):
    conn = db()
    try:
        conn.execute("UPDATE users SET language=? WHERE telegram_id=?", (lang, tg_id))
        conn.commit()
    finally:
        conn.close()


# ─── Telegram helpers ───

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


# ─── DB ops ───

def get_or_create_user(tg_id, username):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (telegram_id, username, subscribed, trial_used, language) VALUES (?,?,0,0,'en')",
                (tg_id, username),
            )
            tickers = [r["ticker"] for r in conn.execute("SELECT ticker FROM halal_stocks").fetchall()]
            for tk in tickers:
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist (telegram_id, ticker) VALUES (?,?)", (tg_id, tk)
                )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        return row
    finally:
        conn.close()


def is_subscribed(user):
    return bool(user["subscribed"]) and user["expiry"] and user["expiry"] >= date.today().isoformat()


def get_watchlist(tg_id):
    conn = db()
    try:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE telegram_id=? ORDER BY ticker", (tg_id,)
        ).fetchall()
        return [r["ticker"] for r in rows]
    finally:
        conn.close()


def ticker_exists(ticker):
    conn = db()
    try:
        return conn.execute("SELECT 1 FROM halal_stocks WHERE ticker=?", (ticker,)).fetchone() is not None
    finally:
        conn.close()


def add_ticker(tg_id, ticker):
    conn = db()
    try:
        conn.execute("INSERT OR IGNORE INTO watchlist (telegram_id, ticker) VALUES (?,?)", (tg_id, ticker))
        conn.commit()
    finally:
        conn.close()


def remove_ticker(tg_id, ticker):
    conn = db()
    try:
        conn.execute("DELETE FROM watchlist WHERE telegram_id=? AND ticker=?", (tg_id, ticker))
        conn.commit()
    finally:
        conn.close()


def mark_trial_used(tg_id):
    conn = db()
    try:
        conn.execute("UPDATE users SET trial_used=1 WHERE telegram_id=?", (tg_id,))
        conn.commit()
    finally:
        conn.close()


def approve_user(tg_id, days=SUB_DAYS):
    conn = db()
    try:
        row = conn.execute("SELECT expiry FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        if not row:
            return None
        today = date.today()
        base = date.fromisoformat(row["expiry"]) if row["expiry"] and row["expiry"] >= today.isoformat() else today
        new_expiry = base + timedelta(days=days)
        conn.execute(
            "UPDATE users SET subscribed=1, expiry=? WHERE telegram_id=?",
            (new_expiry.isoformat(), tg_id),
        )
        conn.commit()
        return new_expiry
    finally:
        conn.close()


def send_trial_report(tg_id):
    from report import fetch_stock_data, build_report

    lang = get_lang(tg_id)
    tickers = get_watchlist(tg_id)[:TRIAL_TICKER_CAP]
    data = [d for d in (fetch_stock_data(tk) for tk in tickers) if d]
    if not data:
        send_message(tg_id, t("trial_report_fail", lang))
        return
    report = build_report(data)
    send_message(tg_id, t("trial_report_prefix", lang) + report)
    mark_trial_used(tg_id)


# ─── Webhook ───

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
    lang = user["language"] if user["language"] else "en"

    if "photo" in msg:
        file_id = msg["photo"][-1]["file_id"]
        forward_photo(file_id, from_user)
        send_message(chat_id, t("photo_received", lang))
        return "ok"

    cmd = text.split()[0].lower() if text else ""

    if cmd == "/start":
        send_message(chat_id, t("welcome", lang))
        if not user["trial_used"] and not is_subscribed(user):
            send_trial_report(chat_id)
        return "ok"

    if cmd == "/language":
        parts = text.split()
        if len(parts) < 2 or parts[1].lower() not in ("en", "ar"):
            send_message(chat_id, t("language_usage", lang, current=lang))
            return "ok"
        new_lang = parts[1].lower()
        set_lang(chat_id, new_lang)
        send_message(chat_id, t("language_set", new_lang))
        return "ok"

    if cmd == "/list":
        conn = db()
        try:
            rows = conn.execute("SELECT ticker, name FROM halal_stocks ORDER BY ticker").fetchall()
        finally:
            conn.close()
        lines = [f"`{r['ticker'].replace('.CA', '')}` - {r['name']}" for r in rows]
        send_message(chat_id, t("list_header", lang) + "\n" + "\n".join(lines))
        return "ok"

    if cmd == "/mystocks":
        tickers = get_watchlist(chat_id)
        if not tickers:
            send_message(chat_id, t("watchlist_empty", lang))
        else:
            clean = [tk.replace(".CA", "") for tk in tickers]
            status = (
                t("subscribed_until", lang, expiry=user["expiry"])
                if is_subscribed(user)
                else t("not_subscribed", lang)
            )
            send_message(chat_id, f"{t('watchlist_header', lang)}\n{', '.join(clean)}\n\n{status}")
        return "ok"

    if cmd == "/add":
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, t("add_usage", lang))
            return "ok"
        raw = parts[1].upper()
        ticker = raw if raw.endswith(".CA") else raw + ".CA"
        if not ticker_exists(ticker):
            send_message(chat_id, t("add_not_halal", lang, raw=raw))
        else:
            add_ticker(chat_id, ticker)
            send_message(chat_id, t("add_ok", lang, raw=raw))
        return "ok"

    if cmd == "/remove":
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, t("remove_usage", lang))
            return "ok"
        raw = parts[1].upper()
        ticker = raw if raw.endswith(".CA") else raw + ".CA"
        remove_ticker(chat_id, ticker)
        send_message(chat_id, t("remove_ok", lang, raw=raw))
        return "ok"

    if cmd == "/subscribe":
        send_message(chat_id, t("subscribe_info", lang, price=SUB_PRICE_EGP, number=VODAFONE_CASH_NUMBER))
        return "ok"

    if cmd == "/approve":
        if chat_id != ADMIN_ID:
            return "ok"
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, t("approve_usage", lang))
            return "ok"
        try:
            target_id = int(parts[1])
        except ValueError:
            send_message(chat_id, t("approve_invalid_id", lang))
            return "ok"
        new_expiry = approve_user(target_id)
        if new_expiry:
            send_message(chat_id, t("approve_ok_admin", lang, id=target_id, expiry=new_expiry))
            send_message(target_id, t("approve_ok_user", get_lang(target_id), expiry=new_expiry))
        else:
            send_message(chat_id, t("approve_not_found", lang))
        return "ok"

    send_message(chat_id, t("unknown_cmd", lang))
    return "ok"


@app.route("/setwebhook")
def setwebhook():
    secret = os.environ.get("WEBHOOK_SECRET", "hook")
    domain = os.environ["WEBHOOK_DOMAIN"]
    url = f"{domain}/{secret}"
    r = requests.post(f"{API_URL}/setWebhook", json={"url": url}, timeout=10)
    return r.json()


@app.route("/run-daily-report")
def run_daily_report():
    """Triggered by an external free cron pinger (e.g. cron-job.org)."""
    token = request.args.get("token")
    if token != os.environ.get("CRON_SECRET"):
        return "forbidden", 403
    import report

    report.main()
    return "sent"


if __name__ == "__main__":
    app.run(debug=True)
