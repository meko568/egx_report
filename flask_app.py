# PythonAnywhere WSGI entry point.
# In the Web tab -> WSGI configuration file, replace contents with something like:
#
#   import os, sys
#   path = '/home/yourname/egx_report'
#   if path not in sys.path:
#       sys.path.insert(0, path)
#
#   os.environ["TELEGRAM_BOT_TOKEN"] = "123456:ABC..."
#   os.environ["ADMIN_TELEGRAM_ID"] = "123456789"
#   os.environ["WEBHOOK_SECRET"] = "some-random-string"
#   os.environ["WEBHOOK_DOMAIN"] = "https://yourname.pythonanywhere.com"
#   os.environ["CRON_SECRET"] = "another-random-string"
#   os.environ["VODAFONE_CASH_NUMBER"] = "01xxxxxxxxx"
#   os.environ["DB_PATH"] = "/home/yourname/egx_report/egxbot.db"
#
#   from flask_app import app as application

from bot import app  # noqa: F401
