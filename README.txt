v34.4.2 — EARNINGS REACTION DAY FIX
- Date-only earnings dates (no time of day) now map to the session with the bigger reaction (the date or the next session),
  so an after-close report's gap is "day 1", not "day 2". Affects the History table and the strangle odds.
- Two listed dates within 30 sessions are one report: the one with the bigger reaction is kept. History rows show the reaction session's date.

