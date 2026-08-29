from pathlib import Path

p=Path('app.py')
rp=Path('README.txt')
s=p.read_text(); r=rp.read_text()

if 'APP_VERSION = "27.1"' in s:
    s=s.replace('APP_VERSION = "27.1"','APP_VERSION = "27.2"',1)
elif 'APP_VERSION = "27.2"' not in s:
    raise SystemExit('Expected v27.1 APP_VERSION not found')

old='''SECTOR_HOLDING_SUPPLEMENTS = {
    "XLB": [{"ticker": "B", "name": "Barrick Mining Corporation", "weight": None}],
}

def apply_sector_supplements(etf, holdings):
    out=[dict(h) for h in holdings]
    seen={str(h.get("ticker") or h.get("symbol") or "").upper() for h in out}
    for s in SECTOR_HOLDING_SUPPLEMENTS.get(etf, []):
        sym=str(s.get("ticker") or "").upper()
        if sym and sym not in seen:
            out.append(dict(s))
            seen.add(sym)
    return out
'''
new='''SECTOR_HOLDING_SUPPLEMENTS = {
    "XLB": [{"ticker": "B", "name": "Barrick Mining Corporation", "weight": None}],
}

_US_EQUITY_SYMBOLS={"loaded_at":0.0,"symbols":None}
def _basic_us_equity_symbol(value):
    t=str(value or "").strip().upper().replace(".","-")
    if not t or len(t)>8 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ-" for ch in t):
        return None
    parts=t.split("-")
    if not parts or not (1<=len(parts[0])<=5) or not parts[0].isalpha():
        return None
    if any((not p.isalpha()) or len(p)>2 for p in parts[1:]):
        return None
    return t

def _active_alpaca_us_equity_symbols():
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        return None
    now=time.time()
    cached_symbols=_US_EQUITY_SYMBOLS.get("symbols")
    if cached_symbols is not None and now-float(_US_EQUITY_SYMBOLS.get("loaded_at") or 0)<3600:
        return cached_symbols
    try:
        resp=requests.get(
            ALPACA_TRADING_BASE_URL+"/v2/assets",
            params={"status":"active","asset_class":"us_equity"},
            headers={"APCA-API-KEY-ID":ALPACA_API_KEY,"APCA-API-SECRET-KEY":ALPACA_API_SECRET},
            timeout=8,
        )
        resp.raise_for_status()
        rows=resp.json() or []
        symbols={str(x.get("symbol") or "").upper().replace(".","-") for x in rows if x.get("symbol")}
        if symbols:
            _US_EQUITY_SYMBOLS["loaded_at"]=now
            _US_EQUITY_SYMBOLS["symbols"]=symbols
            return symbols
    except Exception:
        pass
    return cached_symbols

def filter_us_equity_holdings(holdings):
    active=_active_alpaca_us_equity_symbols()
    out=[]; seen=set()
    for h in holdings or []:
        row=dict(h)
        t=_basic_us_equity_symbol(row.get("ticker") or row.get("symbol"))
        if not t or t in seen:
            continue
        if active is not None and t not in active:
            continue
        row["ticker"]=t
        seen.add(t)
        out.append(row)
    return out

def apply_sector_supplements(etf, holdings):
    out=[dict(h) for h in holdings]
    seen={str(h.get("ticker") or h.get("symbol") or "").upper().replace(".","-") for h in out}
    for extra in SECTOR_HOLDING_SUPPLEMENTS.get(etf, []):
        sym=str(extra.get("ticker") or "").upper().replace(".","-")
        if sym and sym not in seen:
            out.append(dict(extra))
            seen.add(sym)
    return filter_us_equity_holdings(out)
'''
if old not in s:
    if new not in s:
        raise SystemExit('Expected sector supplement block not found')
else:
    s=s.replace(old,new,1)

entry='''v27.2 — FILTER NON-US / MALFORMED HOLDING SYMBOLS BEFORE PRICE SCANS
- Render logs showed sector scans sending local/foreign listing codes such as 968, 3800, S92, NOFR, SCATC, DORL, GRE and SLR into Yahoo as if they were US tickers, creating repeated failed downloads and unnecessary work during Top Setups.
- All holdings now pass through a US-equity namespace filter after issuer/provider retrieval and sector supplements. Obvious malformed/numeric/local-exchange codes are rejected immediately.
- When Alpaca credentials are available, the filter refreshes Alpaca's active US-equity asset list at most once per hour and only keeps holdings that resolve to an active US equity. If that lookup is temporarily unavailable, the app falls back to a conservative US ticker-shape filter rather than failing the sector scan.
- This does not change RRG scoring, Top Setups ranking, Layer 2.5 reversal logic, or card presentation. It only prevents invalid/non-US listing identifiers from reaching the expensive price/scan pipeline.

'''
if not r.startswith('v27.2 — FILTER NON-US / MALFORMED HOLDING SYMBOLS'):
    r=entry+r

p.write_text(s); rp.write_text(r)
assert 'APP_VERSION = "27.2"' in s
assert 'def filter_us_equity_holdings(holdings):' in s
assert 'ALPACA_TRADING_BASE_URL+"/v2/assets"' in s
assert 'return filter_us_equity_holdings(out)' in s
print('v27.2 patch applied')
