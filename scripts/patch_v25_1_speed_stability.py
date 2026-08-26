from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()
orig=s

s=s.replace('APP_VERSION = "25.0"','APP_VERSION = "25.1"',1)

# 1) Daily OHLC: paid Alpaca SIP first, Yahoo only as fallback. This removes Yahoo
# from the critical path for Chart/STRAT/Institutional/setup-history.
old='''def dl_ohlc(ticker, period="3y"):
    # Daily history uses the same canonical/coalesced provider cache as every
    # other Yahoo-backed module. This prevents Chart + Context + expectancy from
    # each firing their own download for the same ticker.
    return _yf_download_retry(ticker,period,"1d",timeout=12,attempts=2)
'''
new='''def _period_days(period):
    p=str(period or "").lower()
    table={"5d":8,"1mo":35,"1m":35,"3mo":105,"3m":105,"6mo":205,"6m":205,
           "1y":370,"18mo":560,"2y":740,"3y":1110,"4y":1480,"5y":1850}
    return table.get(p,1110)

def _alpaca_daily_ohlc(ticker, period="3y"):
    ticker=str(ticker or "").upper().strip()
    key=f"alpaca-day-v25-1:{ALPACA_STOCK_FEED}:{ticker}:{period}"
    now=time.time(); hit=CACHE.get(key); fresh_ttl=300; stale_ttl=86400
    if hit and now-hit[0] < fresh_ttl:return hit[1].copy()
    if not (ALPACA_API_KEY and ALPACA_API_SECRET):return pd.DataFrame()
    with _cache_lock(key):
        now=time.time(); hit=CACHE.get(key)
        if hit and now-hit[0] < fresh_ttl:return hit[1].copy()
        try:
            from zoneinfo import ZoneInfo
            end=datetime.now(ZoneInfo("America/New_York")); start=end-timedelta(days=_period_days(period))
            url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
            params={"timeframe":"1Day","start":start.isoformat(),"end":end.isoformat(),
                    "adjustment":"raw","feed":ALPACA_STOCK_FEED,"sort":"asc","limit":10000}
            raw=[]; token=None
            for _ in range(4):
                if token:params["page_token"]=token
                r=requests.get(url,params=params,headers=alpaca_headers(),timeout=20)
                r.raise_for_status(); j=r.json() or {}; raw.extend(j.get("bars") or [])
                token=j.get("next_page_token") or j.get("page_token")
                if not token:break
            if raw:
                rows=[]; idx=[]
                for b in raw:
                    try:
                        idx.append(pd.Timestamp(b.get("t")).tz_convert("America/New_York").tz_localize(None))
                        rows.append({"Open":float(b.get("o")),"High":float(b.get("h")),"Low":float(b.get("l")),
                                     "Close":float(b.get("c")),"Volume":float(b.get("v") or 0)})
                    except Exception:pass
                if rows:
                    df=pd.DataFrame(rows,index=pd.DatetimeIndex(idx)).sort_index()
                    CACHE[key]=(time.time(),df.copy()); _mark_source("alpaca_stocks",True); return df
        except Exception as e:
            _mark_source("alpaca_stocks",False,e)
        hit=CACHE.get(key)
        if hit and now-hit[0] < stale_ttl:return hit[1].copy()
        return pd.DataFrame()

def dl_ohlc(ticker, period="3y"):
    # Paid consolidated SIP is canonical for deep-dive OHLC. Yahoo is a fallback,
    # not a parallel dependency, which removes most rate-limit failures.
    df=_alpaca_daily_ohlc(ticker,period)
    if df is not None and len(df):return df
    return _yf_download_retry(ticker,period,"1d",timeout=10,attempts=1)
'''
if old not in s: raise SystemExit('dl_ohlc marker not found')
s=s.replace(old,new,1)

