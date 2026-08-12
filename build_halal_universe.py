#!/usr/bin/env python3
"""
Builds the halal_stocks universe from scratch instead of a hand-picked list.

Pipeline:
  1. EGX_ALL_TICKERS = every EGX-listed ticker (224, source: stockanalysis.com,
     refresh this list every few months — EGX doesn't publish a clean API).
  2. Sector/industry keyword exclude (banks, insurance, brewers, gambling,
     conventional finance/leasing/brokerage) via yfinance .info — same
     EXCLUDED_KEYWORDS used by report.py's /screen command.
  3. Financial ratio screen (AAOIFI-style, ~33% cutoff) via yfinance .info:
       - total debt / market cap        < 33%
       - total cash+STI / market cap    < 33%
     Tickers missing marketCap/financial data are EXCLUDED (fail-safe, not
     assumed halal) and logged for manual review.
  4. POSTs the surviving list to /import-halal-list on PythonAnywhere.

This is a best-effort screen using Yahoo Finance's summary fields, not a full
Shariah board audit (no interest-income-as-%-of-revenue line item is reliably
available via yfinance for EGX tickers). Re-run monthly; ratios move slowly.

Run manually:
    CRON_SECRET=xxx WEBHOOK_DOMAIN=https://meko568.pythonanywhere.com python3 build_halal_universe.py
Or via the halal-universe.yml GitHub Actions workflow (monthly cron).
"""

import os
import sys
import time
import requests
import yfinance as yf

RATIO_CUTOFF = 0.33  # AAOIFI-style debt/cash screen threshold

# Yahoo blocks the plain-requests TLS fingerprint that GitHub Actions runners
# use for the .info/quoteSummary endpoint. Recent yfinance (0.2.6x+) handles
# curl_cffi impersonation INTERNALLY now and manages its own cookie/crumb
# session — passing a hand-built curl_cffi Session actively breaks that flow.
# Fix is just having curl_cffi installed (see requirements.txt / workflow
# pip install step); don't construct or pass a session, let yfinance own it.

# sector/industry substrings (lowercase) that disqualify a ticker outright,
# regardless of financial ratios. Kept in sync with report.py EXCLUDED_KEYWORDS.
EXCLUDED_KEYWORDS = [
    "bank", "insurance", "capital markets", "credit services",
    "asset management", "brewer", "wineries", "gambling", "casino",
    "tobacco", "mortgage", "diversified financial", "leasing",
    "brokerage", "securities",
]

# Full EGX-listed ticker universe (224 symbols, source: stockanalysis.com/list/
# egyptian-stock-exchange/, captured 2026-08-12). Refresh periodically — EGX
# has no clean public ticker-list API, so this is a static snapshot.
EGX_ALL_TICKERS = [
    "COMI", "SWDY", "TMGH", "ETEL", "EGAL", "QNBE", "EAST", "MFPC", "ABUK", "ALCN",
    "HDBK", "EFIH", "ADIB", "ORAS", "FWRY", "EMFD", "SCTS", "ORHD", "EFID", "PHDC",
    "GPPL", "JUFO", "BIOC", "VLMR", "VLMRA", "HRHO", "OCDI", "CANA", "BTFH", "HELI",
    "GBCO", "RAYA", "IRON", "FERC", "CIEB", "FAIT", "FAITA", "EXPA", "EGCH", "CLHO",
    "VALU", "PHAR", "ARCC", "CCAP", "CIRA", "MTIE", "SCEM", "TAQA", "EFIC", "EGTS",
    "SKPC", "POUL", "MCQE", "NIPH", "ORWE", "MASR", "EGSA", "SAUD", "MOIL", "UBEE",
    "AMES", "EGBE", "MBSC", "ISPH", "MHOT", "TALM", "CICH", "RMDA", "ATQA", "AMOC",
    "CSAG", "BINV", "IFAP", "MPRC", "OLFI", "MOIN", "PRDC", "MIPH", "ISMQ", "BONY",
    "OIH", "EGAS", "DOMT", "PHTV", "SPHT", "AFMC", "KORA", "MPCI", "ELEC", "ZMID",
    "CPCI", "ACAP", "NINH", "ENGC", "SUGR", "NAPR", "AMIA", "AXPH", "GOUR", "CNFN",
    "ARAB", "OCPH", "SPIN", "DSCW", "AMER", "MICH", "GSSC", "KABO", "MFSC", "SVCE",
    "WCDF", "GDWA", "UNIT", "OFH", "UEFM", "AJWA", "SDTI", "SAIB", "ADCI", "INFI",
    "ASCM", "ELKA", "ELSH", "ACGC", "ACAMD", "ISMA", "LCSW", "SMFR", "CRST", "KZPC",
    "ZEOT", "ALRA", "DAPH", "CFGH", "ETRS", "EDFM", "GGCC", "NARE", "ATLC", "MPCO",
    "PHGC", "MILS", "ADPC", "GGRN", "RACC", "CEFM", "GPIM", "EHDR", "IDRE", "EALR",
    "NAHO", "UEGC", "AALR", "SNFC", "ECAP", "WKOL", "MOSC", "PRCL", "MAAL", "ODIN",
    "MENA", "SCFM", "DTPP", "NCCW", "CAED", "CERA", "GTWL", "DEIN", "SEIG", "NHPS",
    "SEIGA", "OBRI", "MEPA", "SIPC", "RREI", "NDRL", "AIDC", "ALUM", "AMII", "AFDI",
    "COSG", "EBSC", "ASPI", "POCO", "LUTS", "PRMH", "RTVC", "UNIP", "TANM", "MCRO",
    "GTEX", "KRDI", "ICID", "APSW", "TYCN", "AIHC", "ROTO", "SPMD", "MEGM", "ICLE",
    "RUBX", "EASB", "KWIN", "RAKT", "MOED", "AREH", "EEII", "CCRS", "EPCO", "GRCA",
    "GIHD", "ELWA", "ELNA", "DGTZ", "DCCC", "MMAT", "NEDA", "TRTO", "EPPK", "GMCI",
    "EOSB", "CPME", "COPR",
]


