from pathlib import Path

APP = Path("app.py")
README = Path("README.txt")
RENDER = Path("render.yaml")

text = APP.read_text()


def replace_once(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    text = text.replace(old, new, 1)

# Version + threading import.
replace_once(
    "import io, math, time, traceback, os, sqlite3, json, hmac\n",
    "import io, math, time, traceback, os, sqlite3, json, hmac, threading\n",
    "threading import",
)
replace_once('APP_VERSION = "24.0.1"', 'APP_VERSION = "23.6"', "APP_VERSION")

# Cache stampede locking.
replace_once(
    '''CACHE = {}\nCACHE_TTL = 60 * 15\n\ndef cached(key, fn, ttl=CACHE_TTL):\n    now = time.time()\n    hit = CACHE.get(key)\n    if hit and now - hit[0] < ttl:\n        return hit[1]\n    val = fn()\n    CACHE[key] = (now, val)\n    return val\n\n\ndef cached_refresh_safe(key, fn, force=False, ttl=CACHE_TTL):\n    """Refresh without destroying the last known-good payload."""\n    now = time.time()\n    hit = CACHE.get(key)\n    if hit and not force and now - hit[0] < ttl:\n        return hit[1], False, None\n    try:\n        val = fn()\n        CACHE[key] = (now, val)\n        return val, False, None\n    except Exception as e:\n        if hit:\n            return hit[1], True, str(e)\n        raise\n''',
    '''CACHE = {}\nCACHE_TTL = 60 * 15\n_CACHE_LOCKS = {}\n_CACHE_LOCKS_GUARD = threading.Lock()\n\ndef _cache_lock(key):\n    # One lock per cache key so unrelated keys never block each other.\n    with _CACHE_LOCKS_GUARD:\n        lock = _CACHE_LOCKS.get(key)\n        if lock is None:\n            lock = threading.Lock()\n            _CACHE_LOCKS[key] = lock\n        return lock\n\ndef cached(key, fn, ttl=CACHE_TTL):\n    now = time.time()\n    hit = CACHE.get(key)\n    if hit and now - hit[0] < ttl:\n        return hit[1]\n    with _cache_lock(key):\n        now = time.time()\n        hit = CACHE.get(key)\n        if hit and now - hit[0] < ttl:\n            return hit[1]\n        val = fn()\n        CACHE[key] = (now, val)\n        return val\n\n\ndef cached_refresh_safe(key, fn, force=False, ttl=CACHE_TTL):\n    """Refresh without destroying the last known-good payload."""\n    now = time.time()\n    hit = CACHE.get(key)\n    if hit and not force and now - hit[0] < ttl:\n        return hit[1], False, None\n    with _cache_lock(key):\n        now = time.time()\n        hit = CACHE.get(key)\n        if hit and not force and now - hit[0] < ttl:\n            return hit[1], False, None\n        try:\n            val = fn()\n            CACHE[key] = (now, val)\n            return val, False, None\n        except Exception as e:\n            if hit:\n                return hit[1], True, str(e)\n            raise\n''',
    "cache locking",
)

# Alpaca visible-profile pagination.
replace_once(
    '''        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=30)\n        if r.status_code in (401,403):\n            try: detail=(r.json() or {}).get("message") or r.text\n            except Exception: detail=r.text\n            return {"sessions":[],"weeks":[],"source":source_tf,\n                    "error":f"Alpaca stock-bar access rejected: {detail or r.status_code}"}\n        r.raise_for_status()\n        raw=(r.json() or {}).get("bars") or []\n''',
    '''        raw=[]; token=None\n        for _ in range(6):\n            if token: params["page_token"]=token\n            r=requests.get(url,params=params,headers=alpaca_headers(),timeout=30)\n            if r.status_code in (401,403):\n                try: detail=(r.json() or {}).get("message") or r.text\n                except Exception: detail=r.text\n                return {"sessions":[],"weeks":[],"source":source_tf,\n                        "error":f"Alpaca stock-bar access rejected: {detail or r.status_code}"}\n            r.raise_for_status()\n            j=r.json() or {}\n            raw.extend(j.get("bars") or [])\n            token=j.get("next_page_token") or j.get("page_token")\n            if not token: break\n''',
    "Alpaca pagination",
)

# Finnhub ETF holdings pagination.
replace_once(
    '''    # Finnhub documents skip pagination and up to 100 holdings per call.\n    for skip in (0, 100, 200, 300, 400):\n''',
    '''    # Finnhub returns up to 100 holdings per call with skip-based pagination.\n    # Continue until the provider signals the true end of the fund, bounded by a\n    # generous safety ceiling so malformed pagination cannot loop forever.\n    skip = 0\n    for _ in range(30):  # safety ceiling: up to 3,000 holdings\n''',
    "Finnhub loop",
)
replace_once(
    '''        if new_count == 0 or len(rows) < 100:\n            break\n\n    all_rows = clean_equity_holdings(all_rows)\n''',
    '''        if new_count == 0 or len(rows) < 100:\n            break\n        skip += 100\n\n    all_rows = clean_equity_holdings(all_rows)\n''',
    "Finnhub skip increment",
)

# Historical RRG API caveat.
replace_once(
    '''            "holdings_as_screened":holdings_as_screened,\n            "results":rows\n''',
    '''            "holdings_as_screened":holdings_as_screened,\n            "results":rows,\n            "caveat":(\n                "Holdings reflect TODAY's fund composition applied retroactively to this "\n                "historical date, not the fund's actual holdings as of that date. This "\n                "biases the sample toward names that performed well enough to remain (or "\n                "become) top holdings today — treat forward-return stats here as "\n                "illustrative, not a rigorous backtest."\n            ) if mode=="stocks" else None\n''',
    "historical RRG caveat payload",
)

# Historical RRG UI caveat.
replace_once(
    '''    <div class="note" style="margin-top:9px">\n      The RRG is calculated only with price data available on or before the selected date. Historical stock mode defaults to top 20 holdings. Search/filters are instant; previously loaded Group/Stock, ETF, date and holdings-limit combinations repopulate from browser-session cache.\n    </div>\n''',
    '''    <div class="note" style="margin-top:9px">\n      The RRG is calculated only with price data available on or before the selected date. Historical stock mode defaults to top 20 holdings. Search/filters are instant; previously loaded Group/Stock, ETF, date and holdings-limit combinations repopulate from browser-session cache.\n    </div>\n    <div id="histCaveat" class="note" style="margin-top:6px;color:#f59e0b"></div>\n''',
    "historical caveat element",
)
replace_once(
    ''' st.textContent=(fromCache?"Cached · ":"")+detail;\n renderHistorical();\n''',
    ''' st.textContent=(fromCache?"Cached · ":"")+detail;\n const caveatEl=document.getElementById("histCaveat");\n if(caveatEl)caveatEl.textContent=j.caveat||"";\n renderHistorical();\n''',
    "historical caveat rendering",
)

APP.write_text(text)

entry = '''v23.6 — CACHE STAMPEDE, PAGINATION TRUNCATION, AND BACKTEST DISCLOSURE FIXES\n- Added per-key locking with double-checked cache re-validation so concurrent requests for the same uncached expensive resource do not stampede the upstream provider.\n- alpaca_visible_profiles() now paginates 5Min/1Min bar requests instead of silently truncating at Alpaca's 10,000-bar page limit.\n- finnhub_etf_holdings() now paginates until the true end of the holdings list, with a 3,000-holding safety ceiling instead of a fixed 500-holding cap.\n- Historical RRG stocks mode now explicitly discloses that current ETF holdings are applied retroactively, so forward-return stats are illustrative and not survivorship-free backtests. Groups mode is unaffected.\n\n'''
readme = README.read_text()
if not readme.startswith("v23.6 —"):
    README.write_text(entry + readme)

render = RENDER.read_text()
render = render.replace(
    "gunicorn --workers 1 --threads 4 --timeout 120 --keep-alive 5 runtime_v2402:app",
    "gunicorn --workers 1 --threads 4 --timeout 120 --keep-alive 5 app:app",
)
RENDER.write_text(render)

for obsolete in (Path("runtime_v2402.py"), Path("README_V24_0_2.txt")):
    if obsolete.exists():
        obsolete.unlink()

print("v23.6 source patch applied")