# 2) Cache canonical hourly bars so Chart 1H/4H and STRAT reuse one SIP request.
start=s.index('def alpaca_chart_bars(ticker,timeframe,period):')
end=s.index('\ndef sma(arr, n):',start)
old=s[start:end]
new='''def _canonical_hourly_bars(ticker,period):
    ticker=str(ticker or "").upper().strip(); key=f"alpaca-hour-v25-1:{ALPACA_STOCK_FEED}:{ticker}:{period}"
    now=time.time(); hit=CACHE.get(key); fresh_ttl=120; stale_ttl=21600
    if hit and now-hit[0] < fresh_ttl:return [dict(x) for x in hit[1]]
    with _cache_lock(key):
        now=time.time(); hit=CACHE.get(key)
        if hit and now-hit[0] < fresh_ttl:return [dict(x) for x in hit[1]]
        parsed=[]
        if ALPACA_API_KEY and ALPACA_API_SECRET:
            try:
                start_dt,end_dt=_period_start_et(period); url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
                params={"timeframe":"1Hour","start":start_dt.isoformat(),"end":end_dt.isoformat(),
                        "adjustment":"raw","feed":ALPACA_STOCK_FEED,"sort":"asc","limit":10000}
                raw=[]; token=None
                for _ in range(3):
                    if token:params["page_token"]=token
                    r=requests.get(url,params=params,headers=alpaca_headers(),timeout=20); r.raise_for_status()
                    j=r.json() or {}; raw.extend(j.get("bars") or []); token=j.get("next_page_token") or j.get("page_token")
                    if not token:break
                for b in raw:
                    try:
                        dt=pd.Timestamp(b.get("t"));
                        if dt.tzinfo is None:dt=dt.tz_localize("UTC")
                        dt=dt.tz_convert("America/New_York"); mins=dt.hour*60+dt.minute
                        if mins<570 or mins>=960:continue
                        parsed.append({"dt":dt,"open":float(b.get("o")),"high":float(b.get("h")),
                                       "low":float(b.get("l")),"close":float(b.get("c")),"volume":int(b.get("v") or 0)})
                    except Exception:pass
                if parsed:_mark_source("alpaca_stocks",True)
            except Exception as e:_mark_source("alpaca_stocks",False,e); parsed=[]
        if not parsed:
            hit=CACHE.get(key)
            if hit and now-hit[0] < stale_ttl:return [dict(x) for x in hit[1]]
            try:
                days=_period_days(period); yperiod="1mo" if days<=35 else "3mo" if days<=105 else "6mo"
                df=_yf_download_retry(ticker,yperiod,"60m",timeout=10,attempts=1,prepost=False)
                if df is not None and len(df):
                    for idx,row in df.dropna(subset=["Close"]).iterrows():
                        dt=pd.Timestamp(idx); parsed.append({"dt":dt,"open":float(row.get("Open",row["Close"])),
                           "high":float(row.get("High",row["Close"])),"low":float(row.get("Low",row["Close"])),
                           "close":float(row["Close"]),"volume":int(row.get("Volume") or 0)})
            except Exception:parsed=[]
        if parsed:CACHE[key]=(time.time(),[dict(x) for x in parsed])
        return parsed

def alpaca_chart_bars(ticker,timeframe,period):
    if timeframe not in ("1h","4h"):return []
    parsed=_canonical_hourly_bars(ticker,period)
    if timeframe=="1h":return parsed
    groups={}
    for b in parsed:
        mins=b["dt"].hour*60+b["dt"].minute; slot=0 if mins<810 else 1
        groups.setdefault((b["dt"].date(),slot),[]).append(b)
    out=[]
    for key in sorted(groups):
        g=groups[key]; out.append({"dt":g[0]["dt"],"open":g[0]["open"],"high":max(x["high"] for x in g),
             "low":min(x["low"] for x in g),"close":g[-1]["close"],"volume":sum(x["volume"] for x in g)})
    return out
'''
s=s[:start]+new+s[end:]

