#!/usr/bin/env python3
"""
EGX Halal Report — Telegram bot (webhook mode, PythonAnywhere free tier, SQLite)

Env vars required:
  TELEGRAM_BOT_TOKEN   - from @BotFather
  ADMIN_TELEGRAM_ID    - your telegram numeric id (get from @userinfobot)
  WEBHOOK_SECRET       - random string, part of the webhook URL path
  WEBHOOK_DOMAIN       - e.g. https://yourname.pythonanywhere.com
  DB_PATH              - e.g. /home/yourname/egx_report/egxbot.db
  CRON_SECRET          - random string, protects /export-db, /ack-jobs, /run-daily-report
  VODAFONE_CASH_NUMBER - number shown to users on /subscribe
"""

import os
import re
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
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

app = Flask(__name__)

ANALYZER_INFO = {
    "rsi": {"en": "RSI (14)", "ar": "مؤشر القوة النسبية"},
    "sma": {"en": "SMA 20/50 Cross", "ar": "تقاطع المتوسطات المتحركة"},
    "macd": {"en": "MACD", "ar": "ماكد"},
    "bollinger": {"en": "Bollinger %B", "ar": "بولينجر باند"},
    "volume_spike": {"en": "Volume Spike", "ar": "ارتفاع حجم التداول"},
}

# Extra halal seed tickers — auto-inserted (INSERT OR IGNORE) on every connect,
# so a repo pull + reload is enough, no manual SQL needed.
HALAL_SEED = [
    ("FWRY.CA", "Fawry", "FinTech/Payments"),
    ("PHDC.CA", "Palm Hills", "Real Estate"),
    ("JUFO.CA", "Juhayna", "Food & Beverage"),
    ("ORHD.CA", "Orascom Development", "Real Estate/Tourism"),
    ("CLHO.CA", "Cleopatra Hospitals", "Healthcare"),
    ("MFPC.CA", "Misr Fertilizers", "Industrials/Materials"),
    ("EFID.CA", "Edita Food Industries", "Food/Consumer"),
    ("ETEL.CA", "Telecom Egypt", "Telecommunications"),
    ("TMGH.CA", "Talaat Moustafa Group", "Real Estate"),
    ("EIPICO.CA", "EIPICO", "Pharma"),
    ("OLFI.CA", "Obour Land", "Food/Consumer"),
    ("ISPH.CA", "Ibnsina Pharma", "Pharma"),
    ("ORWE.CA", "Oriental Weavers", "Industrials/Textiles"),
    ("SWDY.CA", "Elsewedy Electric", "Industrials/Electric"),
    ("MASR.CA", "Madinet Masr", "Real Estate"),
    ("SPMD.CA", "Speed Medical", "Healthcare"),
    ("RMDA.CA", "Raya Foods", "Food/Consumer"),
    ("EGCH.CA", "Egyptian Chemical Industries", "Industrials/Chemicals"),
    ("ACGC.CA", "Arab Cotton Ginning", "Industrials/Agri"),
    ("POUL.CA", "Cairo Poultry", "Food/Agri"),
    ("DOMT.CA", "Domty", "Food/Consumer"),
    ("CIRA.CA", "CIRA Education", "Education"),
    ("ABUK.CA", "Abu Qir Fertilizers", "Industrials/Materials"),
    ("ESRS.CA", "Ezz Steel", "Industrials/Steel"),
    ("EAST.CA", "Eastern Company", "Consumer"),
    ("ORAS.CA", "Orascom Construction", "Industrials/Construction"),
    ("AMOC.CA", "Alexandria Mineral Oils", "Industrials/Energy"),
]


# ─── DB ───

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_columns(conn)
    _ensure_halal_seed(conn)
    _ensure_job_queue(conn)
    return conn


def _ensure_job_queue(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS job_queue ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER, kind TEXT, "
        "payload TEXT, lang TEXT DEFAULT 'en', created_at TEXT DEFAULT (datetime('now')))"
    )
    conn.commit()


