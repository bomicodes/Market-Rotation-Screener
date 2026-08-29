from pathlib import Path
import re

app=Path('app.py')
readme=Path('README.txt')
s=app.read_text()
r=readme.read_text()

if 'APP_VERSION = "26.9"' in s:
    s=s.replace('APP_VERSION = "26.9"','APP_VERSION = "27.0"',1)
elif 'APP_VERSION = "27.0"' not in s:
    raise SystemExit('Expected v26.9 APP_VERSION not found')

marker='@app.get("/api/chart-preview/<ticker>")\ndef api_chart_preview(ticker):'
server='''def _early_reversal_signal_from_ohlc(df):
    if df is None or not len(df): return None
    need=["Open","High","Low","Close"]
    if any(c not in df.columns for c in need): return None
    d=df.copy()
    for c in need+["Volume"]:
        if c in d.columns:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=need)
    if len(d)<6:return None
    a=d.iloc[-3]; d1=d.iloc[-2]; d2=d.iloc[-1]
    a_low=float(a["Low"]); d1_low=float(d1["Low"]); d1_close=float(d1["Close"]); d1_open=float(d1["Open"])
    d2_close=float(d2["Close"]); d2_open=float(d2["Open"]); d2_high=float(d2["High"])
    failed2d=d1_low<a_low and d1_close>a_low
    green1=d1_close>d1_open; green2=d2_close>d2_open
    follow_through=d2_close>d1_close or d2_high>float(d1["High"])
    if failed2d and green1 and green2 and follow_through:
        return {"kind":"FAILED 2D + 2 GREEN","phase":"EARLY","score":6,"detail":"Failed daily 2-down reclaimed the prior low, followed by two green daily bars"}
    if failed2d and green1:
        return {"kind":"FAILED 2D REVERSAL","phase":"EARLY","score":4,"detail":"Daily 2-down failed and reclaimed the prior low"}
    prior=d.iloc[-7:-2]
    if len(prior)==0:return None
    prior_high=float(prior["High"].max()); prior_close_high=float(prior["Close"].max())
    range2=max(.01,float(d2["High"])-float(d2["Low"])); close_near_high=(float(d2["High"])-d2_close)/range2<=.30
    rising=green1 and green2 and d2_close>d1_close
    breakout=rising and d2_close>prior_close_high and d2_high>=prior_high
    vol_expansion=False
    if "Volume" in d.columns:
        vols=[float(v) for v in d.tail(12)["Volume"].dropna().tolist()]
        if len(vols)>=2:
            cur_vol=vols[-1]; avg_vol=sum(vols[:-1])/len(vols[:-1]); vol_expansion=cur_vol>=avg_vol*1.15
    if breakout and close_near_high:
        return {"kind":"VOLUME CONTINUATION" if vol_expansion else "CONTINUATION BREAKOUT","phase":"CONTINUATION","score":6 if vol_expansion else 5,"detail":"Two green daily bars with a fresh breakout"+(" on expanding volume" if vol_expansion else "")}
    if rising and close_near_high:
        return {"kind":"TREND CONTINUATION","phase":"CONTINUATION","score":3,"detail":"Two green daily bars with higher close and strong close location"}
    return None

@app.post("/api/early-reversal-scan")
def api_early_reversal_scan():
    body=request.get_json(silent=True) or {}
    raw=body.get("tickers") or []
    tickers=[]; seen=set()
    for value in raw:
        t=str(value or "").upper().strip()
        if not t or len(t)>16 or t in seen:continue
        if not all(ch.isalnum() or ch in ".-^" for ch in t):continue
        seen.add(t); tickers.append(t)
        if len(tickers)>=90:break
    if not tickers:return jsonify({"ok":True,"signals":{},"failed":0,"scanned":0})
    signals={}; failed=[]
    def one(t):
        try:
            px=dl_ohlc(t,"1m")
            return t,_early_reversal_signal_from_ohlc(px),None
        except Exception as e:
            return t,None,str(e)
    workers=min(3,len(tickers))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures=[ex.submit(one,t) for t in tickers]
        for f in as_completed(futures):
            t,sig,err=f.result()
            if sig is not None:signals[t]=sig
            if err:failed.append(t)
    return jsonify({"ok":True,"signals":signals,"failed":len(failed),"scanned":len(tickers)})

'''
if server not in s:
    if marker not in s: raise SystemExit('chart-preview route marker not found')
    s=s.replace(marker,server+marker,1)

pattern=re.compile(r'''   if\(st\)st\.textContent=`Layer 2\.5 · checking early daily reversals on \$\{candidates\.length\} candidates`;\n.*?   candidates=candidates\.sort\(\(a,b\)=>v262EarlyMoveScore\(b\)-v262EarlyMoveScore\(a\)\)\.slice\(0,60\);''',re.S)
replacement='''   if(st)st.textContent=`Layer 2.5 · checking early daily reversals on ${candidates.length} candidates`;
   try{
     const ac=(typeof AbortController!=="undefined")?new AbortController():null;
     const timer=ac?setTimeout(()=>ac.abort(),60000):null;
     const resp=await fetch("/api/early-reversal-scan",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json",Accept:"application/json"},body:JSON.stringify({tickers:candidates.map(x=>x.ticker)}),...(ac?{signal:ac.signal}:{})});
     if(timer)clearTimeout(timer);
     const j=await resp.json();
     if(!resp.ok||!j?.ok)throw new Error(j?.error||`Layer 2.5 bulk scan failed (${resp.status})`);
     const signals=j.signals||{};
     candidates.forEach(x=>{const sig=signals[String(x.ticker||"").toUpperCase()];if(sig)x._earlyPriceSignal=sig;});
   }catch(e){
     console.warn("Layer 2.5 bulk enrichment unavailable; continuing without early-price signals",e);
   }
   candidates=candidates.sort((a,b)=>v262EarlyMoveScore(b)-v262EarlyMoveScore(a)).slice(0,60);'''
if not pattern.search(s):
    if '/api/early-reversal-scan' not in s: raise SystemExit('Layer 2.5 block not found')
else:
    s=pattern.sub(replacement,s,count=1)

entry='''v27.0 — REBUILD LAYER 2.5 AS ONE BULK SERVER SCAN
- Replaces up to 90 browser-side /api/chart-preview requests with one /api/early-reversal-scan request. Layer 2.5 no longer downloads full chart-preview payloads just to inspect the last few daily bars.
- The server computes the existing failed-2D / continuation logic directly from cached daily OHLC using dl_ohlc, with controlled concurrency of 3 workers and per-ticker failure isolation.
- This removes the 6-request browser burst against a 1-worker/4-thread Render service, avoids queue time consuming client-side per-ticker timeouts, and dramatically reduces JSON/network overhead.
- If the bulk enrichment itself is unavailable, Top Setups continues without early-price bonuses rather than failing the scan.

'''
if not r.startswith('v27.0 — REBUILD LAYER 2.5'):
    r=entry+r

app.write_text(s); readme.write_text(r)
assert 'APP_VERSION = "27.0"' in s
assert '@app.post("/api/early-reversal-scan")' in s
assert 'ThreadPoolExecutor(max_workers=workers)' in s
assert 'fetch("/api/early-reversal-scan"' in s
assert '/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d' not in s[s.find('Layer 2.5 · checking early daily reversals'):s.find('Layer 2.5 · checking early daily reversals')+2500]
print('v27.0 bulk Layer 2.5 patch applied')
