# Deploy on PythonAnywhere (free tier, SQLite, no MySQL needed)

## 1. Account + files
1. pythonanywhere.com → free signup, no card.
2. Bash console (Consoles → New console → Bash):
   ```
   git clone https://github.com/meko568/egx_report.git
   cd egx_report
   pip3.10 install --user -r requirements.txt
   ```
   (match pip version to whatever Python version you pick in step 3)

## 2. Create the SQLite DB
Still in the Bash console:
```
sqlite3 egxbot.db < schema.sql
```
That's it — no server, no password, just a file sitting in your repo folder.

## 3. Web app
1. Web tab → Add a new web app → Manual config → pick a Python version (match step 1).
2. Set source code dir to `/home/yourname/egx_report`.
3. Click the WSGI config file link → replace contents with what's in `flask_app.py`'s
   comment block (import path + all `os.environ[...] = "..."` lines + the import line).
   Make sure `DB_PATH` points to the exact path of `egxbot.db` from step 2.
4. Reload the web app (green button, Web tab).

## 4. Register the webhook
Visit once in your browser (logged into PythonAnywhere):
`https://yourname.pythonanywhere.com/setwebhook`
Should return `{"ok": true, ...}`.

## 5. Daily report — external free cron (PythonAnywhere free tier has no Scheduled Tasks anymore)
1. Go to cron-job.org → free signup.
2. New cron job → URL: `https://yourname.pythonanywhere.com/run-daily-report?token=YOUR_CRON_SECRET`
3. Schedule: once daily, your preferred time (site shows UTC — convert from Cairo time).
4. Save, run once manually to test it fires correctly.

## 6. GitHub Actions — not needed anymore
Delete or ignore `.github/workflows/daily-report.yml` — cron-job.org replaces it,
and GitHub Actions can't reach your SQLite file anyway (it only exists inside your
PythonAnywhere account).

## 7. Test with real users
- `/start` → welcome + one free sample report (from your default watchlist = all halal tickers).
- `/add`, `/remove`, `/mystocks`, `/list` → work immediately, no subscription needed.
- `/subscribe` → shows Vodafone Cash number → user sends screenshot as a photo
  → forwarded to you (admin) with `/approve <id>` in the caption.
- You run `/approve <id>` from your own Telegram account (must match `ADMIN_TELEGRAM_ID`)
  → sets subscribed=1, expiry = today+30d (or +30d from current expiry if renewing early), notifies user.
- Hit `/run-daily-report?token=...` manually once in your browser to confirm reports
  go out correctly before trusting the cron-job.org schedule.
