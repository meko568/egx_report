# Deploy on PythonAnywhere (free tier)

## 1. Account + files
1. Sign up at pythonanywhere.com (free, no card).
2. Files tab → upload this whole repo (or use Bash console: `git clone` your repo).
3. Bash console: `pip install --user -r requirements.txt`

## 2. MySQL
1. Databases tab → set a DB password → your free MySQL instance spins up.
   Host shown there = `DB_HOST`. Your DB is `yourname$default` — create a
   new one named `egxbot` (shows as `yourname$egxbot`) = `DB_NAME`.
2. Open a MySQL console (same tab) and run everything in `schema.sql`.

## 3. Web app
1. Web tab → Add a new web app → Manual config → Python 3.10.
2. Set source code dir to your repo path.
3. Edit the WSGI config file it links you to → paste contents of `flask_app.py`'s
   comment block (import path + `from flask_app import app as application`),
   AND set all env vars there with `os.environ["X"] = "..."` (free tier has
   no separate secrets UI — this file is the only place).
4. Reload the web app.

## 4. Register the webhook
Visit once in your browser (while logged into PythonAnywhere):
`https://yourname.pythonanywhere.com/setwebhook`
Should return `{"ok": true, ...}`.

## 5. Daily report — PythonAnywhere Task (this is the real cron now)
1. Tasks tab → free tier gives 1 scheduled task/day.
2. Command: `curl "https://yourname.pythonanywhere.com/run-daily-report?token=YOUR_CRON_SECRET"`
   (calling the endpoint keeps it inside the request-based free tier CPU model)
3. Set time (UTC — PythonAnywhere shows UTC, convert Cairo time yourself).

## 6. GitHub Actions (optional backup)
Repo → Settings → Secrets → add `CRON_PING_URL` =
`https://yourname.pythonanywhere.com/run-daily-report?token=YOUR_CRON_SECRET`
Keeps `daily-report.yml` as a 15-min-later safety ping — harmless if it
double-fires since Telegram just gets two identical messages that day
(not deduped — remove the workflow entirely if that bugs you).

## 7. Test with real users
- Message your bot `/start` → should get welcome + one free sample report.
- `/add`, `/remove`, `/mystocks`, `/list` should all work immediately, no subscription needed.
- `/subscribe` → shows Vodafone Cash number → user sends screenshot as photo
  → forwarded to you (admin) with `/approve <id>` button text.
- Run `/approve <id>` from your own account (must match `ADMIN_TELEGRAM_ID`) →
  sets subscribed + expiry = today+30d, notifies user.
- Trigger `/run-daily-report?token=...` manually once to confirm reports
  go out correctly before relying on the scheduled Task.
