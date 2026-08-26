from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()
orig=s

s=re.sub(r'APP_VERSION = "24\.9"', 'APP_VERSION = "25.0"', s, count=1)

old='''def _yf_download_retry(ticker, period, interval="1d", timeout=12, attempts=2, prepost=False):
    """Small fail-safe wrapper for Yahoo requests used by the deep-dive modules.

    Yahoo occasionally stalls or returns an empty frame even for liquid symbols.
    Keep retries bounded so one bad provider call cannot leave the chart/STRAT UI
    spinning for a minute.
    """
    last=pd.DataFrame()
    for attempt in range(max(1, attempts)):
        try:
            df=yf.download(
                ticker, period=period, interval=interval, auto_adjust=True,
                progress=False, threads=False, prepost=prepost, timeout=timeout
            )
            if df is not None and len(df):
                if isinstance(df.columns,pd.MultiIndex):
                    df.columns=[c[0] for c in df.columns]
                df.index=pd.to_datetime(df.index).tz_localize(None)
                return df.sort_index()
            last=df if df is not None else pd.DataFrame()
        except Exception:
            pass
        if attempt+1 < attempts:
            time.sleep(0.6*(attempt+1))
    return last if last is not None else pd.DataFrame()

def dl_ohlc(ticker, period="3y"):
    # Cache successful daily history briefly because chart review and setup
    # analytics frequently request the same symbol within seconds of each other.
    key=f"ohlc-v23-6:{ticker.upper()}:{period}"
    hit=CACHE.get(key)
    if hit and time.time()-hit[0] < 300:
        return hit[1].copy()
    df=_yf_download_retry(ticker,period,"1d",timeout=12,attempts=2)
    if df is not None and len(df):
        CACHE[key]=(time.time(),df.copy())
    return df if df is not None else pd.DataFrame()
'''

new='''def _yf_download_retry(ticker, period, interval="1d", timeout=12, attempts=2, prepost=False):
    """Coalesced Yahoo fetch with stale-on-error protection.

    Chart, STRAT and institutional modules often ask for the same symbol within
    milliseconds. One upstream request now owns each ticker/period/interval key;
    concurrent callers wait for it and reuse the result. Successful frames remain
    fresh for five minutes and stale frames remain eligible for 24 hours if Yahoo
    is rate-limited or temporarily unavailable.
    """
    ticker=str(ticker or "").upper().strip()
    key=f"yf-frame-v25:{ticker}:{period}:{interval}:{1 if prepost else 0}"
    now=time.time(); hit=CACHE.get(key)
    fresh_ttl=300
    stale_ttl=86400
    if hit and now-hit[0] < fresh_ttl:
        return hit[1].copy()

    with _cache_lock(key):
        now=time.time(); hit=CACHE.get(key)
        if hit and now-hit[0] < fresh_ttl:
            return hit[1].copy()
        last=pd.DataFrame(); last_err=None
        for attempt in range(max(1, attempts)):
            try:
                df=yf.download(
                    ticker, period=period, interval=interval, auto_adjust=True,
                    progress=False, threads=False, prepost=prepost, timeout=timeout
                )
                if df is not None and len(df):
                    if isinstance(df.columns,pd.MultiIndex):
                        df.columns=[c[0] for c in df.columns]
                    df.index=pd.to_datetime(df.index).tz_localize(None)
                    df=df.sort_index()
                    CACHE[key]=(time.time(),df.copy())
                    _mark_source("yfinance",True)
                    return df
                last=df if df is not None else pd.DataFrame()
            except Exception as e:
                last_err=e
                # A retry immediately after a provider 429 usually worsens the
                # rate-limit window. Prefer stale data when we have it.
                msg=str(e).lower()
                if "429" in msg or "rate" in msg or "too many requests" in msg:
                    break
            if attempt+1 < attempts:
                time.sleep(0.8*(attempt+1))

        _mark_source("yfinance",False,last_err or "empty response")
        hit=CACHE.get(key)
        if hit and now-hit[0] < stale_ttl:
            return hit[1].copy()
        return last if last is not None else pd.DataFrame()

def dl_ohlc(ticker, period="3y"):
    # Daily history uses the same canonical/coalesced provider cache as every
    # other Yahoo-backed module. This prevents Chart + Context + expectancy from
    # each firing their own download for the same ticker.
    return _yf_download_retry(ticker,period,"1d",timeout=12,attempts=2)
'''

if old not in s:
    raise SystemExit('Yahoo helper block not found')
s=s.replace(old,new,1)

old_strat='''@app.get("/api/strat/<ticker>")
def api_strat(ticker):
    ticker=ticker.upper().strip()
    try:
        intraday=_yf_download_retry(ticker,"60d","60m",timeout=12,attempts=2,prepost=False)
        daily=dl_ohlc(ticker,"2y")
'''
new_strat='''@app.get("/api/strat/<ticker>")
def api_strat(ticker):
    ticker=ticker.upper().strip()
    try:
        # Prefer the paid consolidated Alpaca SIP feed for hourly STRAT bars.
        # This avoids a second Yahoo request immediately after Chart Review and
        # keeps intraday price-action analysis on the same canonical source.
        intraday=pd.DataFrame()
        try:
            abars=alpaca_chart_bars(ticker,"1h","3m")
            if abars:
                intraday=pd.DataFrame([{\n                    "Open":b.get("open"),"High":b.get("high"),"Low":b.get("low"),\n                    "Close":b.get("close"),"Volume":b.get("volume"),"dt":b.get("dt")\n                } for b in abars])
                if len(intraday):
                    intraday.index=pd.to_datetime(intraday.pop("dt")).tz_localize(None)
                    intraday=intraday.sort_index()
        except Exception:
            intraday=pd.DataFrame()
        if intraday is None or len(intraday)==0:
            intraday=_yf_download_retry(ticker,"60d","60m",timeout=12,attempts=1,prepost=False)
        daily=dl_ohlc(ticker,"2y")
'''
if old_strat not in s:
    raise SystemExit('STRAT endpoint prefix not found')
s=s.replace(old_strat,new_strat,1)

# Add a small response hint so clients can distinguish a provider-degraded result
# from a hard application failure when stale data was reused.
old_health='''return jsonify({"ok":True,"version":APP_VERSION'''
# not required; leave diagnostics shape stable if exact marker has changed.

if s==orig:
    raise SystemExit('No changes made')
p.write_text(s)
print('patched app.py to v25.0 request coalescing')
