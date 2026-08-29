v26.5 — STABILIZE TOP SETUPS ACROSS EMPTY REFRESHES
- Preserves the last non-empty Top Setups board in browser storage and silently restores it when a later scan unexpectedly returns zero or throws, so overnight/weekend refreshes do not make valid setups disappear at random.
- No card UI changes: no carried/ready labels, timestamps, or extra status text were added. A normal non-empty scan immediately replaces the saved board.
- Saved boards expire after four calendar days so stale setups cannot linger indefinitely across multiple sessions.
- Includes the v26.4 direction-agreement fix: CONFIRMED now requires Value Acceptance direction to equal STRAT continuity direction before receiving confirmed-lane rank weight.

v26.3 — TEMPORAL OPPORTUNITY LANES + CONTINUATION PRESERVATION
- Top Setups now distinguishes EARLY, CONTINUATION, CONFIRMED, and DEVELOPING opportunities instead of treating maximum confirmation as automatically best.
- EARLY includes failed daily 2-down/reclaim patterns such as the SLB/NOW examples, with extra weight for the second green follow-through day.
- CONTINUATION detects two-green-day higher-close breakouts, close-near-high behavior, and optional volume expansion so an already-started move is not discarded simply for being underway.
- The old blanket momentum-extension penalty is reduced for validated continuation plays; extension remains an execution warning rather than a reason to erase a strong continuation setup.
- Cards now explicitly explain whether the timing edge is early, continuation, or confirmed so the trader can choose between catching the turn and joining a healthy move.

v26.2b — TRADER BOARD DISPLAY FIXES
- Labels total option cost explicitly as /contract.
- Missing trigger/invalidation now render as — rather than JavaScript null -> $0 coercion.
- One-touch premium floors are labeled unreliable rather than showing misleading huge distance percentages.

v26.2 — EARLIER MOVE DETECTION
- Top Setups now considers Lagging-but-turning sectors before they fully graduate into Improving/Leading, reducing confirmation lag.
- Added daily failed-2D reversal detection: a 2-down that trades below the prior low but reclaims it, with extra priority when followed by a second green day/follow-through.
- Early price reversal strength now promotes candidates before the expensive Top-16 STRAT/value/premium stage instead of waiting for RRG confirmation after the move.
- Broadened the preliminary pool and allows turning-tail stocks to survive the first stock gate even before favorable-quadrant confirmation.
- Early Turn / speculative signals stay visible alongside confirmed Top Setups so the dashboard shows both EARLY and CONFIRMED opportunities.
- v26.1 display fixes are retained separately in the next patch if not already merged.

v25.37 — MOBILE TOP SETUP PREMIUM RELIABILITY
- Premium Support results are now cached server-side for 30 minutes by ticker/direction so a result completed on desktop can be reused by mobile without repeating the expensive Alpaca history work immediately.
- Added bounded retries for transient 429/502/503/504/network failures in the Top Setups premium layer.
- Failed mobile requests are now explicitly stored as retryable failures instead of silently disappearing into console-only errors / showing as not evaluated.
- Added a delayed rehydration pass that retries only missing/transient premium rows and re-renders Top Setups when results arrive.

v25.36 — STOCK RRG SUMMARY CLICK = FOCUS ONLY
- Extended the v25.35 focus-only behavior to the Stock RRG summary rows themselves. Clicking a stock in the summary now only highlights that ticker on the Stock RRG and dims the others; it no longer bubbles into the deep-dive/Volume Profile navigation handler.
- Interactive controls inside the row (buttons/links/inputs) are left alone. Clicking the selected stock again still clears the RRG focus.

v25.35 — STOCK RRG CLICK = FOCUS ONLY
- Fixed Stock RRG interaction so clicking a ticker label/endpoint now only highlights that stock's tail and dims the others in place.
- Removed the automatic jump into the chart / Volume Profile from Stock RRG clicks. The stock table/watchlist remains the explicit path into the deep-dive panels.
- Clicking the selected RRG ticker again still clears focus.

v25.34 — POST-EARNINGS 502 / INSTANCE-RESTART RESILIENCE
- Render logs showed the earnings 502 coinciding with the web service restarting: health checks stopped, Gunicorn relaunched, then health recovered. The browser previously surfaced that transient restart as a terminal “Unreadable earnings response (502)” error.
- Earnings Movers now automatically retries transient 429/502/503/504 responses with bounded backoff (2.5s, 6s, 12s) and tells the user the service is restarting/busy rather than failing immediately.
- Successful JSON behavior and existing server-side cached/stale-safe earnings logic are unchanged. Non-transient application errors still fail immediately.

