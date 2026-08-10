import os
import sqlite3
import logging
from datetime import date, timedelta
from flask import Flask, request
import requests

LOG = logging.getLogger("egx_bot")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
VODAFONE_CASH_NUMBER = os.environ.get("VODAFONE_CASH_NUMBER", "01XXXXXXXXX")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "egxbot.db"))
SUB_PRICE_EGP = 100
SUB_DAYS = 30
TRIAL_TICKER_CAP = 12

app = Flask(__name__)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def send_message(chat_id, text, parse_mode="Markdown"):
    conn = None  # Declare conn upfront
    try:
        conn = db()
        user = conn.execute("SELECT language FROM users WHERE telegram_id=?", (chat_id,)).fetchone()
        if user and user['language'] == 'ar':
            text = translate_to_arabic(text)
        requests.post(
            f"{API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10
        )  # Removed extra parenthesis here
    except Exception as e:
        LOG.error(f"send_message failed: {e}")
    finally:
        if conn is not None:
            conn.close()
            f"{API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        LOG.error(f"send_message failed: {e}")
    finally:
        if 'conn' in locals():
            conn.close()


def translate_to_arabic(text):
    # Use proper Arabic translation for all messages
    translations = {
        "*Welcome to EGX Halal Report Bot.*": "*مرحبا بك في بوت تقارير الإيقاظ المصرية!*",
        "Commands:*": "*الأوامر:*",
        "Your watchlist is empty.*": "*قائمتك فارغة.*",
        "Subscription active until ": "*الاشتراک فعال حتی *",
        "*One-time free sample report:*": "*عبرية一个一个时段的免费报告:*",
        "Please select your language:" : "*من فضلك اختر لغة:*",
        "/language en - English": "/language en - الإنجليزيه",
        "/language ar - Arabic": "/language ar - العربية"
    }
    for eng, ar in translations.items():
        text = text.replace(eng, ar)
    return text

# ... rest of the existing bot.py code remains the same ...