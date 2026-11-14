v36.2 — fill-day stop-outs are checked against hourly bars: if the stock hit the stop before it traded through the trigger, the trade
isn't stopped (daily bars can't tell the order and the grader assumed the worst, e.g. CNC 11/13). Past fill-day stop-outs regraded once.