v25.33 — IOS SAFARI NEWS CONTEXT DOMEXCEPTION HARDENING
- Moved News + Catalyst Context refresh from a JSON POST RequestInit path to a plain same-origin GET with a bounded encoded ticker list, matching the simpler Safari-safe request pattern used elsewhere.
- Added GET support to /api/news-context while retaining POST compatibility.
- Removed locale/Intl formatting from headline timestamps and only emits clickable links for valid http(s) URLs, reducing additional WebKit parsing surfaces.
- News errors now distinguish request-dispatch failures from unreadable/server responses instead of surfacing only the opaque DOMException.

v25.32 — IOS SAFARI STOCK SCREEN DOMEXCEPTION HARDENING
- Removed the scrollIntoView options-object overload again. iOS Safari/WebKit can throw the opaque “The string did not match the expected pattern” DOMException from this overload; all app scrolling now uses the legacy Boolean overload only.
- Simplified Stock Screen sector requests to plain same-origin relative fetch URLs with default Request options, removing another WebKit URL/Request construction surface from the PBW/ETF load path.
- Applied the same plain-fetch construction to the all-holdings ticker-search expansion path.

v25.31 — FIX TOP SETUPS AFTER PREMIUM DECOUPLING
- Fixed the regression where reducing premium-support weight caused previously valid XLK/IGV setups to fall below the same 55-point total-score bar and disappear.
- Added a separate qualificationScore that explicitly removes the premium adjustment. Top Setups now qualifies on the underlying setup only (RRG/sector alignment, options liquidity, value/structure, STRAT/GEX context), using a 45-point underlying bar.
- Premium still modifies the displayed/ranking score by a small amount, so AT SUPPORT can improve entry preference and AWAY FROM SUPPORT can slightly reduce rank, but neither can make or break qualification.
- Nearest Misses now shows both the underlying setup score and ranked score for easier debugging.

v25.30 — PREMIUM SUPPORT BECOMES ENTRY-QUALITY OVERLAY
- Removed premium-near-support as a qualification requirement/fallback gate. Top Setups qualification remains driven by RRG trajectory, liquid/tradable options, value/structure, STRAT and the broader confluence score.
- Reduced premium-support scoring weight so an attractive premium improves ranking but cannot dominate a strong underlying setup; being away from support is a small entry-quality penalty rather than automatic exclusion.
- Early Turn / Speculative Signals no longer require AT SUPPORT / NEAR SUPPORT to appear. Premium history is fetched opportunistically and displayed as entry context; the early RRG/sector signal survives when premium is normal, extended, or unavailable.
- Kept liquidity/tradability and genuinely adverse setup conditions as hard gates.

v25.29 — NEWS + CATALYST CONTEXT
- Added a Dashboard News + Catalyst Context panel inspired by the requested market-news / why-it-matters layout.
- Market News loads current general headlines from the existing Finnhub connection; ticker-specific news is fetched only for the strongest current Top Setup / Speculative Signal candidates.
- Each ticker headline gets a deterministic catalyst category (earnings, analyst, M&A, product, legal/regulatory, macro, company) and a concise "Why it matters" explanation. No LLM calls and no speculative claim about causality or direction.
- News is bounded (8 candidate tickers, 2 displayed headlines each), parallelized, and cached for 10 minutes to protect the small Render instance.

v25.28 — CONSOLIDATE SPECULATIVE SIGNALS + BROADEN INSTITUTIONAL ACTIVITY TO ALL SECTORS
- Site-wide audit found real redundancy: Early Turn Watch and Institutional Activity Top Setups were two separate, always-visible panels serving the same underlying purpose ("something to watch when Top Setups doesn't have a clean pick") via different detection methods, each requiring its own manual button click, neither aware of the other or of Top Setups' own Premium Support Watch / Nearest Misses fallback chain.
- Merged both into one panel ("SPECULATIVE SIGNALS") with a single "Scan speculative signals" button (runSpeculativeSignals) that runs both underlying detections together. Both original detection functions (earlyTurnQualifies/strongestLaggingSectors, _institutional_trade_sample) are unchanged — this is a UI/orchestration consolidation, not a rewrite of working detection logic.
- The panel now auto-collapses when Top Setups already found qualified (hardPass, score>=55) picks, and auto-expands when it didn't — verified with a direct simulation of both cases. No reason to keep an unconfirmed/speculative panel demanding attention on a day the main scan already found something solid.
- Institutional Activity was previously limited to whatever single ETF/group happened to be manually loaded in the drill-down view ("scan institutional [one sector] individually"). Broadened to reuse the same all-sectors candidate pool (window.allSupportiveCandidates) that Top Setups and Early Turn Watch already build — now scans the strongest candidates across every supportive sector in one pass instead of requiring a sector to be selected first.
- Feed Comparison was rendering above every single tab/view, not just the Dashboard — its purpose (deciding whether to keep the Alpaca upgrade) has largely been served now that the upgrade decision is made. Relocated it into the Dashboard view specifically, positioned below Speculative Signals rather than above everything.

