v25.7 — FOLLOW-UP: PYCACHE UNTRACKING DIDN'T FULLY TAKE
- The v25.6 cleanup's .gitignore landed correctly and the workflow/script removal fully succeeded, but the compiled __pycache__/app.cpython-312.pyc binary was still tracked afterward — adding a .gitignore rule doesn't retroactively untrack a file that's already in the index; it likely got re-added by a blanket `git add` after being regenerated during patch validation. This explicitly runs `git rm --cached` on it again. No other changes.

v25.6 — REPO HYGIENE CLEANUP
- Removed 24 one-off GitHub Actions workflows, 23 one-off patch scripts (scripts/), 2 one-off scripts and a trigger marker file; the actual changes they made are already permanently baked into app.py.
- Added .gitignore (__pycache__/, *.pyc, .pytest_cache/, *.sqlite3, .env, editor/OS cruft) and untracked a compiled .pyc binary that had been accidentally committed.
- No changes to app.py itself; compiles cleanly and all 14 existing tests still pass.
- NOTE: this changelog has a real gap — v24.5 through v25.5 were never documented here despite shipping real changes. Worth a dedicated follow-up pass to backfill those entries from git history.

See git history for earlier release notes.