def sector_excluded(info):
    sector = (info.get("sector") or "").lower()
    industry = (info.get("industry") or "").lower()
    return next((kw for kw in EXCLUDED_KEYWORDS if kw in sector or kw in industry), None)


def ratio_screen(info):
    """Returns (passed: bool, reason: str)."""
    mcap = info.get("marketCap")
    if not mcap:
        return False, "no marketCap data (excluded, manual review)"
    debt = info.get("totalDebt") or 0
    cash = info.get("totalCash") or 0
    debt_ratio = debt / mcap
    cash_ratio = cash / mcap
    if debt_ratio >= RATIO_CUTOFF:
        return False, f"debt/mktcap {debt_ratio:.0%} >= {RATIO_CUTOFF:.0%}"
    if cash_ratio >= RATIO_CUTOFF:
        return False, f"cash/mktcap {cash_ratio:.0%} >= {RATIO_CUTOFF:.0%}"
    return True, f"debt/mktcap {debt_ratio:.0%}, cash/mktcap {cash_ratio:.0%}"


def screen_all():
    passed, rejected, review = [], [], []
    total = len(EGX_ALL_TICKERS)
    for idx, sym in enumerate(EGX_ALL_TICKERS, 1):
        ticker = f"{sym}.CA"
        if idx % 10 == 0 or idx == total:
            print(f"[PROGRESS] {idx}/{total} — {len(passed)} passed so far", flush=True)
        info = None
        for attempt in range(2):  # 2 tries max: retries were blowing the 40min budget at 3
            try:
                info = yf.Ticker(ticker).info
                if info and (info.get("sector") or info.get("industry")):
                    break
            except Exception as e:
                info = None
            time.sleep(1.0 * (attempt + 1))  # backoff on empty/error before retry

        if not info:
            review.append((ticker, "fetch failed after 2 retries (empty response)"))
            continue

        if not info or not (info.get("sector") or info.get("industry")):
            review.append((ticker, "no sector/industry data"))
            continue

        hit = sector_excluded(info)
        if hit:
            rejected.append((ticker, f"sector keyword: {hit}"))
            continue

        ok, reason = ratio_screen(info)
        name = info.get("longName") or info.get("shortName") or ticker
        sector = info.get("sector") or "N/A"
        if ok:
            passed.append({"ticker": ticker, "name": name, "sector": sector})
        else:
            (rejected if "no marketCap" not in reason else review).append((ticker, reason))

        time.sleep(0.3)  # be polite to Yahoo, avoid rate-limit bans across 224 tickers

    return passed, rejected, review


def main():
    passed, rejected, review = screen_all()

    print(f"[RESULT] {len(passed)} passed / {len(rejected)} rejected / {len(review)} need manual review "
          f"(out of {len(EGX_ALL_TICKERS)} total)")
    if review:
        print("\n[MANUAL REVIEW] no reliable data via yfinance, excluded fail-safe:")
        for t, r in review:
            print(f"  {t}: {r}")

    if not passed:
        print("[ERROR] zero tickers passed, aborting import")
        return 1

    domain = os.environ.get("WEBHOOK_DOMAIN", "https://meko568.pythonanywhere.com")
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        print("[ERROR] CRON_SECRET not set, not importing (dry run only)")
        return 1

    r = requests.post(
        f"{domain}/import-halal-list",
        params={"token": secret},
        json={"stocks": passed},
        timeout=30,
    )
    r.raise_for_status()
    print(f"[IMPORTED] {r.json()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