# 3) Chart profile work: one lower-timeframe request. Derive summary profiles from visible profiles.
old='''        try:
            profiles=alpaca_session_volume_profiles(ticker)
        except Exception as profile_err:
            profiles={"session":None,"previous":None,"error":str(profile_err)}
        try:
            visible_profiles=alpaca_visible_profiles(ticker,period,timeframe)
        except Exception as visible_err:
            visible_profiles={"sessions":[],"weeks":[],"source":None,"error":str(visible_err)}
'''
new='''        try:
            visible_profiles, vp_stale, vp_err=cached_refresh_safe(
                f"visible-v25-1:{ALPACA_STOCK_FEED}:{ticker}:{period}:{timeframe}",
                lambda:alpaca_visible_profiles(ticker,period,timeframe),ttl=120)
        except Exception as visible_err:
            visible_profiles={"sessions":[],"weeks":[],"source":None,"error":str(visible_err)}; vp_stale=False; vp_err=str(visible_err)
        sess=(visible_profiles or {}).get("sessions") or []; weeks=(visible_profiles or {}).get("weeks") or []
        profiles={
            "session":(sess[-1].get("profile") if sess else None),
            "previous":(sess[-2].get("profile") if len(sess)>=2 else None),
            "current_week":(weeks[-1].get("profile") if weeks else None),
            "previous_week":(weeks[-2].get("profile") if len(weeks)>=2 else None),
            "error":(visible_profiles or {}).get("error") or vp_err,
            "stale":vp_stale,
        }
'''
if old not in s: raise SystemExit('profile block marker not found')
s=s.replace(old,new,1)
# avoid duplicate sess assignment immediately after
s=s.replace('''        migration=None
        sess=(visible_profiles or {}).get("sessions") or []
''','''        migration=None
''',1)

# 4) Cache STRAT endpoint as a whole, serving last-good payload on refresh error.
old_start='''@app.get("/api/strat/<ticker>")
def api_strat(ticker):
    ticker=ticker.upper().strip()
    try:
'''
if old_start not in s: raise SystemExit('strat start not found')
# Replace whole endpoint through next @app.get("/api/market")
ss=s.index(old_start); ee=s.index('\n\n@app.get("/api/market")',ss)
body=s[ss:ee]
inner=body[len(old_start):]
# strip terminal except block
needle='''    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500
'''
if not inner.endswith(needle): raise SystemExit('strat end not recognized')
inner=inner[:-len(needle)]
# de-indent one level and replace return jsonify with return dict
lines=[]
for line in inner.splitlines():
    lines.append(line[4:] if line.startswith('    ') else line)
core='\n'.join(lines).replace('return jsonify({"ok":True,"ticker":ticker,"frames":frames,"bullish_count":bulls,"bearish_count":bears,"continuity":continuity})','return {"ticker":ticker,"frames":frames,"bullish_count":bulls,"bearish_count":bears,"continuity":continuity}')
replacement='''def strat_payload(ticker):
    ticker=ticker.upper().strip()
'''+ '\n'.join('    '+x if x else '' for x in core.splitlines()) + '''

@app.get("/api/strat/<ticker>")
def api_strat(ticker):
    ticker=ticker.upper().strip()
    try:
        payload,stale,err=cached_refresh_safe(f"strat-v25-1:{ticker}",lambda:strat_payload(ticker),ttl=120)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500
'''
s=s[:ss]+replacement+s[ee:]

# 5) Institutional context stale-safe instead of hard failing.
old='''@app.get("/api/institutional-context/<ticker>")
def api_institutional_context(ticker):
    try:
        parent=(request.args.get("parent") or "").upper().strip() or None;key=f"institutional-v24:{ticker.upper()}:{parent or 'NONE'}";payload=cached(key,lambda:institutional_context_payload(ticker,parent),ttl=900);return jsonify({"ok":True,**payload})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500
'''
new='''@app.get("/api/institutional-context/<ticker>")
def api_institutional_context(ticker):
    try:
        parent=(request.args.get("parent") or "").upper().strip() or None
        key=f"institutional-v25-1:{ticker.upper()}:{parent or 'NONE'}"
        payload,stale,err=cached_refresh_safe(key,lambda:institutional_context_payload(ticker,parent),ttl=900)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500
'''
if old not in s: raise SystemExit('institutional endpoint marker not found')
s=s.replace(old,new,1)

