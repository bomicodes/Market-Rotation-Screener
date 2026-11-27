v38.0 — SPY/QQQ index pullback lane, the rules backtested in Vela 2018-2026 (trend setup, touch of SMA20 +/- 0.25 ATR within
10 days, a close in the top 40% of the bar beyond the zone within 3 bars, SPY on the right side of its 50-day; half at +0.5R then
break-even). SPY +9.6R / QQQ +4.5R; single stocks and XLP were about break-even, so the lane is index-only. Nightly stage
"index pullback" logs signals as lane index_pullback; the digest prints [digest index] (alert level, in the zone, signal, in a trade).

v37.15 — every filled setup is also graded with a "half at +0.5R, stop to break-even" exit on the same fill (before the partial:
the plan's stop and, for trigger plans, the failed-2U/2D close; after it: break-even, target or time). Shown in the Track record panel
and printed in the digest ([digest pbe]) next to the plan as-is, by lane and calls vs puts. Closed trades are regraded once by the nightly.

v37.12 — Focus filter on Tonight's plan (the default): pullbacks with the trend, failed-bounce puts and the latest session's Strong
post-earnings strangles; everything else stays under All.

v37.11 — reversal/counter-trend ideas hidden from Tonight's plan (still logged; a "show" link brings them back). Track record adds
the share of trades whose option reached +50%. Digest adds win-rate slices (lane, direction, group tag, market read, R/R and pairs).

v37.10 — strangle day 1 never moves forward to a heavier day 2 (KSS/ANF/DKS were re-logged 11/24); a ticker is logged at most once
per 7 days and the duplicates were removed.

v37.9 — MAX_CONTRACT (Render env) sets the max premium per contract for the nightly board, logging and digest; the page adopts it
(set to $200 on 11/24).

v37.8 — real breadth from the ~350-stock group universe (% above 20/50-day, 20-day highs vs lows), saved by the group-flow stage
and printed in the digest next to RSP/SPY, IWM/SPY, QQQ/SPY and credit.

v37.7 — plain-text digest written to the server log (board, open trades marked midday, closed trades, lane records, strangles):
after each nightly, at 12:30 ET on trading days, and on demand via GET /api/digest/print (public, returns no data, 5-min limit) or the
"Print digest" workflow. Lets the board be checked through the Render logs without opening the site.

v37.6 — strangle tracker confirms day 1 by the volume spike (calendar session or a neighbour, >= 1.5x the 20-day average);
calendars were a day off for PANW 11/18. Logs which reporters missed the day-1 check.

v37.5 — post-earnings strangle tracker: each night logs the strangle the screener would buy at the day-1 close for that session's
reporters and grades it on real option bars (leg +150%, -35% combined, time stop, exit-by); Track record panel by follow-through read
and odds. New day-1 follow-through read (close location, size vs usual reaction, with/against the market) on strangle plans and badges.

v37.4 — the nightly logs the board itself (wide scan, laggards, bases, earnings drift, put side) with the page's rules (shorts only when
the tape allows them); nights nobody opened the page (11/18, 11/19) and the put side were missing. 11/17 put-side setups back-filled.

v37.3 — a trigger plan is never "buy now" until price has crossed the trigger (a close a few cents short of it turned the Webull order
into an immediate buy limit, e.g. BSX/VST put-side setups 11/17). Entry-plan and put-side caches bumped.

v37.2 — put side: rotation-out leaders breaking down, catch-down laggards in falling groups, failed bounces into the falling 20-day
(/api/put-side, nightly stage "put side", shown when the market read allows shorts). Puts in an inflated market use a near-the-money
strike and a 10-session max hold. Track record splits calls vs puts per lane. Entry plans that fail to load retry and show a short message.

v36.7 — the watchdog thread was running in the gunicorn master (the module is loaded before the fork), invisible to the status page;
it now starts in the worker on the first request. KV reads expire after 30 s so status written by another process shows up.

v36.6 — nightly watchdog heartbeat: first check 90 s after boot, then every 5 min, logs each start/decision, and /api/nightly/status shows
its state (it failed to start the 11/16 run and left no trace).

v37.1 — catalysts: past-event recaps (takeaways, highlights, "at X investor day:") are dropped, undated items must read as a future
event ("to present", "will host", "upcoming"), numeric dates (11/4) are parsed, and a no-year date shortly before the article is
treated as already past. Cache key catalysts-v37:2.

v37.0 — post-earnings drift lane ("pead"): reporters 2–10 sessions after a ≥ 4% gap on ≥ 2× volume that closed strong and still hold
≥ 50% of the move; trigger = inside-bar break, else the post-earnings high; stop ≥ 1 ATR (≤ 2.5 ATR); target 2R. Contracts come from the
existing option_entry_plan (unchanged). Shows on Tonight's plan ("Earnings drift" filter) and the Earnings tab, logged and graded like
every lane. New Catalysts tab: 14-day earnings (Finnhub + Nasdaq, group universe + board), company events from Alpaca news headlines
(investor/analyst days, conference presentations, sales/deliveries, FDA, product events) and macro; catalyst line on plan cards.
Nightly stages: earnings drift, catalysts.

v36.5 — track record adds a "bought the dip instead" variant for trigger plans: a resting limit halfway to the stop (or the logged VP
level), stop/target/time exits, R in the original plan's units; shows fill rate and winners it would have missed. Back-filled.

v36.4 — free options-flow read on every plan name (end-of-day volume vs open interest, repeat days, held positions), feeds the
Flow factor; earnings inside the trade window get their own group; track record adds a day-1-grace variant and with/against-group tags
(group flow computed as of each log date, so past trades are tagged too). Nightly stages: group tags, options flow.

v36.3 — Tonight's plan and the Top Setups grid sort by trigger status: triggered / in zone first, then trigger set (closest first),
then waiting for a pullback, then not ready / not tradable. Section headers and "x% away" on each card.

v36.2 — fill-day stop-outs are checked against hourly bars: if the stock hit the stop before it traded through the trigger, the trade
isn't stopped (daily bars can't tell the order and the grader assumed the worst, e.g. CNC 11/13). Past fill-day stop-outs regraded once.