v25.27 — PBW/TAN HOLDINGS FALLBACK HARDENING
- Invesco product pages can reject Render/server requests with HTTP 406 even when the same page works interactively in a browser. Added a full-list public holdings fallback for PBW/TAN before Finnhub/Yahoo so the Stock Screen remains usable when that happens.
- The fallback is still subordinate to the official issuer feed and feeds the existing persistent last-known-good cache after a successful retrieval.
- Sanitized terminal holdings errors so provider URLs/API tokens and raw upstream exception chains are no longer exposed in the UI.

v25.26 — DETERMINISTIC CONTEXT FOR INSTITUTIONAL ACTIVITY TOP SETUPS
- Rather than shipping a second, standalone "Dark Pool Prints" panel that would have overlapped with the already-built, more deeply-integrated "Institutional Activity Top Setups" scanner (multi-ticker, blended into rotation scoring), grafted the deterministic (non-AI) context piece onto that existing feature instead.
- Added dark_pool_spike_context(): reuses the app's own earnings-calendar merge and macro calendar to report "Xd after/before known earnings" and "nearby macro events" for a flagged large-print day — zero LLM calls, zero web search, zero new ongoing cost. Wired into _institutional_trade_sample() so context is only computed for genuine spikes (largest_multiple >= 2.0), not every row.
- Frontend: a compact context sub-row now appears beneath any flagged ticker's row in the Institutional Activity table when context exists.
- NOTE: versions between 25.22 and 25.25 are not yet documented here despite shipping real changes; worth a dedicated backfill pass.

v25.22 — ETF HOLDINGS RESILIENCE + POST-EARNINGS PROVIDER HARDENING
- Fixed TAN/PBW-style Invesco holdings parsing by installing the HTML parser stack used by pandas.read_html (lxml + BeautifulSoup + html5lib) and explicitly falling back between parsers.
- Official Invesco top-holdings tables with >=8 usable rows are now accepted as a clearly-labeled PARTIAL fallback instead of throwing the entire Stock Screen into Finnhub/Yahoo.
- Added a persistent last-known-good ETF holdings cache backed by the existing Neon/Postgres (SQLite locally). Successful issuer/Finnhub/Yahoo holdings are saved; if every live provider later fails, Stock Screen and Post-Earnings use the cached universe instead of going blank.
- This builds on v25.21's Post-Earnings 502 fix: the market-wide scan is already cached/stale-safe, and now its holdings-discovery stage is also resilient to provider outages.

v25.21 — POST-EARNINGS SCANNER 502 FIX
- Wrapped /api/postearnings-opportunities in cached_refresh_safe with a 5-minute TTL so repeat requests do not rerun the full discovery pipeline.
- A genuine refresh failure can now serve the last known-good result as stale, with the refresh error preserved, instead of returning a hard failure when cached data exists.

v25.20 — DIRECTION MISMATCH GUARD + PREMIUM STATE COLOR CODING
- Ported the v25.17 safety fixes onto the current v25.19 build without removing Early Turn Watch.
- Top Setup premium states are now visually coded: REVERSAL CONFIRMED / AT SUPPORT green, NEAR SUPPORT / CHEAP-UNPROVEN yellow, AWAY FROM SUPPORT red.
- If the institutional structure model direction conflicts with the setup thesis direction (value acceptance, then STRAT fallback), trigger/invalidation/target presentation is withheld and replaced with an explicit warning instead of showing a backwards ladder.

