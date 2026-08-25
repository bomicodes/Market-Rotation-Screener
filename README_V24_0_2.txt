v24.0.2 — CACHE STAMPEDE, PAGINATION TRUNCATION, AND BACKTEST DISCLOSURE FIXES

- Added per-key locking around the shared in-memory CACHE with double-checked re-validation. Concurrent requests for the same cold key now serialize around one expensive fetch while unrelated keys remain independent.
- Fixed alpaca_visible_profiles() pagination. A 6-month 5Min window can exceed Alpaca's 10,000-bar page limit; the runtime now follows next_page_token across bounded pages so recent sessions are not silently omitted.
- Fixed finnhub_etf_holdings() truncation. The old fallback stopped after five 100-row pages; it now paginates until the true end of data, with a 3,000-holding safety ceiling.
- Added an explicit Historical RRG caveat for Stocks mode: current ETF holdings are applied retroactively because point-in-time holdings snapshots are not available from the current free data sources. Forward-return statistics are therefore illustrative rather than survivorship-free backtests. Groups mode is unaffected.
- v23.5 STRAT continuity, rotation-noise, and constant-time login fixes remain preserved from the current v24 application.

IMPLEMENTATION NOTE
The current screener is a large monolithic app.py at v24.0.1. To avoid replacing/regressing that file, these reliability fixes are applied by runtime_v2402.py and Render now starts gunicorn against runtime_v2402:app. The runtime sets the effective app version to v24.0.2 while leaving the existing v24.0.1 feature set intact.
