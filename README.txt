MARKET ROTATION SCREENER v9 — CLOUD / MOBILE
================================================

WHAT'S INCLUDED
- Fast RRG (10/5 daily) + Trend RRG (25/12 daily)
- Alignment labels
- Core sectors + industry/theme ETFs
- Holdings drill-down
- Post-earnings screen + historical earnings excursion profiles
- Mobile-responsive layout for iPhone/Safari
- Optional password protection
- Render-ready configuration (render.yaml)
- Gunicorn production server

IMPORTANT DATA NOTE
This app uses free/public sources (Yahoo/yfinance and issuer holdings pages).
These sources can rate-limit or temporarily fail. The app keeps last-good data
while the process stays alive, but a free cloud instance can restart/sleep,
which clears in-memory cache.

RENDER DEPLOYMENT — RECOMMENDED
1. Put the files in this folder into a GitHub repository.
2. In Render, create a new Blueprint or Web Service connected to that repo.
3. If using Blueprint, Render will detect render.yaml.
4. When prompted for SCREENER_PASSWORD, enter a password you want to use.
5. Deploy.
6. Open the HTTPS URL Render gives you.
7. On iPhone Safari: Share → Add to Home Screen.

Manual Render settings if not using render.yaml:
- Runtime: Python
- Build command: pip install -r requirements.txt
- Start command: gunicorn --workers 1 --threads 4 --timeout 120 app:app
- Health check: /health
- Environment variables:
    SECRET_KEY = any long random string
    SCREENER_PASSWORD = your desired password

LOCAL TESTING
pip install -r requirements.txt
python app.py
Then open http://127.0.0.1:8765

SECURITY
SCREENER_PASSWORD is optional. If it is blank/unset, the app has no login.
Do not put your actual password into the source code or Git repository.

PHONE USE
Once deployed, the Mac is no longer involved. Open the Render HTTPS URL from
any internet connection. Add it to your iPhone Home Screen for app-like access.

REFRESH MODEL
Normal page loads do NOT intentionally refresh hundreds of symbols.
Use the on-screen Refresh/Scan controls when you want updated calculations.

DISCLAIMER
DIY relative-rotation screening model; not the proprietary official RRG formula.
Screen first, then confirm price structure/premium/entry separately.