v25.19 — EARLY TURN WATCH: SECTOR-LED PATH
- Reported example (a trader's IGV/CRM call): the real trade thesis often operates one level up from any single stock's own RRG position — "IGV, the whole software sector ETF, has the strongest tail of any sector and is heading into Improving" — then picking a liquid holding within that sector (CRM) for options, regardless of CRM's own individual RRG state.
- v25.18's Early Turn Watch only looked at stocks whose OWN tail was turning, inside sectors already passing groupTrajectoryPass — it would never have surfaced CRM off of IGV's move, since IGV itself was still Lagging and wouldn't pass that gate.
- Added strongestLaggingSectors(): ranks Lagging-quadrant sectors by tail strength using the existing peer-relative sectorHeatScore composite, independent of groupTrajectoryPass. Verified against a reconstruction of the actual IGV scenario — correctly ranks IGV top, excludes a Lagging-but-not-turning sector and an already-Improving one (which belongs in Top Setups instead).
- Early Turn Watch now runs both paths together: STOCK-LED (a stock's own tail turning from Lagging) and SECTOR-LED (the single strongest Lagging-turning sector's top 8 holdings by weight, screened for options liquidity), each checked for premium support and clearly labeled by which path found them. A sector-context line ("Sector signal: IGV is the strongest Lagging sector...") appears above the list when a qualifying sector exists.

v25.18 — EARLY TURN WATCH (LAGGING + TURNING TAIL + PREMIUM NEAR SUPPORT)
- New, separate list distinct from Top Setups: finds stocks still reading Lagging on at least one RRG horizon whose own tail has just turned NE (RS-Ratio and RS-Momentum both rising) — the earliest possible "catch it before the crowd" signal, deliberately excluded from Top Setups since that gate requires an already-favorable (Improving/Leading) quadrant.
- Explicitly excludes anything that already qualifies for Top Setups' Full/Early alignment, so the two lists never duplicate each other. Verified against the target pattern (Lagging both horizons, fast tail turning NE), an already-Improving case, an already-EARLY-aligned case, and a still-rotating-out case — all four behave as intended.
- Reuses the candidate pool already fetched by the Top Setups scan (persisted via window.allSupportiveCandidates right after the liquidity filter) rather than re-scanning sectors/RRG/options from scratch — no additional network cost beyond the premium-support check itself, which only runs on the shortlisted top 10 by a dedicated early-turn score (RRG trajectory + option liquidity/IV).
- Requires the contract's premium to be REVERSAL CONFIRMED / AT SUPPORT / NEAR SUPPORT (not away from support or unproven) via the existing /api/premium-support endpoint.
- Each card is explicitly labeled "Speculative — has not yet met the Full/Early RRG alignment bar" so it isn't mistaken for a confirmed setup.

(v25.17, a direction-mismatch guard + premium state color coding fix, was prepared but not yet applied at the time of this version — apply both patches in either order, they don't conflict.)

v25.16 — TOP SETUPS DIAGNOSTIC: NEAREST MISSES
- Complements v25.15's premium-support-watch fallback: when even the watch tier is empty (true worst case — no A-quality setup and no qualifying premium watch), there was previously no way to tell "the scanner correctly found nothing today" apart from "something is silently broken."
- topSetupEvaluation() now returns gateFailures: a plain-language list of which specific gate(s) a candidate failed (RRG not aligned, tail rotating out, options not liquid/tradable, value acceptance rejected, invalid trade-plan structure, or a below-threshold score with the actual number shown).
- The empty-state Top Setups panel now shows a "Nearest misses" list in that true-worst-case scenario: the top 5 candidates by raw score regardless of hardPass, each with its score and specific failure reason(s).

v25.15 — PREMIUM SUPPORT WATCH FALLBACK
- Top Setups no longer goes completely blank when no stock passes the full A-quality hard gate but a finalist has a genuinely strong premium-floor setup.
- A-quality gating is unchanged. When zero A-quality names exist, the UI may instead show up to six PREMIUM SUPPORT WATCH cards from the already-vetted finalist universe when the contract is REVERSAL CONFIRMED / AT SUPPORT / NEAR SUPPORT with Premium Support Score >=60.
- Premium watches are explicitly labeled as stock-confirmation-pending scouting candidates, not trade calls.

v25.14 — PREMIUM SUPPORT FULL 7–90 DTE CHAIN
- Premium Support now builds its own complete 7–90 DTE Alpaca option chain instead of consuming the generic options-quality payload, which intentionally truncates its UI list to 120 contracts.
- This guarantees 36–60 and 61–90 DTE contracts can actually reach the premium-support bucket selector even when a ticker has a very large front-month chain.
- Response diagnostics now include contracts_considered as well as contracts_screened.

v25.13 — PREMIUM SUPPORT LONGER-DATED CONTRACTS
- Premium Support now searches its own 7–90 DTE option universe instead of inheriting the regular 7–35 DTE swing-selector chain. The regular options scanner remains unchanged.
- Candidate history is balanced across 7–35, 36–60, and 61–90 DTE buckets so front-month contracts cannot crowd out further-dated premium bases. Up to 12 contracts are inspected per ticker.
- Historical premium lookback expanded to 100 calendar days and the support/compression calculation now uses up to the latest 30 daily premium bars, improving detection of multi-week bases.

v25.12 — ALPACA OPTION HISTORY QUERY FIX
- Removed the unsupported `feed` query parameter from `/v1beta1/options/bars`; Alpaca rejects it with HTTP 400.
- Removed the obsolete OPRA-to-indicative retry for historical option bars.
- Premium Support can now reach the historical-bars response instead of failing on request validation.

v25.11 — PREMIUM HISTORY 400 FIX
- Fixed Alpaca historical option-bar requests using a future calendar date as the end bound when the Render server had crossed UTC midnight. Requests now use the actual current UTC timestamp (minus one minute) in RFC-3339 form.
- Alpaca option-history failures now preserve the API response body on the Top Setup diagnostic, so any remaining parameter or entitlement problem is explicit rather than a generic HTTP 400.

v25.10 — PREMIUM SCANNER VISIBILITY / FALLBACK
- Premium Support no longer fails silently on Top Setup cards: every finalist now shows the selected premium setup or an explicit unavailable/not-evaluated diagnostic.
- Historical option bars retry with Alpaca indicative feed if OPRA historical access is rejected.
- Relaxed the candidate prefilter from OI>=75 + volume>=10 + spread<=22% to OI>=50 + spread<=25%; current-day volume no longer blocks a contract that has useful historical premium structure.

v25.9 — PREMIUM SUPPORT / COMPRESSION SCANNER
- Added contract-level historical premium analysis for Top Setup finalists using Alpaca historical daily option bars. The scanner looks for liquid 7–35 DTE OTM calls/puts whose premium is near a repeatedly-tested 20-day support zone, with range compression and prior expansion potential.
- Premium Support Score combines distance to the premium floor, repeated support tests, range compression, reversal confirmation, prior-high expansion multiple, and execution quality. It is a confirmation layer rather than a hard gate because option premium support decays with theta/IV and is not equivalent to stock support.
- Top Setups runs premium history only on the final directional shortlist, reusing the already-cached options chain. Cards display contract, support zone, distance from support, tests, prior premium high/multiple, state, and score. New endpoint: GET /api/premium-support/<ticker>?direction=bullish|bearish.

v25.8 — TRIGGER PROXIMITY CHECK + SESSION LABELING (reported on VST)
- Trade-plan integrity gate now also rejects a trigger that's too far from current price, not just an incorrectly-ordered one. A 20-day high/low based trigger can pass the existing ordering check yet still be a stale relic of a large intra-window move (e.g. VST rallied from its 20-day low ~$122.62 to a spike near $176, then pulled back to $152.78 — the bearish trigger was still correctly ORDERED relative to invalidation/targets, but sat 3.35x ATR from spot, describing a price regime the stock had already moved on from). New check: reject if |spot-trigger| > 3x ATR, matching the plan's own target2 convention (trigger +/- 3*atr). Verified against the exact reported numbers (correctly rejects) and a normal healthy plan (correctly still passes, no false positive).
- The Chart Preview VAH/POC/VAL legend and the Value Acceptance card can legitimately show different numbers for the same ticker — they intentionally reference different sessions (latest available vs. Value Acceptance's deliberate prior-session reference for breakout classification), reading from the same underlying data but via different selection logic. This was previously unlabeled and looked like a bug. Both panels now show an explicit "Session: ..." label (with date where available) so the difference is visible instead of silently confusing.

v25.7 — FOLLOW-UP: PYCACHE UNTRACKING DIDN'T FULLY TAKE
- The v25.6 cleanup's .gitignore landed correctly and the workflow/script removal fully succeeded, but the compiled __pycache__/app.cpython-312.pyc binary was still tracked afterward — adding a .gitignore rule doesn't retroactively untrack a file that's already in the index; it likely got re-added by a blanket `git add` after being regenerated during patch validation. This explicitly runs `git rm --cached` on it again. No other changes.

v25.6 — REPO HYGIENE CLEANUP
- Removed 24 one-off GitHub Actions workflows, 23 one-off patch scripts (scripts/), 2 one-off scripts and a trigger marker file; the actual changes they made are already permanently baked into app.py.
- Added .gitignore (__pycache__/, *.pyc, .pytest_cache/, *.sqlite3, .env, editor/OS cruft) and untracked a compiled .pyc binary that had been accidentally committed.
- No changes to app.py itself; compiles cleanly and all 14 existing tests still pass.
- NOTE: this changelog has a real gap — v24.5 through v25.5 were never documented here despite shipping real changes. Worth a dedicated follow-up pass to backfill those entries from git history.

See git history for earlier release notes.