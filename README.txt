MARKET ROTATION SCREENER v9 — CLOUD / MOBILE
================================================

WHAT'S INCLUDED
- Fast RRG (10/5 daily) + Trend RRG (25/12 daily)
- Alignment labels
- Core sectors + industry/theme ETFs
- Holdings drill-down
- Post-earnings screen + historical earnings excursion profiles
- Mobile-responsive layout for iPhone/Safari
- Optional password protection
- Render-ready configuration (render.yaml)
- Gunicorn production server

IMPORTANT DATA NOTE
This app uses free/public sources (Yahoo/yfinance and issuer holdings pages).
These sources can rate-limit or temporarily fail. The app keeps last-good data
while the process stays alive, but a free cloud instance can restart/sleep,
which clears in-memory cache.

RENDER DEPLOYMENT — RECOMMENDED
1. Put the files in this folder into a GitHub repository.
2. In Render, create a new Blueprint or Web Service connected to that repo.
3. If using Blueprint, Render will detect render.yaml.
4. When prompted for SCREENER_PASSWORD, enter a password you want to use.
5. Deploy.
6. Open the HTTPS URL Render gives you.
7. On iPhone Safari: Share → Add to Home Screen.

Manual Render settings if not using render.yaml:
- Runtime: Python
- Build command: pip install -r requirements.txt
- Start command: gunicorn --workers 1 --threads 4 --timeout 120 app:app
- Health check: /health
- Environment variables:
    SECRET_KEY = any long random string
    SCREENER_PASSWORD = your desired password

LOCAL TESTING
pip install -r requirements.txt
python app.py
Then open http://127.0.0.1:8765

SECURITY
SCREENER_PASSWORD is optional. If it is blank/unset, the app has no login.
Do not put your actual password into the source code or Git repository.

PHONE USE
Once deployed, the Mac is no longer involved. Open the Render HTTPS URL from
any internet connection. Add it to your iPhone Home Screen for app-like access.

REFRESH MODEL
Normal page loads do NOT intentionally refresh hundreds of symbols.
Use the on-screen Refresh/Scan controls when you want updated calculations.

DISCLAIMER
DIY relative-rotation screening model; not the proprietary official RRG formula.
Screen first, then confirm price structure/premium/entry separately.

NEW IN v10 — POST-EARNINGS FIX
- Recent earnings discovery no longer depends only on yfinance.get_earnings_dates().
- It now checks Yahoo Finance's public daily earnings calendar and then uses
  per-ticker yfinance history as a fallback.
- SMH now prefers VanEck's own current holdings page instead of Yahoo's
  limited top-holdings list.
- Historical earnings profiles can explicitly inject the newly discovered
  recent earnings date if the ticker-history endpoint omitted it.
- Example validation: AMD is a current SMH holding and reported earnings
  August 4, 2026, so it should be discoverable in an SMH recent-earnings scan.

NEW IN v11 — OFFICIAL UNUSUAL WHALES INTEGRATION
- Added optional UW_API_TOKEN environment variable.
- If configured, recent earnings use the official Unusual Whales API first:
  GET /api/earnings/premarket
  GET /api/earnings/afterhours
- Historical event dates can also come from:
  GET /api/earnings/{ticker}
- The app displays the earnings source and report time when available.
- If no UW API token is configured, the app continues working with:
  Yahoo public earnings calendar -> yfinance ticker-history fallback.
- No scraping of undocumented/private Unusual Whales website endpoints is used.

RENDER
Add UW_API_TOKEN in Render Environment only if you have an Unusual Whales API token.
Leave it blank if you do not; the fallback sources remain active.

NEW IN v12 — FREE FINNHUB EARNINGS CALENDAR
- Added FINNHUB_API_KEY environment variable.
- Finnhub is now the preferred earnings-discovery source when configured.
- Finnhub's free tier currently provides one month of historical earnings and
  new updates, which fits the post-earnings 3/5/10/14-day scanner.
- One date-range request retrieves the earnings calendar for all holdings in
  the selected ETF rather than making one earnings request per stock.
- Source priority:
    1. Finnhub earnings calendar (free key)
    2. Unusual Whales API (optional, only if user already pays for it)
    3. Yahoo earnings calendar
    4. yfinance ticker history
- Finnhub EPS/revenue estimate/actual fields are retained when returned.
- All historical price excursion and Fast/Trend RRG calculations remain ours.

SETUP
1. Create a free Finnhub account/API key.
2. In Render -> your service -> Environment, add:
       FINNHUB_API_KEY = your_free_key
3. Save changes / redeploy.
4. Do NOT put the key in GitHub.

NEW IN v13 — FULL HOLDINGS AUDIT + EXHAUSTIVE EARNINGS VALIDATION
- Reworked holdings sources by ETF issuer:
  State Street official daily holdings:
    XLK XLC XLY XLF XLI XLB XLE XLV XLP XLU XLRE XBI XRT KRE XME XOP
  iShares official latest-holdings CSV:
    IGV IBB ITB IYT ITA
  VanEck official current holdings:
    SMH OIH
  Invesco official product holdings attempted first:
    TAN PBW
  Yahoo Finance TOP holdings is now last-resort only.
- IGV no longer relies on Yahoo top holdings. iShares currently reports over
  100 IGV holdings and its official CSV includes PLTR.
