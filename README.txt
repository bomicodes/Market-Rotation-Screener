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

