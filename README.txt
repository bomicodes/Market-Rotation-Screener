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