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

v16.6 — HISTORICAL RRG USABILITY
- Historical stock RRG defaults to top 20 ETF holdings.
- Added Historical Holdings selector: 20 / 50 / All.
- Holdings limit is applied before historical price downloads to reduce data load.
- Historical table rows are clickable and use the same tail-focus behavior as chart clicks.
- Clicking a row again clears focus; clicking another row switches focus.
- Chart clicks and table-row highlight stay synchronized.
- Groups-vs-SPY mode ignores/disables the holdings selector.

v16.7 — MAIN ROTATION HOLDINGS LIMIT
- Main Rotation Screen stock-level RRG now defaults to top 20 holdings.
- Added live Holdings selector: 20 / 50 / All.
- Limit is applied before the stock-level RRG download/calculation.
- Changing the selector reloads the selected ETF/group automatically.
- Layer 1 groups-vs-SPY RRG remains full/unlimited.
- Historical mode keeps its separate 20 / 50 / All selector.
- Click-to-focus, earnings, and historical-mover logic are unchanged.

v16.8 — SIMPLE TAIL TRAJECTORY + RRG FILTERS
- Added Tail Trajectory to RRG rows using the most recent 3-point vector:
    Rotating In = recent tail points northeast (RS and momentum both rising)
    Rotating Out = recent tail points southwest (RS and momentum both falling)
    Neutral = all other recent directions
- Added quadrant filter to live stock RRG:
    All / Leading / Improving / Weakening / Lagging
- Added tail filter to live stock RRG:
    All / Rotating In / Rotating Out
- Added the same quadrant and tail filters to Historical RRG.
- Filters use the ticker's current endpoint state, but the full tail remains visible.
- Historical table and chart click-to-focus behavior is preserved.
- Earnings and historical-mover logic remain unchanged.

v16.9 — HISTORICAL FILTER VISIBILITY
- Confirmed/added Historical RRG filters:
    Quadrant: All / Leading / Improving / Weakening / Lagging
    Tail: All / Rotating In / Rotating Out
- Filters apply to both the Historical RRG chart and historical results table.
- Matching names retain their full historical tail.
- All v16.8 logic remains unchanged.

v17.0 — POTENTIAL TURN
- Adds Tail filter: Potential Turn 👀 to live RRG and Historical RRG.
- Potential Turn is intentionally pre-confirmation:
  endpoint is still Lagging, but the recent tail has made a meaningful hook
  from prior deterioration into rightward / northeast improvement.
- Designed to surface the PANW / CRWD / RBRK-style pattern before the
  next-day Improving/Lagging classification can appear.
- Potential Turn is a watch signal, not the same as confirmed Rotating In.
- Existing Rotating In / Rotating Out logic and all v16.9 features preserved.

v17.1 — SEARCH + NO-RELOAD TOGGLING
- Added ticker/company search to live stock RRG and Historical RRG.
- Search, quadrant filters, and tail filters are local only and never refetch.
- Browser-session cache stores:
  * Market / Groups-vs-SPY payload
  * Live ETF stock RRG by ETF + 20/50/All
  * Historical RRG by mode + ETF + date + 20/50/All
- Returning to an already loaded universe repopulates immediately.
- Historical Group vs SPY <-> Stocks within ETF toggles reuse cached results.
- Historical ETF and holdings-limit changes also reuse prior results when available.
- Explicit Refresh buttons bypass cache.
- Potential Turn / Rotating In / Rotating Out logic unchanged from v17.0.

v17.2 — HISTORICAL FILTER LAYOUT
- Historical RRG Quadrant is directly before Tail.
- Quadrant + Tail are visually grouped on the same line.
- Search, browser-session cache, Potential Turn, and all v17.1 behavior unchanged.

v17.3 — LIVE-ONLY WATCHLIST
- Added ☆/★ bookmark button to the live stock RRG table only.
- Added compact Live Watchlist panel under the live RRG.
- Watchlist persists via browser localStorage.
- Saved fields: ticker, ETF, Fast quadrant, Trend quadrant, tail signal.
- Clear-all and individual remove supported.
- Historical RRG is unchanged and has no bookmark feature.

