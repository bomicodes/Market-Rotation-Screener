"""v24.0.2 runtime reliability patch.

Integrates the v23.6 cache-stampede, pagination, and historical-RRG disclosure
fixes on top of the current v24.0.1 monolithic application without replacing
or regressing the existing app.py feature set.
"""

import json
import threading
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

import app as base

app = base.app
base.APP_VERSION = "24.0.2"
APP_VERSION = base.APP_VERSION

# ---------------------------------------------------------------------------
# Per-key cache stampede protection
# ---------------------------------------------------------------------------
base._CACHE_LOCKS = {}
base._CACHE_LOCKS_GUARD = threading.Lock()


def _cache_lock(key):
    # One lock per cache key so unrelated keys never block each other.
    with base._CACHE_LOCKS_GUARD:
        lock = base._CACHE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            base._CACHE_LOCKS[key] = lock
        return lock


def cached(key, fn, ttl=base.CACHE_TTL):
    now = time.time()
    hit = base.CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    with _cache_lock(key):
        now = time.time()
        hit = base.CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        val = fn()
        base.CACHE[key] = (now, val)
        return val


def cached_refresh_safe(key, fn, force=False, ttl=base.CACHE_TTL):
    """Refresh without destroying the last known-good payload."""
    now = time.time()
    hit = base.CACHE.get(key)
    if hit and not force and now - hit[0] < ttl:
        return hit[1], False, None
    with _cache_lock(key):
        now = time.time()
        hit = base.CACHE.get(key)
        if hit and not force and now - hit[0] < ttl:
            return hit[1], False, None
        try:
            val = fn()
            base.CACHE[key] = (now, val)
            return val, False, None
        except Exception as exc:
            if hit:
                return hit[1], True, str(exc)
            raise


base._cache_lock = _cache_lock
base.cached = cached
base.cached_refresh_safe = cached_refresh_safe

# ---------------------------------------------------------------------------
# Alpaca visible-range profile pagination
# ---------------------------------------------------------------------------
def alpaca_visible_profiles(ticker, period, chart_timeframe):
    """Build one visible profile per RTH session/week with complete pagination."""
    if not base.ALPACA_API_KEY or not base.ALPACA_API_SECRET:
        return {"sessions": [], "weeks": [], "source": None, "error": "Alpaca is not configured."}

    try:
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        now = datetime.now(et)
        days = {"1m": 35, "3m": 105, "6m": 205}.get(period, 35)
        source_tf = "1Min" if period == "1m" else "5Min"

        url = f"{base.ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
        params = {
            "timeframe": source_tf,
            "start": (now - timedelta(days=days)).isoformat(),
            "end": now.isoformat(),
            "adjustment": "raw",
            "feed": base.ALPACA_STOCK_FEED,
            "sort": "asc",
            "limit": 10000,
        }

        raw = []
        token = None
        seen_tokens = set()
        for _ in range(6):
            if token:
                params["page_token"] = token
            else:
                params.pop("page_token", None)
            r = requests.get(url, params=params, headers=base.alpaca_headers(), timeout=30)
            if r.status_code in (401, 403):
                try:
                    detail = (r.json() or {}).get("message") or r.text
                except Exception:
                    detail = r.text
                return {
                    "sessions": [], "weeks": [], "source": source_tf,
                    "error": f"Alpaca stock-bar access rejected: {detail or r.status_code}",
                }
            r.raise_for_status()
            payload = r.json() or {}
            raw.extend(payload.get("bars") or [])
            next_token = payload.get("next_page_token") or payload.get("page_token")
            if not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            token = next_token

        sessions = {}
        weeks = {}
        for bar in raw:
            ts = bar.get("t")
            if not ts:
                continue
            try:
                dt = pd.Timestamp(ts)
                if dt.tzinfo is None:
                    dt = dt.tz_localize("UTC")
                dt = dt.tz_convert("America/New_York")
            except Exception:
                continue
            mins = dt.hour * 60 + dt.minute
            if mins < 570 or mins >= 960:
                continue
            d = dt.date()
            sessions.setdefault(d, []).append(bar)
            iso = dt.isocalendar()
            weeks.setdefault((int(iso.year), int(iso.week)), []).append(bar)

        session_rows = 32 if chart_timeframe in ("1h", "4h") else 40
        session_items = []
        for d in sorted(sessions):
            p = base._profile_from_intraday_bars(sessions[d], d, rows_count=session_rows, value_area_pct=68)
            if p:
                p["source"] = f"Alpaca {base.ALPACA_STOCK_FEED.upper()} {source_tf} RTH"
                session_items.append({"date": str(d), "profile": p})

        week_items = []
        for wk in sorted(weeks):
            label = f"{wk[0]}-W{wk[1]:02d}"
            p = base._profile_from_intraday_bars(weeks[wk], label, rows_count=52, value_area_pct=68)
            if p:
                p["source"] = f"Alpaca {base.ALPACA_STOCK_FEED.upper()} {source_tf} weekly composite"
                week_items.append({"week": label, "profile": p})

        return {"sessions": session_items, "weeks": week_items, "source": source_tf, "error": None}
    except Exception as exc:
        return {"sessions": [], "weeks": [], "source": None, "error": str(exc)}


