-- EGX Halal Report Bot — MySQL schema (PythonAnywhere free MySQL)

CREATE TABLE IF NOT EXISTS halal_stocks (
    ticker VARCHAR(20) PRIMARY KEY,
    name   VARCHAR(100),
    sector VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username    VARCHAR(100),
    subscribed  BOOLEAN DEFAULT FALSE,
    expiry      DATE NULL,
    trial_used  BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watchlist (
    telegram_id BIGINT,
    ticker      VARCHAR(20),
    PRIMARY KEY (telegram_id, ticker),
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
    FOREIGN KEY (ticker) REFERENCES halal_stocks(ticker) ON DELETE CASCADE
);

-- Seed halal list (keep in sync with report.py HALAL_TICKERS)
INSERT IGNORE INTO halal_stocks (ticker, name, sector) VALUES
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