# 6) Flow must reuse the exact current options cache; old key caused a second chain scan.
old='''        base,_,_=cached_refresh_safe(f"options-v23:{ticker.upper()}:0-30",lambda:options_quality_payload(ticker,"0-30"),ttl=600)
'''
new='''        base,_,_=cached_refresh_safe(f"options-v24-1:{ticker.upper()}:0-30:7:35",lambda:options_quality_payload(ticker,"0-30",35,7),ttl=600)
'''
if old not in s: raise SystemExit('flow options key marker not found')
s=s.replace(old,new,1)

# 7) Browser request dedupe + tiny cache. Replace safeTickerFetchJson function only.
pat=r'async function safeTickerFetchJson\(path,ticker,params=\{\}\)\{.*?\n\}\nasync function openSectorStockTicker'
m=re.search(pat,s,re.S)
if not m: raise SystemExit('safeTickerFetchJson marker not found')
newjs='''const tickerRequestInflight=new Map(),tickerResponseCache=new Map();
async function safeTickerFetchJson(path,ticker,params={},opts={}){
 const url=safeTickerUrl(path,ticker,params),ttl=Number(opts.ttl||0),now=Date.now();
 const cached=tickerResponseCache.get(url);
 if(ttl>0&&cached&&now-cached.at<ttl)return cached.value;
 if(tickerRequestInflight.has(url))return tickerRequestInflight.get(url);
 const promise=(async()=>{
   let r;
   try{r=await window.fetch(url,{method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}})}
   catch(e){throw new Error(`Request could not be dispatched: ${e?.message||e}`)}
   let raw="",j={};
   try{raw=await r.text();j=raw?JSON.parse(raw):{};}catch(e){throw new Error(`Service returned an unreadable response (${r.status})`)}
   if(!r.ok||!j?.ok)throw new Error(j?.error||`Request failed (${r.status})`);
   if(ttl>0)tickerResponseCache.set(url,{at:Date.now(),value:j});
   return j;
 })();
 tickerRequestInflight.set(url,promise);
 try{return await promise}finally{tickerRequestInflight.delete(url)}
}
async function openSectorStockTicker'''
s=s[:m.start()]+newjs+s[m.end():]

# 8) Chart uses the same helper rather than bespoke Safari parsing.
old='''   const chartUrl=safeTickerUrl("/api/chart-preview",ticker,{period,timeframe:previewTimeframe});
   let r;
   try{
     r=await fetch(chartUrl,{method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}});
   }catch(fetchErr){
     console.error("Chart fetch dispatch failed",{ticker,chartUrl,fetchErr});
     throw new Error(`Chart request could not be dispatched: ${fetchErr?.message||fetchErr}`);
   }
   let j;
   try{j=await r.json();}catch(parseErr){throw new Error(`Chart returned an unreadable response (${r.status})`);}
   if(seq!==previewRequestSeq)return;
   if(!r.ok||!j.ok)throw Error(j.error||`Chart preview failed (${r.status})`);
'''
new='''   const j=await safeTickerFetchJson("/api/chart-preview",ticker,{period,timeframe:previewTimeframe},{ttl:30000});
   if(seq!==previewRequestSeq)return;
'''
if old not in s: raise SystemExit('chart fetch marker not found')
s=s.replace(old,new,1)