base.alpaca_visible_profiles = alpaca_visible_profiles

# ---------------------------------------------------------------------------
# Finnhub full ETF holdings pagination
# ---------------------------------------------------------------------------
def finnhub_etf_holdings(etf):
    """Retrieve the full Finnhub ETF holdings universe with bounded pagination."""
    if not base.FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY is not configured.")

    etf = etf.upper()
    url = "https://finnhub.io/api/v1/etf/holdings"
    all_rows = []
    seen_assets = set()
    skip = 0

    for _ in range(30):  # safety ceiling: up to 3,000 holdings
        params = {"symbol": etf, "skip": skip, "token": base.FINNHUB_API_KEY}
        resp = requests.get(url, params=params, timeout=25, headers={"User-Agent": "MarketRotationScreener/1.0"})
        resp.raise_for_status()
        payload = resp.json() or {}
        rows = payload.get("holdings") if isinstance(payload, dict) else None
        if rows is None and isinstance(payload, list):
            rows = payload
        rows = rows or []
        if not rows:
            break

        new_count = 0
        for row in rows:
            ticker = row.get("symbol") or row.get("asset") or row.get("ticker") or row.get("code") or ""
            ticker = str(ticker).strip().upper().replace(".", "-")
            if not ticker or ticker in seen_assets:
                continue
            seen_assets.add(ticker)
            new_count += 1
            name = row.get("name") or row.get("description") or ticker
            weight = row.get("percent") if row.get("percent") is not None else row.get("weight")
            try:
                weight = float(weight) if weight is not None else None
                if weight is not None and 0 < weight <= 1:
                    weight *= 100
            except Exception:
                weight = None
            all_rows.append({"ticker": ticker, "name": name, "weight": weight})

        if new_count == 0 or len(rows) < 100:
            break
        skip += 100

    all_rows = base.clean_equity_holdings(all_rows)
    if len(all_rows) < 10:
        raise RuntimeError(f"Finnhub returned only {len(all_rows)} usable holdings for {etf}.")
    return all_rows


base.finnhub_etf_holdings = finnhub_etf_holdings

# ---------------------------------------------------------------------------
# Historical-RRG survivorship disclosure: API + UI
# ---------------------------------------------------------------------------
HISTORICAL_STOCK_CAVEAT = (
    "Holdings reflect TODAY's fund composition applied retroactively to this historical date, "
    "not the fund's actual holdings as of that date. This biases the sample toward names that "
    "performed well enough to remain (or become) top holdings today — treat forward-return "
    "stats here as illustrative, not a rigorous backtest."
)


@app.after_request
def v2402_disclosures(response):
    try:
        # Add the caveat to historical-RRG JSON without replacing the underlying
        # endpoint, preserving every v24 field added after v23.6.
        if base.request.path == "/api/historical-rrg" and response.is_json:
            payload = response.get_json(silent=True)
            if isinstance(payload, dict) and payload.get("ok"):
                mode = base.request.args.get("mode", "groups")
                payload["caveat"] = HISTORICAL_STOCK_CAVEAT if mode == "stocks" else None
                response.set_data(json.dumps(payload, separators=(",", ":"), default=str))
                response.headers["Content-Type"] = "application/json"

        # The UI lives inside app.py as one HTML string. Inject the disclosure
        # element and two-line renderer at response time so the v24 monolith does
        # not need a risky full-file rewrite.
        ctype = response.headers.get("Content-Type", "")
        if response.status_code == 200 and "text/html" in ctype:
            text = response.get_data(as_text=True)
            note = (
                '<div class="note" style="margin-top:9px">\n'
                '      The RRG is calculated only with price data available on or before the selected date. Historical stock mode defaults to top 20 holdings. Search/filters are instant; previously loaded Group/Stock, ETF, date and holdings-limit combinations repopulate from browser-session cache.\n'
                '    </div>'
            )
            if 'id="histCaveat"' not in text and note in text:
                text = text.replace(note, note + '\n    <div id="histCaveat" class="note" style="margin-top:6px;color:#f59e0b"></div>', 1)
            marker = 'st.textContent=(fromCache?"Cached · ":"")+detail;'
            injection = marker + '\n const caveatEl=document.getElementById("histCaveat");\n if(caveatEl)caveatEl.textContent=j.caveat||"";'
            if 'caveatEl.textContent=j.caveat' not in text and marker in text:
                text = text.replace(marker, injection, 1)
            response.set_data(text)
            response.headers["Content-Length"] = str(len(response.get_data()))
    except Exception:
        base.app.logger.exception("v24.0.2 disclosure patch failed")
    return response