- Added "Audit holdings sources" button showing loaded count + source for every
  Layer-1 ETF.
- Post-Earnings now reports how many holdings were actually loaded and which
  holdings source was used.
- After calendar-level earnings sources run, every remaining ETF holding gets
  a targeted ticker-history validation before being excluded.
- Post-Earnings status displays source diagnostics (Finnhub / Yahoo / targeted).

v13.1 HOTFIX
- Fixed a JavaScript duplicate-variable declaration in runEarnings().
- The error prevented the browser from initializing any buttons/tabs.
- No screening logic was otherwise changed from v13.

NEW IN v14 — FULL POST-EARNINGS UNIVERSE + CALENDAR FALLBACKS
- Removed the 20/30/50 post-earnings holdings limit.
- Post-Earnings now scans every holding loaded for the selected ETF.
- Added Nasdaq public earnings calendar as a second full-calendar source.
- Earnings discovery order:
    Finnhub range calendar
    optional Unusual Whales
    Nasdaq daily calendar
    Yahoo daily calendar
    small targeted ticker-history fallback
- Removed hundreds of per-ticker validation requests that could overload a
  free Render instance.
- Post-Earnings request URL is now built with URLSearchParams and response text
  is parsed safely, eliminating the Safari "string did not match expected
  pattern" failure caused by the prior scan-size path.
- Holdings Audit now labels Yahoo TOP-holdings fallbacks as PARTIAL instead of
  showing them as fully valid.

NEW IN v15 — FAST POST-EARNINGS ARCHITECTURE
- Main Post-Earnings scan no longer calculates historical earnings profiles.
- Main scan now does only:
    full ETF holdings
    calendar-level earnings discovery
    one batch price download for recent reporters
    Fast + Trend RRG
- Historical 1/3/5/10/14-day mover profiles load only when the user taps
  "Earnings history" for a specific ticker.
- Added /api/earnings-history/<ticker> lazy-loading endpoint.
- Daily Nasdaq/Yahoo calendar calls are cached and parallelized.
- Removed per-ticker earnings-history requests from the main scan.
- Main table ranks recent reporters by current rotation score first.
- Historical mover label changes from LOAD DETAILS to HIGH/MODERATE/LOW after
  the user opens that ticker's history.
- This architecture is intended to avoid Render free-tier 502 timeouts on
  large ETFs such as IGV/IBB/KRE.

NEW IN v16 — EARNINGS HISTORY FIX
- Fixed the lazy Earnings history UI so loading/error/success states persist.
- A null profile no longer silently redraws the same placeholder.
- Historical earnings dates now merge:
    yfinance ticker earnings dates
    Nasdaq one-request earnings-surprise history fallback
    optional Unusual Whales history if configured
- No approximate earnings dates are fabricated.
- If fewer than 3 completed historical events are available, the row displays
  an explicit insufficient-data message and the dates that were found.
- Historical profile requests remain one ticker at a time, preserving the fast
  v15 main-scan architecture.

v16.1 — HOLDINGS REFRESH ONLY (stable v16 base)
- Earnings and historical-mover behavior is unchanged from v16.
- Rebuilt holdings source priority across the Layer-1 universe:
    Official issuer feed -> Finnhub FULL ETF holdings -> Yahoo TOP 10 partial.
- VanEck SMH/OIH now use the official downloadable holdings XLS workbook.
- Finnhub ETF holdings is the universal full-universe fallback and uses the
  existing FINNHUB_API_KEY already configured in Render.
- Yahoo top holdings is retained only as a last-resort PARTIAL fallback.
- This specifically prevents SMH/OIH/TAN/PBW from silently becoming 10-name
  universes when their issuer page parser fails.

v16.2 — ALL HOLDINGS
- Removed the Holdings selector from Stock Screen.
- Stock Screen now always scans all holdings returned by the best available source.
- Backend default is also all holdings.
- Earnings/history behavior remains unchanged from stable v16/v16.1.

v16.3 — CLICK-TO-FOCUS RRG
- Click/tap a ticker endpoint or label directly on either RRG chart.
- Selected ticker tail is drawn thicker/brighter with a larger label/endpoint.
- Other tails fade substantially so the selected path is easy to follow.
- Clicking a different ticker switches focus immediately.
- Clicking the selected ticker again clears focus and restores the full chart.
- No holdings, earnings, or historical-mover logic changed from stable v16.2.

v16.4 — RRG AXIS LABELS
- Added Relative Strength (RS) label to the horizontal RRG axis.
- Added Relative Momentum label to the vertical RRG axis.
- Added small weaker/stronger directional cues around the 100/100 cross.
- Click-to-focus interaction from v16.3 is unchanged.
- Holdings, earnings, and historical-mover logic are unchanged.

v16.5 — HISTORICAL RRG / BACKTEST MODE
- Added a separate Historical RRG tab; live Rotation and Post-Earnings logic unchanged.
- Pick an as-of date and reconstruct either Layer-1 groups vs SPY or stocks vs a selected ETF.
- RRG calculations use only prices on or before the selected date (point-in-time signal).
- Weekends/holidays snap to the previous trading session.
- Previous/Next day controls allow manual replay through history.
- +1D/+5D/+10D/+20D forward returns are calculated separately for study and do not feed the RRG.
- Historical chart retains click-to-focus tails and RS/Momentum axis labels.
