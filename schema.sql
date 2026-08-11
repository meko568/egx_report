-- EGX Halal Report Bot — SQLite schema (PythonAnywhere free tier, no MySQL needed)
-- Run once: sqlite3 egxbot.db < schema.sql

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS halal_stocks (
    ticker TEXT PRIMARY KEY,
    name   TEXT,
    sector TEXT
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username    TEXT,
    subscribed  INTEGER DEFAULT 0,      -- 0/1
    expiry      TEXT,                   -- ISO date 'YYYY-MM-DD' or NULL
    trial_used  INTEGER DEFAULT 0,      -- 0/1
    analyze_trial_used INTEGER DEFAULT 0, -- 0/1, one free /analyze for non-subscribers
    screen_trial_used  INTEGER DEFAULT 0, -- 0/1, one free /screen for non-subscribers
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watchlist (
    telegram_id INTEGER,
    ticker      TEXT,
    PRIMARY KEY (telegram_id, ticker),
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
    FOREIGN KEY (ticker) REFERENCES halal_stocks(ticker) ON DELETE CASCADE
);

-- On-demand jobs (/screen, /analyze, trial report) queued by bot.py on
-- PythonAnywhere (which cannot reach Yahoo Finance / Telegram outbound),
-- and processed by the GitHub Actions job-worker (unrestricted outbound).
-- A row = pending. The worker deletes rows via /ack-jobs once sent.
CREATE TABLE IF NOT EXISTS job_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    kind        TEXT,      -- 'screen' | 'analyze' | 'trial'
    payload     TEXT,      -- ticker for 'screen', NULL otherwise
    lang        TEXT DEFAULT 'en',
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Seed halal list (keep in sync with report.py HALAL_TICKERS)
INSERT OR IGNORE INTO halal_stocks (ticker, name, sector) VALUES
('FWRY.CA',   'Fawry',                 'FinTech/Payments'),
('PHDC.CA',   'Palm Hills',            'Real Estate'),
('JUFO.CA',   'Juhayna',               'Food & Beverage'),
('ORHD.CA',   'Orascom Development',   'Real Estate/Tourism'),
('CLHO.CA',   'Cleopatra Hospitals',   'Healthcare'),
('MFPC.CA',   'Misr Fertilizers',      'Industrials/Materials'),
('EFID.CA',   'Edita Food Industries', 'Food/Consumer'),
('ETEL.CA',   'Telecom Egypt',         'Telecommunications'),
('TMGH.CA',   'Talaat Moustafa Group', 'Real Estate'),
('EIPICO.CA', 'EIPICO',                'Pharma'),
('OLFI.CA',   'Obour Land',            'Food/Consumer'),
('ISPH.CA',   'Ibnsina Pharma',        'Pharma');
