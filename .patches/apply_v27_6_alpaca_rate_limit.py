from pathlib import Path

p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "27.5"' in s

# imports/version
s=s.replace('import io, math, time, traceback, os, sqlite3, json, hmac, threading\n','import io, math, time, traceback, os, sqlite3, json, hmac, threading\nfrom collections import deque\n',1)
s=s.replace('APP_VERSION = "27.5"','APP_VERSION = "27.6"',1)

# Insert shared Alpaca rate gate immediately before alpaca_headers().
marker='def alpaca_headers():\n'
assert marker in s
rate='''_ALPACA_RATE_LOCK = threading.Lock()\n_ALPACA_CALL_TIMES = deque()\n# Keep a deliberate safety margin below the account-level request ceiling.\n_ALPACA_MAX_PER_MIN = 170\n_ALPACA_WINDOW_SEC = 60.0\n\ndef _alpaca_rate_gate():\n    """Block until one more Alpaca request fits inside the shared sliding window."""\n    while True:\n        with _ALPACA_RATE_LOCK:\n            now=time.time()\n            while _ALPACA_CALL_TIMES and now-_ALPACA_CALL_TIMES[0] >= _ALPACA_WINDOW_SEC:\n                _ALPACA_CALL_TIMES.popleft()\n            if len(_ALPACA_CALL_TIMES) < _ALPACA_MAX_PER_MIN:\n                _ALPACA_CALL_TIMES.append(now)\n                return\n            sleep_for=_ALPACA_WINDOW_SEC-(now-_ALPACA_CALL_TIMES[0])+0.01\n        time.sleep(max(0.01,min(sleep_for,5.0)))\n\n'''
s=s.replace(marker,rate+marker,1)

headers_end='''def alpaca_headers():\n    if not ALPACA_API_KEY or not ALPACA_API_SECRET:\n        raise RuntimeError("Alpaca is not configured. Add APCA_API_KEY_ID and APCA_API_SECRET_KEY to Render.")\n    return {"APCA-API-KEY-ID":ALPACA_API_KEY,"APCA-API-SECRET-KEY":ALPACA_API_SECRET,"Accept":"application/json"}\n\n'''
assert headers_end in s
wrapper='''def alpaca_get(url, **kwargs):\n    """Single choke point for outbound Alpaca GET requests across Flask and worker threads."""\n    _alpaca_rate_gate()\n    kwargs.setdefault("headers",alpaca_headers())\n    return requests.get(url,**kwargs)\n\n'''
s=s.replace(headers_end,headers_end+wrapper,1)

# Known Alpaca call sites from v27.6 diff.
repls={
'requests.get(url,params=params,headers=alpaca_headers(),timeout=20)':'alpaca_get(url,params=params,timeout=20)',
'requests.get(url,params=params,headers=alpaca_headers(),timeout=25)':'alpaca_get(url,params=params,timeout=25)',
'requests.get(url,params=params,headers=alpaca_headers(),timeout=30)':'alpaca_get(url,params=params,timeout=30)',
'requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)':'alpaca_get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,timeout=30)',
'requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/trades",params=params,headers=alpaca_headers(),timeout=35)':'alpaca_get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/trades",params=params,timeout=35)',
'requests.get(url,params=params,headers=alpaca_headers(),timeout=18)':'alpaca_get(url,params=params,timeout=18)',
}
for old,new in repls.items():
    s=s.replace(old,new)

# v27.2 active-US-equity refresh is also an Alpaca call and must share the same budget.
old='''        resp=requests.get(\n            ALPACA_TRADING_BASE_URL+"/v2/assets",\n            params={"status":"active","asset_class":"us_equity"},\n            headers={"APCA-API-KEY-ID":ALPACA_API_KEY,"APCA-API-SECRET-KEY":ALPACA_API_SECRET},\n            timeout=8,\n        )'''
new='''        resp=alpaca_get(\n            ALPACA_TRADING_BASE_URL+"/v2/assets",\n            params={"status":"active","asset_class":"us_equity"},\n            timeout=8,\n        )'''
assert old in s
s=s.replace(old,new,1)

p.write_text(s)

r=Path('README.txt')
readme=r.read_text()
assert readme.startswith('v27.5')
entry='''v27.6 — FIX ROOT CAUSE OF FALSE "ILLIQUID" REPORTS: ALPACA RATE LIMITING\n- v27.5 improved the wording of liquidity failures, but rate-limited Alpaca responses could still make later scan candidates look falsely thin. v27.6 adds a shared sliding-window request gate so concurrent scan threads no longer burst Alpaca independently.\n- Added a process-wide limiter at 170 requests/minute and routed Alpaca GET traffic through alpaca_get(). This includes stock/option bars, option chains, trades, snapshots, institutional samples, and the active-US-equity asset refresh used by holdings sanitation.\n- The limiter is shared across Flask threads and ThreadPoolExecutors inside the current single Gunicorn worker. Large scans may take longer when the budget is saturated, but they should pace instead of turning 429s into misleading liquidity failures.\n\n'''
r.write_text(entry+readme)

# Static validation.
out=p.read_text()
assert 'APP_VERSION = "27.6"' in out
assert 'from collections import deque' in out
assert 'def _alpaca_rate_gate():' in out
assert 'def alpaca_get(url, **kwargs):' in out
assert '_ALPACA_MAX_PER_MIN = 170' in out
assert 'ALPACA_TRADING_BASE_URL+"/v2/assets"' in out
assert 'resp=alpaca_get(' in out
# All explicit Alpaca-data calls that previously carried alpaca_headers() should now go through wrapper.
assert 'requests.get(f"{ALPACA_DATA_BASE_URL}' not in out
assert 'requests.get(url,params=params,headers=alpaca_headers()' not in out
print('v27.6 patch applied')
