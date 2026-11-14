v36.4 — free options-flow read on every plan name (end-of-day volume vs open interest, repeat days, held positions), feeds the
Flow factor; earnings inside the trade window get their own group; track record adds a day-1-grace variant and with/against-group tags
(group flow computed as of each log date, so past trades are tagged too). Nightly stages: group tags, options flow.

v36.3 — Tonight's plan and the Top Setups grid sort by trigger status: triggered / in zone first, then trigger set (closest first),
then waiting for a pullback, then not ready / not tradable. Section headers and "x% away" on each card.

v36.2 — fill-day stop-outs are checked against hourly bars: if the stock hit the stop before it traded through the trigger, the trade
isn't stopped (daily bars can't tell the order and the grader assumed the worst, e.g. CNC 11/13). Past fill-day stop-outs regraded once.

