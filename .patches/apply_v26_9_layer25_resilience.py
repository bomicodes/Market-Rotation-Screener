from pathlib import Path

p=Path('app.py')
s=p.read_text()
rp=Path('README.txt')
r=rp.read_text()

if 'APP_VERSION = "26.8"' in s:
    s=s.replace('APP_VERSION = "26.8"','APP_VERSION = "26.9"',1)
elif 'APP_VERSION = "26.9"' not in s:
    raise SystemExit('Expected v26.8 APP_VERSION not found')

old='''   for(let n=0;n<candidates.length;n+=6){
     await Promise.all(candidates.slice(n,n+6).map(async x=>{
       const ac=new AbortController();
       const timer=setTimeout(()=>ac.abort(),8000);
       try{
         const r=await fetch(`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`,{signal:ac.signal}),j=await r.json();
         if(r.ok&&j.ok)x._earlyPriceSignal=v262DailyReversalSignal(j);
       }catch(e){ /* timeout or network error on one ticker should not block the rest of the scan */ }
       finally{clearTimeout(timer)}
     }));
   }'''

new='''   for(let n=0;n<candidates.length;n+=6){
     await Promise.allSettled(candidates.slice(n,n+6).map(async x=>{
       let ac=null,timer=null;
       try{
         const url=`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`;
         const hasAbort=typeof AbortController!=="undefined";
         if(hasAbort)ac=new AbortController();
         const request=fetch(url,hasAbort?{signal:ac.signal}:{});
         const timeout=new Promise((_,reject)=>{
           timer=setTimeout(()=>{
             try{if(ac)ac.abort()}catch(_e){}
             reject(new Error("Layer 2.5 chart-preview timeout"));
           },8000);
         });
         const resp=await Promise.race([request,timeout]);
         const j=await resp.json();
         if(resp.ok&&j.ok)x._earlyPriceSignal=v262DailyReversalSignal(j);
       }catch(e){ /* one ticker is optional enrichment; never fail the scan */ }
       finally{if(timer)clearTimeout(timer)}
     }));
   }'''

if old not in s:
    if new not in s:
        raise SystemExit('Expected v26.7 Layer 2.5 block not found')
else:
    s=s.replace(old,new,1)

entry='''v26.9 — HARDEN LAYER 2.5 SO ONE TICKER CANNOT FAIL THE SCAN
- Fixes the new Layer 2.5 failure mode introduced by v26.7: AbortController construction occurred outside the per-ticker try/catch and batches still used Promise.all, so an unexpected client-side exception could reject the entire batch and terminate Top Setups at Layer 2.5.
- Each chart-preview enrichment is now fully isolated. AbortController is feature-detected, the 8-second bound is enforced with Promise.race, and batches use Promise.allSettled so a timeout, network failure, JSON error, browser compatibility issue, or other single-ticker exception cannot reject the Layer 2.5 batch.
- If AbortController is unavailable, the scan still advances after 8 seconds; the underlying browser request may finish later, but it no longer blocks Top Setups.
- This changes only Layer 2.5 resilience. Candidate ranking, early-reversal logic, v26.5 persistence, and the v26.8 shared safeTickerFetchJson retry behavior are unchanged.

'''
if not r.startswith('v26.9 — HARDEN LAYER 2.5'):
    r=entry+r

p.write_text(s)
rp.write_text(r)

assert 'APP_VERSION = "26.9"' in s
assert 'Promise.allSettled(candidates.slice(n,n+6)' in s
assert 'typeof AbortController!=="undefined"' in s
assert 'Promise.race([request,timeout])' in s
assert 'Layer 2.5 chart-preview timeout' in s
print('v26.9 patch applied')
