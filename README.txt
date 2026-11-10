v34.6.2–34.6.3 — NIGHTLY DATA FRESHNESS
- The nightly job now refreshes the base scan (its cache key wasn't covered) and drops the 1-hour bulk OHLCV cache.
- When Yahoo's bulk daily bars lag a session, the missing sessions come from Alpaca, with IEX volume scaled to consolidated
  volume by each stock's own Yahoo/IEX ratio over the overlapping sessions.

