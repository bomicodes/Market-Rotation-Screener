from pathlib import Path
p=Path('README.txt'); s=p.read_text()
if s.startswith('v24.0 —'): raise SystemExit(0)
header='''v24.0 — SERVER-SYNCED WATCHLIST, REGRESSION TESTS, GLOSSARY, SOURCE HEALTH, MACRO CALENDAR
- Watchlist now syncs to the server (Postgres/SQLite, same backend as setup snapshots) instead of living only in one browser's localStorage. Bookmarks made on one device now show up on another; localStorage is kept as an instant-load/offline cache, not the source of truth. New endpoints: GET/POST/DELETE /api/watchlist.
- Added tests/test_core_logic.py: 10 regression tests for PEAD directional persistence/reversion classification and STRAT scenario classification, using synthetic OHLC data and monkeypatched dl_ohlc — no network calls required. Verified against the real functions (all pass) and confirmed they actually catch regressions, not just pass trivially. Run with `pytest tests/test_core_logic.py -v` (requirements-dev.txt added, kept separate from production requirements.txt).
- Added a glossary/tooltip layer for acronym-heavy labels (RRG, GEX, VAH, POC, VAL, STRAT, FTC, IV, DTE). Applied to the page intro, VAH/POC/VAL cards, the STRAT section header, and the FTC badge — non-interactive labels only, deliberately skipping clickable nav tabs to avoid a competing tap action.
- Added a data-source health strip (top of Dashboard) tracking Alpaca (stocks/options), Finnhub, yfinance, Unusual Whales, and the Nasdaq/Yahoo earnings-calendar fallback. New /api/source-health endpoint. Caught and fixed a same-second timestamp-tie bug during testing where a success immediately following a failure could still report "degraded" — status is now tracked via an explicit last-event flag, not a timestamp comparison.
- Added a macro calendar card (FOMC/CPI/jobs report dates) to the Dashboard sidebar. Dates are a curated, manually-maintained list (same pattern as SECTOR_HOLDING_SUPPLEMENTS) sourced from federalreserve.gov and BLS press releases, verified 2026-08-25 — deliberately limited to officially-confirmed dates rather than guessing future CPI/jobs dates, since an invented date could give false confidence about a "clear" week. New /api/macro-calendar endpoint.

'''
p.write_text(header+s)