# 9) Institutional JS Safari-safe + dedupe.
pat=r'async function loadInstitutionalContext\(ticker,parent=null,quiet=false\)\{.*?\n\}\nfunction renderInstitutionalContext'
m=re.search(pat,s,re.S)
if not m: raise SystemExit('loadInstitutionalContext marker not found')
oldfun=m.group(0)
# preserve render boundary, replace function
newfun='''async function loadInstitutionalContext(ticker,parent=null,quiet=false){
 ticker=normalizeStockTicker(ticker);if(!ticker)return null;activeInstitutionalTicker=ticker;
 const el=ensureInstitutionalPanel();if(el&&!quiet)el.innerHTML=`<div class="note">Loading ${ticker} institutional decision layer…</div>`;
 try{
   const j=await safeTickerFetchJson("/api/institutional-context",ticker,parent?{parent}: {},{ttl:60000});
   institutionalContextMap[ticker]=j;if(activeInstitutionalTicker===ticker)renderInstitutionalContext(ticker);renderTopSetups();return j
 }catch(e){if(el&&!quiet)el.innerHTML=`<span class="warn">Institutional layer: ${e.message}</span>`;return null}
}
function renderInstitutionalContext'''
s=s[:m.start()]+newfun+s[m.end():]

# 10) Do not auto-run expensive flow after options. Surface on-demand state instead.
old='''   renderOptionsPanel();renderLiveStocks();loadFlowTicker(ticker,false);
'''
new='''   renderOptionsPanel();renderLiveStocks();
   const fs=document.getElementById("flowSection"),fst=document.getElementById("flowStatus"),fb=document.getElementById("refreshFlow");
   if(fs)fs.style.display="block";if(fst)fst.textContent=`${ticker} · flow deferred for faster loading`;if(fb)fb.textContent="Load flow";
'''
if old not in s: raise SystemExit('auto flow marker not found')
s=s.replace(old,new,1)
old='''document.getElementById("refreshFlow")?.addEventListener("click",()=>{if(activeOptionsData?.ticker)loadFlowTicker(activeOptionsData.ticker,true)});'''
new='''document.getElementById("refreshFlow")?.addEventListener("click",()=>{if(activeOptionsData?.ticker){const force=!!(activeFlowData&&activeFlowData.ticker===activeOptionsData.ticker);loadFlowTicker(activeOptionsData.ticker,force);}});'''
if old not in s: raise SystemExit('flow listener marker not found')
s=s.replace(old,new,1)
# button text returns to Refresh after successful flow
s=s.replace('''activeFlowData=j;renderFlow(j);renderHeatMap()''','''activeFlowData=j;renderFlow(j);renderHeatMap();const fb=document.getElementById("refreshFlow");if(fb)fb.textContent="Refresh flow"''',1)

# 11) Defer market-wide Top Setups until browser idle rather than 80ms after dashboard data.
old=''' setTimeout(()=>runAutomaticTopSetups(false),80);'''
new=''' if("requestIdleCallback" in window)requestIdleCallback(()=>runAutomaticTopSetups(false),{timeout:3000});
 else setTimeout(()=>runAutomaticTopSetups(false),1800);'''
if old not in s: raise SystemExit('top setup schedule marker not found')
s=s.replace(old,new,1)

# 12) Defer institutional context until core chart/STRAT/options have had a head start.
old='''const _openSectorStockTickerV23=openSectorStockTicker;openSectorStockTicker=async function(rawTicker,opts={}){const ticker=normalizeStockTicker(rawTicker),parent=currentSector,out=await _openSectorStockTickerV23(rawTicker,opts);loadInstitutionalContext(ticker,parent,false);return out};'''
new='''const _openSectorStockTickerV23=openSectorStockTicker;openSectorStockTicker=async function(rawTicker,opts={}){const ticker=normalizeStockTicker(rawTicker),parent=currentSector,out=await _openSectorStockTickerV23(rawTicker,opts);setTimeout(()=>loadInstitutionalContext(ticker,parent,false),350);return out};'''
if old not in s: raise SystemExit('institutional defer marker not found')
s=s.replace(old,new,1)

# status wording
s=s.replace('Dealer positioning + sampled flow will load with each analyzed ticker.','Dealer positioning loads with each ticker; institutional flow loads on demand for faster analysis.',1)

if s==orig:raise SystemExit('no changes')
p.write_text(s)
print('patched v25.1 speed/stability')