v17.4 — LIVE + HISTORICAL ROW FOCUS
- Live RRG table rows are now clickable.
- Historical RRG table retains/standardizes the same click-to-focus behavior.
- Clicking a ticker row or ticker on the RRG keeps ALL currently displayed tails on chart.
- Non-selected tails dim; selected tail/label/endpoint are emphasized.
- Clicking the selected ticker again clears focus and restores normal display.
- Row highlighting stays synchronized with chart clicks.
- Bookmark stars are isolated from row-click focus.
- Filters, cache, watchlist, and all signal logic unchanged.

v17.5 — LIVE RRG CLICK-FOCUS FIX
- Fixed live table-row focus by using an explicit focus ticker during canvas redraw.
- Live row click and chart click now share one focus function.
- Historical row/chart click uses the same mechanism for consistency.
- Selected tail is slightly thicker/larger; all other displayed tails dim more strongly.
- Clicking the selected ticker again clears focus and restores all tails normally.
- Watchlist, filters, caching, and signal logic unchanged.

v17.6 — SECTOR RRG CLICK-FOCUS FIX
- Sector/Groups-vs-SPY table rows are now wired to RRG focus.
- Clicking XLK (or any sector/theme row) highlights its full tail and dims all other displayed tails.
- The same click still selects that ETF and loads its holdings, preserving existing workflow.
- Clicking the selected sector again clears the RRG highlight.
- Clicking the ticker directly on the sector RRG also focuses it and loads its holdings.
- Sector table row highlighting stays synchronized with chart selection.
- Live stock, Historical RRG, watchlist, filters, cache, and signals unchanged.

v17.7 — SUBTLE RRG FOCUS
- Selected ticker tail now keeps the same line thickness, endpoint size, and label size as default.
- Focus is created primarily by dimming all other displayed tails.
- Applies consistently to Sector, Live Stock, and Historical RRG charts.
- All v17.6 click behavior, caching, filters, and watchlist unchanged.

v18.0 — OPTIONS SCREEN + EARNINGS HORIZON FIX

Render Environment:
  APCA_API_KEY_ID=<Alpaca API key>
  APCA_API_SECRET_KEY=<Alpaca API secret>
Optional:
  ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets

Options:
- 7–30 DTE Alpaca indicative screening.
- Per-ticker Options button and Scan options for up to 25 displayed live RRG names.
- Shows bid/ask, spread %, volume, OI, IV, delta and liquidity.
- Liquid = OI>=500, volume>=100, spread<=10%.
- Tradable = OI>=100, volume>=25, spread<=15%.
- ATM IV is compared with annualized 20D realized volatility:
  Cheap/Crushed <0.90 IV/RV; Normal 0.90–1.25; Elevated 1.25–1.60; Juiced >1.60.
- This is not IV rank/percentile; use Webull OPRA for the final live quote.

Earnings:
- Incomplete horizons now show as — and are never reused as longer horizons.
- Stats use only completed 14D earnings events; recent incomplete events can still display.

v18.1 — SAFE STARTUP / RENDER HARDENING
- Home page and /health make ZERO Alpaca or external data requests.
- Options remain fully lazy: only Options / Scan options buttons call Alpaca.
- Added /api/diagnostics and version=18.1 to /health.
- Added APCA_API_KEY_ID and APCA_API_SECRET_KEY to render.yaml.
- Gunicorn timeout restored to 120 seconds.
- Added server-side 500 logging to make any future Render traceback visible.
- Preserves v18.0 options screen and DDOG/incomplete earnings-horizon fix.

v18.2 — LOGIN 500 FIX
- Fixed CSS accidentally embedded in the login-page Python f-string.
- Resolves Render traceback: NameError: name 'cursor' is not defined.
- Preserves the live/historical RRG focus behavior, options module, and earnings-horizon fix.

v18.3 — CLEAN LOGIN REBUILD
- Replaced the entire login function/template with a clean non-f-string HTML template.
- Eliminates all CSS-expression NameErrors in /login.
- Preserves v18 options module, RRG behavior, caching, and earnings fixes.

v18.4 — MARKET REGIME + ALPACA SETUP

MARKET REGIME
- Existing RRG calculations are unchanged.
- Added compact context cards:
    SPY trend
    RSP/SPY breadth
    IWM/SPY small-cap participation
    QQQ/SPY growth leadership
    HYG/LQD credit risk appetite
    10Y Treasury yield level + 5D/20D trend
- Added context-only Risk Appetite label:
    Risk-On / Mixed / Risk-Off
  This does NOT feed into RRG calculations or ranking.

