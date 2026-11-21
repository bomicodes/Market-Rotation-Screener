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

