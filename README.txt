v34.4 — FASTER EARNINGS HISTORY + STRANGLE ODDS
- History: Yahoo + Nasdaq date lookups run in parallel (Nasdaq timeout 20 s → 8 s); dates cached a week and profiles 12 h, both
  persisted (L2) so restarts don't lose them; the post-earnings scan pre-builds History for all of its rows in the background,
  and the nightly job runs the scan so they're ready in the morning.
- Strangle odds (per 1-week / 2-week choice), in plain words:
  1) past reports: buy at the day-1 close, then did the stock move far enough within the hold (2 or 3 sessions) for either leg to hit +150%?
  2) volume profile: is price inside its 20-day value area (balanced, needs a push) or outside it (moves travel)?
     For each leg, is the path to its +150% price clear (or through thin volume), or is there a POC / value edge / high-volume node in the way?
  → High / Medium / Low. When both tenors fit the budget, the planner shows the one with better odds first.