ALPACA
- Options module from v18 remains intact.
- Added direct signup link in the Options panel.
- Render variables are already defined in render.yaml:
    APCA_API_KEY_ID
    APCA_API_SECRET_KEY
- The site works without keys; only Options/Scan options require them.
- Free Alpaca indicative data is for screening only; verify live OPRA in Webull before entry.

v18.5 — OPTIONS SETUP UX
- Added prominent Connect Alpaca / Get API Key button.
- Options ticker clicks and Scan options automatically scroll to the Options panel.
- App checks /api/diagnostics to show Alpaca connected/not-connected state.
- If Alpaca keys are missing, Options actions show setup instructions instead of appearing inactive.
- No RRG calculations changed.

v18.6 — OPTIONS SCAN RESULTS FIX
- Scan options now scans the full currently filtered live-RRG ticker set (up to 100 safety ceiling), not only the first 25.
- Scan results render in their own ranked summary table instead of leaving the panel stuck on one active ticker.
- Ranked by a simple options-quality score using liquidity, IV state, and number of liquid/tradable contracts.
- Each scan result has Analyze Ticker for full 7–30 DTE chain drill-down.
- Renamed the per-row action to Analyze Ticker.
- Replaced the ambiguous Check badge with Not scanned.
- No RRG calculations changed.

v18.7 — LIVE TICKER SEARCH FIX
- Live RRG ticker search now searches the selected ETF's FULL holdings universe,
  even when the display is set to top 20 or top 50 holdings.
- Search remains instant for already-loaded names, then automatically loads/caches
  the full ETF holdings set after a 250 ms debounce.
- Search matches ticker or company name.
- Clearing search immediately restores the normal selected holdings view.
- Switching ETFs resets the expanded search universe correctly.
- No RRG calculations changed.

v18.8 — OPTIONS CONTRACT UI
- Human-readable contracts: Ticker · Expiration · Strike + C/P.
- Example: XOM · Aug 21 · $165C.
- Mid premium is now a dedicated prominent column.
- Bid/ask remain visible beside Mid.
- Raw OCC option symbol is demoted to small secondary text.
- Mobile layout gives the contract cell more room.
- No options scoring, data source, or RRG calculations changed.

v18.9 — OPTIONS UI CLEANUP
- Removed % vs spot / ITM-OTM style contract context from the contract display.
- Contract labels now read like: DVN · Aug 21 · $45 Call.
- Raw OCC symbol remains in small secondary text.
- Blue Alpaca setup button automatically hides after the app confirms Alpaca is connected.
- Connected status remains visible in the green status box.
- No options scoring, data source, or RRG calculations changed.

v18.10 — UNDERLYING PRICE CONTEXT
- Added a visible underlying-price strip directly above the options contract table.
- Example: UNDERLYING PRICE · DVN $45.36.
- Keeps contract labels simple while letting the user compare stock price vs strike manually.
- No ITM/ATM/OTM percentages or badges added.
- No options scoring, data source, or RRG calculations changed.

v18.11 — OPTIONS HEADER + CALLS/PUTS
- Underlying price moved into the compact options filter/header row.
- Removed the separate large underlying-price box.
- Calls + puts remain the default contract view.
- Type filter can still switch to Calls only or Puts only.
- Contract rows are ordered by expiration, strike, then option type for easier scanning.
- Increased visible chain limit to 120 contracts so both calls and puts can display together.
- No options scoring, data source, or RRG calculations changed.

v18.12 — CURRENT PRICE WORDING
- Renamed options price context from Underlying to Current price.
- No other UI, options, or RRG logic changes.

v18.13 — SECTOR SWITCH DATA-INTEGRITY FIX
- Fixed stale sector holdings appearing after switching ETFs.
- Each live-sector request is now tagged with the ETF/request sequence that started it.
- Late responses from a previously selected ETF are ignored.
- Old rows are cleared immediately while the newly selected ETF loads.
- Search state is reset when changing ETFs so a prior sector's expanded holdings cannot bleed into the new one.
- No RRG calculations changed.

v18.14 — WEEKLY OPTIONS
- Expanded options chain from 7–30 DTE to 0–30 DTE.
- Includes weekly contracts with fewer than 7 calendar days remaining.
- Calls and puts remain included.
- Cache key bumped so previously cached 7–30 DTE scans do not hide the new weeklies.
- No RRG calculations or options liquidity thresholds changed.
