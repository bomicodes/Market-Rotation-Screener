v34.7 — LIQUIDITY READ + NIGHTLY BACKUPS
- Options liquidity: besides the spread-based rule, a chain counts by participation — Liquid with ≥ 4 near-the-money contracts
  (within 10%) having OI ≥ 1,000, volume ≥ 100 and spread ≤ 40%; Tradable with ≥ 3 having OI ≥ 300, volume ≥ 25, spread ≤ 50%.
  The free indicative feed (and every feed after the close, when the nightly runs) shows spreads far wider than the live market,
  which had liquid names like PYPL reading "Thin".
- Nightly: GitHub cron at 21:50, 22:25, 23:40 and 01:15 UTC (later ones exit if the run already happened), plus an in-app
  watchdog that starts the run 45+ min after the close if nothing started.