def enqueue_job(tg_id, kind, payload, lang):
    """Queue work that needs Yahoo Finance / outbound Telegram — PythonAnywhere
    free tier can't do either. GitHub Actions job-worker picks these up."""
    conn = db()
    try:
        conn.execute(
            "INSERT INTO job_queue (telegram_id, kind, payload, lang) VALUES (?, ?, ?, ?)",
            (tg_id, kind, payload, lang),
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_columns(conn):
    """Auto-migrate: add any missing columns. Safe to call every connect.
    Existing rows get sane 'already set up' defaults; new rows override
    onboarding_done/onboarding_step explicitly in get_or_create_user."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    migrations = [
        ("language", "TEXT DEFAULT 'en'"),
        ("preferred_time", "TEXT DEFAULT '09:00'"),
        ("analyzers", "TEXT DEFAULT 'rsi'"),
        ("onboarding_step", "TEXT DEFAULT 'done'"),
        ("onboarding_done", "INTEGER DEFAULT 1"),
    ]
    changed = False
    for col, ddl in migrations:
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            changed = True
    if changed:
        conn.commit()


def _ensure_halal_seed(conn):
    conn.executemany(
        "INSERT OR IGNORE INTO halal_stocks (ticker, name, sector) VALUES (?,?,?)",
        HALAL_SEED,
    )
    conn.commit()


# ─── i18n ───

STRINGS = {
    "welcome": {
        "en": (
            "\U0001F44B Welcome to EGX Halal Report Bot.\n\n"
            "Commands:\n"
            "/mystocks - your watchlist\n"
            "/add TICKER - add a halal stock\n"
            "/remove TICKER - remove a stock\n"
            "/list - all available halal stocks\n"
            "/search KEYWORD - search halal stocks by name/ticker\n"
            "/screen TICKER - heuristic halal sector screen on any EGX ticker\n"
            "/analyze - instant report now (subscribers)\n"
            "/analyzers - choose which indicators show in your reports\n"
            "/time HH:MM - set your daily report time (default 09:00)\n"
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
            "/search كلمة - بحث في الأسهم الحلال بالاسم أو الكود\n"
            "/screen TICKER - فحص تقريبي لأي سهم حسب القطاع\n"
            "/analyze - تقرير فوري دلوقتي (للمشتركين)\n"
            "/analyzers - اختيار المؤشرات في تقاريرك\n"
            "/time HH:MM - وقت تقريرك اليومي (افتراضي 09:00)\n"
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
    "add_not_halal": {"en": "{raw} not in halal list. Check /list or /search.", "ar": "{raw} مش موجود في قائمة الأسهم الحلال. شوف /list أو /search."},
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
        "en": "\U0001F389 Subscription active until {expiry}. Daily reports start at your chosen time!",
        "ar": "\U0001F389 اشتراكك فعال لحد {expiry}. التقارير اليومية هتبدأ في وقتك المختار!",
    },
    "approve_not_found": {"en": "User not found (they must /start first).", "ar": "المستخدم مش موجود (لازم يعمل /start الأول)."},
    "unknown_cmd": {
        "en": "Unknown command. Try /mystocks, /add, /remove, /list, /search, /screen, /analyze, /subscribe",
        "ar": "أمر غير معروف. جرب /mystocks, /add, /remove, /list, /search, /screen, /analyze, /subscribe",
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
    # onboarding
    "choose_language": {"en": "\U0001F44B Welcome! Choose your language:", "ar": "\U0001F44B اهلاً! اختار لغتك:"},
    "choose_stocks": {
        "en": "Pick the halal stocks you want to track (tap to toggle), then tap Done:",
        "ar": "اختار الأسهم الحلال اللي عايز تتابعها (دوس عشان تختار)، وبعدين دوس تم:",
    },
    "done_btn": {"en": "Done", "ar": "تم"},
    "stocks_need_one": {"en": "Pick at least one stock first.", "ar": "اختار سهم واحد على الأقل الأول."},
    "stocks_saved": {"en": "\u2705 Watchlist updated.", "ar": "\u2705 اتحدثت القائمة."},
    "ask_time": {
        "en": "What time do you want your daily report? Send as HH:MM (24h, e.g. 09:00), or send /skip for the default 09:00.",
        "ar": "عايز تقريرك اليومي الساعة كام؟ ابعت الوقت بصيغة HH:MM (مثال 09:00)، أو ابعت /skip للوقت الافتراضي 09:00.",
    },
    "time_usage": {
        "en": "Usage: /time HH:MM (24h, e.g. /time 14:30)\nCurrent: {current}",
        "ar": "الاستخدام: /time HH:MM (مثال: /time 14:30)\nالحالي: {current}",
    },
    "time_invalid": {"en": "Invalid time format. Use HH:MM, e.g. 09:00", "ar": "صيغة وقت غلط. استخدم HH:MM مثل 09:00"},
    "time_set": {"en": "\u2705 Daily report time set to {time}.", "ar": "\u2705 اتحدد وقت تقريرك اليومي {time}."},
    "choose_analyzers": {
        "en": "Pick the indicators you want in your reports (tap to toggle), then tap Done:",
        "ar": "اختار المؤشرات اللي عايزها في تقاريرك (دوس عشان تختار)، وبعدين دوس تم:",
    },
    "analyzers_saved": {"en": "\u2705 Analyzers updated.", "ar": "\u2705 اتحدثت المؤشرات."},
    "analyze_locked": {
        "en": "\U0001F512 /analyze is for subscribers. Use /subscribe to unlock instant + daily reports.",
        "ar": "\U0001F512 /analyze للمشتركين بس. استخدم /subscribe عشان تفتح التقارير الفورية واليومية.",
    },
    "analyze_wait": {
        "en": "\u23F3 Queued. Your report will arrive in a few minutes.",
        "ar": "\u23F3 اتضاف للطابور. التقرير هيوصلك خلال دقايق.",
    },
    "job_queued": {
        "en": "\u23F3 Queued — checking {ticker}, result in a few minutes.",
        "ar": "\u23F3 اتضاف للطابور — بفحص {ticker}، النتيجة هتوصل خلال دقايق.",
    },
    "search_usage": {"en": "Usage: /search KEYWORD", "ar": "الاستخدام: /search كلمة"},
    "search_none": {"en": "No halal stocks matched \"{kw}\". Try /screen TICKER to check any ticker.", "ar": "مفيش نتايج لـ \"{kw}\". جرب /screen TICKER عشان تفحص أي سهم."},
    "screen_usage": {"en": "Usage: /screen TICKER (e.g. /screen HRHO)", "ar": "الاستخدام: /screen TICKER (مثال: /screen HRHO)"},
    "screen_error": {"en": "Couldn't fetch data for {ticker}. Check the ticker symbol.", "ar": "معرفتش أجيب بيانات {ticker}. تأكد من الكود."},
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


def set_onboarding_step(tg_id, step):
    conn = db()
    try:
        conn.execute("UPDATE users SET onboarding_step=? WHERE telegram_id=?", (step, tg_id))
        conn.commit()
    finally:
        conn.close()


def set_onboarding_done(tg_id):
    conn = db()
    try:
        conn.execute(
            "UPDATE users SET onboarding_done=1, onboarding_step='done' WHERE telegram_id=?", (tg_id,)
        )
        conn.commit()
    finally:
        conn.close()


def set_time(tg_id, hhmm):
    conn = db()
    try:
        conn.execute("UPDATE users SET preferred_time=? WHERE telegram_id=?", (hhmm, tg_id))
        conn.commit()
    finally:
        conn.close()


def get_user_analyzers(tg_id):
    conn = db()
    try:
        row = conn.execute("SELECT analyzers FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        raw = (row["analyzers"] if row and row["analyzers"] else "rsi")
        return [a for a in raw.split(",") if a]
    finally:
        conn.close()


def set_user_analyzers(tg_id, keys):
    conn = db()
    try:
        conn.execute("UPDATE users SET analyzers=? WHERE telegram_id=?", (",".join(keys), tg_id))
        conn.commit()
    finally:
        conn.close()


def toggle_analyzer(tg_id, key):
    current = get_user_analyzers(tg_id)
    if key in current:
        current.remove(key)
    else:
        current.append(key)
    set_user_analyzers(tg_id, current)


# ─── Telegram helpers ───

def send_message(chat_id, text, parse_mode="Markdown", reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        log.error(f"send_message failed: {e}")


def edit_markup(chat_id, message_id, reply_markup):
    try:
        requests.post(
            f"{API_URL}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
            timeout=10,
        )
    except Exception as e:
        log.error(f"edit_markup failed: {e}")


def answer_callback(cq_id, text=None, alert=False):
    payload = {"callback_query_id": cq_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = alert
    try:
        requests.post(f"{API_URL}/answerCallbackQuery", json=payload, timeout=10)
    except Exception as e:
        log.error(f"answer_callback failed: {e}")


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


# ─── Keyboards ───

def build_lang_markup():
    return {"inline_keyboard": [[
        {"text": "English", "callback_data": "lang:en"},
        {"text": "\u0627\u0644\u0639\u0631\u0628\u064A\u0629", "callback_data": "lang:ar"},
    ]]}


def build_stock_markup(tg_id):
    conn = db()
    try:
        rows = conn.execute("SELECT ticker FROM halal_stocks ORDER BY ticker").fetchall()
    finally:
        conn.close()
    wl = set(get_watchlist(tg_id))
    buttons, row = [], []
    for r in rows:
        tk = r["ticker"]
        label = ("\u2705 " if tk in wl else "") + tk.replace(".CA", "")
        row.append({"text": label, "callback_data": f"stock:TOGGLE:{tk}"})
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons  # done button appended by caller with correct lang


def build_stock_keyboard(tg_id, lang):
    buttons = build_stock_markup(tg_id)
    buttons.append([{"text": "\u2705 " + t("done_btn", lang), "callback_data": "stock:DONE"}])
    return {"inline_keyboard": buttons}


def build_analyzer_keyboard(tg_id, lang):
    current = set(get_user_analyzers(tg_id))
    buttons = []
    for key, labels in ANALYZER_INFO.items():
        label = ("\u2705 " if key in current else "") + labels.get(lang, labels["en"])
        buttons.append([{"text": label, "callback_data": f"an:TOGGLE:{key}"}])
    buttons.append([{"text": "\u2705 " + t("done_btn", lang), "callback_data": "an:DONE"}])
    return {"inline_keyboard": buttons}


# ─── DB ops ───

def get_or_create_user(tg_id, username):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users "
                "(telegram_id, username, subscribed, trial_used, language, "
                " preferred_time, analyzers, onboarding_step, onboarding_done) "
                "VALUES (?,?,0,0,'en','09:00','rsi','lang',0)",
                (tg_id, username),
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
    """PythonAnywhere can't reach Yahoo Finance, so this just queues the job
    for the GitHub Actions worker (see /ack-jobs, report.py:process_job_queue)."""
    lang = get_lang(tg_id)
    tickers = get_watchlist(tg_id)[:TRIAL_TICKER_CAP]
    if not tickers:
        return
    enqueue_job(tg_id, "trial", None, lang)
    send_message(tg_id, t("analyze_wait", lang))
    mark_trial_used(tg_id)


def send_onboarding_step(chat_id, lang, step):
    if step == "lang":
        send_message(chat_id, t("choose_language", lang), reply_markup=build_lang_markup())
    elif step == "stocks":
        send_message(chat_id, t("choose_stocks", lang), reply_markup=build_stock_keyboard(chat_id, lang))
    elif step == "time":
        send_message(chat_id, t("ask_time", lang))
    elif step == "analyzers":
        send_message(chat_id, t("choose_analyzers", lang), reply_markup=build_analyzer_keyboard(chat_id, lang))


# ─── Callback query handling ───

def handle_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    data = cq.get("data", "")
    from_user = cq.get("from", {})
    cq_id = cq["id"]

    user = get_or_create_user(chat_id, from_user.get("username", ""))
    lang = user["language"] or "en"
    answer_callback(cq_id)

    parts = data.split(":")
    prefix = parts[0]

    if prefix == "lang":
        new_lang = parts[1]
        set_lang(chat_id, new_lang)
        if not user["onboarding_done"] and user["onboarding_step"] == "lang":
            set_onboarding_step(chat_id, "stocks")
            send_onboarding_step(chat_id, new_lang, "stocks")
        else:
            send_message(chat_id, t("language_set", new_lang))
        return

    if prefix == "stock":
        action = parts[1]
        if action == "TOGGLE":
            ticker = parts[2]
            wl = get_watchlist(chat_id)
            if ticker in wl:
                remove_ticker(chat_id, ticker)
            else:
                add_ticker(chat_id, ticker)
            edit_markup(chat_id, message_id, build_stock_keyboard(chat_id, lang))
        elif action == "DONE":
            if not get_watchlist(chat_id):
                answer_callback(cq_id, t("stocks_need_one", lang), alert=True)
                return
            if not user["onboarding_done"] and user["onboarding_step"] == "stocks":
                set_onboarding_step(chat_id, "time")
                send_onboarding_step(chat_id, lang, "time")
            else:
                send_message(chat_id, t("stocks_saved", lang))
        return

    if prefix == "an":
        action = parts[1]
        if action == "TOGGLE":
            key = parts[2]
            toggle_analyzer(chat_id, key)
            edit_markup(chat_id, message_id, build_analyzer_keyboard(chat_id, lang))
        elif action == "DONE":
            if not get_user_analyzers(chat_id):
                set_user_analyzers(chat_id, ["rsi"])
            if not user["onboarding_done"] and user["onboarding_step"] == "analyzers":
                set_onboarding_done(chat_id)
                send_message(chat_id, t("welcome", lang))
                if not user["trial_used"]:
                    send_trial_report(chat_id)
            else:
                send_message(chat_id, t("analyzers_saved", lang))
        return


# ─── Webhook ───

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}

    cq = update.get("callback_query")
    if cq:
        handle_callback(cq)
        return "ok"

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

    # mid-onboarding free-text step: time entry
    if not user["onboarding_done"] and user["onboarding_step"] == "time" and not text.startswith("/"):
        if TIME_RE.match(text.strip()):
            set_time(chat_id, text.strip())
        else:
            set_time(chat_id, "09:00")
        set_onboarding_step(chat_id, "analyzers")
        send_onboarding_step(chat_id, lang, "analyzers")
        return "ok"

    if not user["onboarding_done"] and user["onboarding_step"] == "time" and text.strip() == "/skip":
        set_time(chat_id, "09:00")
        set_onboarding_step(chat_id, "analyzers")
        send_onboarding_step(chat_id, lang, "analyzers")
        return "ok"

    cmd = text.split()[0].lower() if text else ""

    if cmd == "/start":
        if not user["onboarding_done"]:
            send_onboarding_step(chat_id, lang, user["onboarding_step"] or "lang")
            return "ok"
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

    if cmd == "/search":
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_message(chat_id, t("search_usage", lang))
            return "ok"
        kw = parts[1].strip()
        conn = db()
        try:
            rows = conn.execute(
                "SELECT ticker, name, sector FROM halal_stocks "
                "WHERE ticker LIKE ? OR name LIKE ? OR sector LIKE ? ORDER BY ticker",
                (f"%{kw}%", f"%{kw}%", f"%{kw}%"),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            send_message(chat_id, t("search_none", lang, kw=kw))
            return "ok"
        lines = [f"`{r['ticker'].replace('.CA', '')}` - {r['name']} ({r['sector']})" for r in rows]
        send_message(chat_id, t("list_header", lang) + "\n" + "\n".join(lines))
        return "ok"

    if cmd == "/screen":
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, t("screen_usage", lang))
            return "ok"
        raw = parts[1].upper()
        ticker = raw if raw.endswith(".CA") else raw + ".CA"
        # PythonAnywhere can't reach Yahoo Finance — queue for the GH Actions worker.
        enqueue_job(chat_id, "screen", ticker, lang)
        send_message(chat_id, t("job_queued", lang, ticker=raw))
        return "ok"

    if cmd == "/addstock":
        if chat_id != ADMIN_ID:
            return "ok"
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            send_message(chat_id, "Usage: /addstock TICKER Name Here")
            return "ok"
        raw = parts[1].upper()
        ticker = raw if raw.endswith(".CA") else raw + ".CA"
        name = parts[2]
        conn = db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO halal_stocks (ticker, name, sector) VALUES (?,?,?)",
                (ticker, name, "Screened"),
            )
            conn.commit()
        finally:
            conn.close()
        send_message(chat_id, f"\u2705 Added {ticker} - {name} to halal list.")
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

    if cmd == "/analyzers":
        send_message(chat_id, t("choose_analyzers", lang), reply_markup=build_analyzer_keyboard(chat_id, lang))
        return "ok"

    if cmd == "/time":
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, t("time_usage", lang, current=user["preferred_time"]))
            return "ok"
        val = parts[1].strip()
        if not TIME_RE.match(val):
            send_message(chat_id, t("time_invalid", lang))
            return "ok"
        set_time(chat_id, val)
        send_message(chat_id, t("time_set", lang, time=val))
        return "ok"

    if cmd == "/analyze":
        if not is_subscribed(user):
            send_message(chat_id, t("analyze_locked", lang))
            return "ok"
        tickers = get_watchlist(chat_id)
        if not tickers:
            send_message(chat_id, t("watchlist_empty", lang))
            return "ok"
        # PythonAnywhere can't reach Yahoo Finance — queue for the GH Actions worker.
        enqueue_job(chat_id, "analyze", None, lang)
        send_message(chat_id, t("analyze_wait", lang))
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
    domain = os.environ["WEBHOOK_DOMAIN"]
    url = f"{domain}/{BOT_TOKEN}"
    r = requests.post(f"{API_URL}/setWebhook", json={"url": url}, timeout=10)
    return r.json()


@app.route("/export-db")
def export_db():
    """Lets GitHub Actions (unrestricted outbound) pull the live subscriber DB,
    since PythonAnywhere free tier can't reach api.telegram.org / Yahoo Finance
    to send reports itself."""
    token = request.args.get("token")
    if token != os.environ.get("CRON_SECRET"):
        return "forbidden", 403
    from flask import send_file

    return send_file(DB_PATH, mimetype="application/octet-stream", as_attachment=True, download_name="egxbot.db")


@app.route("/ack-jobs", methods=["POST"])
def ack_jobs():
    """GitHub Actions job-worker calls this after sending queued /screen,
    /analyze, and trial-report results, so processed rows aren't resent."""
    token = request.args.get("token")
    if token != os.environ.get("CRON_SECRET"):
        return "forbidden", 403
    body = request.get_json(force=True, silent=True) or {}
    ids = [int(i) for i in body.get("ids", [])]
    if not ids:
        return "ok"
    conn = db()
    try:
        qmarks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM job_queue WHERE id IN ({qmarks})", ids)
        conn.commit()
    finally:
        conn.close()
    return "ok"


@app.route("/run-daily-report")
def run_daily_report():
    """Triggered by an external free cron pinger (e.g. cron-job.org).
    Must be triggered HOURLY now — report.py filters by each user's preferred_time."""
    token = request.args.get("token")
    if token != os.environ.get("CRON_SECRET"):
        return "forbidden", 403
    import report

    report.main()
    return "sent"


if __name__ == "__main__":
    app.run(debug=True)
