from pathlib import Path

p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "27.2"' in s
s=s.replace('APP_VERSION = "27.2"','APP_VERSION = "27.4"',1)

old='''async function auditHoldings(){
 const p=document.getElementById("auditPanel");p.style.display="block";p.textContent="Checking issuer holdings feeds…";
 try{
   let r=await fetch("/api/holdings-audit"),j=await r.json();if(!j.ok)throw Error(j.error||"Audit failed");
   p.innerHTML=`<div class="scroll"><table><thead><tr><th>ETF</th><th>Holdings loaded</th><th>Source</th><th>Status</th></tr></thead><tbody>${j.results.map(x=>`<tr><td><b>${x.etf}</b><div class="tiny">${x.name}</div></td><td>${x.count}</td><td>${x.source}</td><td>${!x.ok?"⚠️ "+(x.error||"failed"):(x.partial?"⚠️ PARTIAL":"✓ FULL")}</td></tr>`).join("")}</tbody></table></div>`;
 }catch(e){p.innerHTML=`<span class="error">${e.message}</span>`}
}

'''
assert old in s
s=s.replace(old,'',1)

old='''async function fetchPremiumSupportReliable(ticker,direction,attempts=3){
  const delays=[0,1200,3500];let lastErr=null;
  for(let i=0;i<attempts;i++){
    if(delays[i])await sleepMs(delays[i]);
    try{
      const url=`/api/premium-support/${encodeURIComponent(ticker)}?direction=${encodeURIComponent(direction)}`;
      const r=await fetch(url,{headers:{"Accept":"application/json"},cache:"no-store"});
      const raw=await r.text();let j={};
      try{j=raw?JSON.parse(raw):{}}catch(_e){throw new Error(`Unreadable premium response (${r.status})`)}
      if(r.ok&&j.ok)return j;
      const msg=j?.error||`HTTP ${r.status}`;
      if(![429,502,503,504].includes(r.status))throw new Error(msg);
      lastErr=new Error(msg);
    }catch(e){lastErr=e}
  }
  throw lastErr||new Error("Premium support request failed");
}'''
new='''async function fetchPremiumSupportReliable(ticker,direction,attempts=3){
  const delays=[0,1200,3500];let lastErr=null;
  for(let i=0;i<attempts;i++){
    if(delays[i])await sleepMs(delays[i]);
    const ac=new AbortController();
    const timer=setTimeout(()=>ac.abort(),15000);
    try{
      const url=safeTickerUrl("/api/premium-support",ticker,{direction});
      const r=await fetch(url,{headers:{"Accept":"application/json"},cache:"no-store",signal:ac.signal});
      const raw=await r.text();let j={};
      try{j=raw?JSON.parse(raw):{}}catch(_e){throw new Error(`Unreadable premium response (${r.status})`)}
      if(r.ok&&j.ok)return j;
      const msg=j?.error||`HTTP ${r.status}`;
      if(![429,502,503,504].includes(r.status))throw new Error(msg);
      lastErr=new Error(msg);
    }catch(e){lastErr=(e?.name==="AbortError")?new Error("Premium support request timed out"):e}
    finally{clearTimeout(timer)}
  }
  throw lastErr||new Error("Premium support request failed");
}'''
assert old in s
s=s.replace(old,new,1)

old='''   for(let n=0;n<supportive.length;n+=2){
     const batch=supportive.slice(n,n+2);
     const results=await Promise.all(batch.map(async g=>{
       try{
         const key=cacheKeySector(g.ticker,"20");
         if(clientCache.sectors.has(key))return {g,j:clientCache.sectors.get(key)};
         const r=await fetch(`/api/sector/${encodeURIComponent(g.ticker)}?limit=20`,{headers:{"Accept":"application/json"}});
         const j=await r.json();if(!r.ok||!j.ok)return null;
         clientCache.sectors.set(key,j);return {g,j};
       }catch(e){return null}
     }));'''
new='''   for(let n=0;n<supportive.length;n+=4){
     const batch=supportive.slice(n,n+4);
     const results=await Promise.all(batch.map(async g=>{
       const ac=new AbortController();
       const timer=setTimeout(()=>ac.abort(),20000);
       try{
         const key=cacheKeySector(g.ticker,"20");
         if(clientCache.sectors.has(key))return {g,j:clientCache.sectors.get(key)};
         const r=await fetch(`/api/sector/${encodeURIComponent(g.ticker)}?limit=20`,{headers:{"Accept":"application/json"},signal:ac.signal});
         const j=await r.json();if(!r.ok||!j.ok)return null;
         clientCache.sectors.set(key,j);return {g,j};
       }catch(e){return null}
       finally{clearTimeout(timer)}
     }));'''
assert old in s
s=s.replace(old,new,1)

old='''   const or=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols:candidates.map(x=>x.ticker)})});
   const oj=await or.json();
   if(or.ok&&oj.ok)(oj.results||[]).forEach(o=>{if(o?.ticker&&o.ok!==false)optionScanMap[o.ticker]=o});'''
new='''   {
     const ac=new AbortController();
     const timer=setTimeout(()=>ac.abort(),45000);
     try{
       const or=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols:candidates.map(x=>x.ticker)}),signal:ac.signal});
       const oj=await or.json();
       if(or.ok&&oj.ok)(oj.results||[]).forEach(o=>{if(o?.ticker&&o.ok!==false)optionScanMap[o.ticker]=o});
     }catch(e){
       throw new Error(e?.name==="AbortError"?"Options scan timed out — try again in a moment.":`Options scan failed: ${e?.message||e}`);
     }finally{clearTimeout(timer)}
   }'''
assert old in s
s=s.replace(old,new,1)

old='''   for(let n=0;n<finalists.length;n+=3){
     const batch=finalists.slice(n,n+3);
     await Promise.all(batch.map(async x=>{
       try{
         const [cr,sr]=await Promise.all([
           fetch(`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`),
           fetch(`/api/strat/${encodeURIComponent(x.ticker)}`)
         ]);
         const cj=await cr.json(),sj=await sr.json();
         if(cr.ok&&cj.ok)valueAcceptanceMap[x.ticker]=classifyValueAcceptance(cj);
         if(sr.ok&&sj.ok)stratSignalMap[x.ticker]=sj;
       }catch(e){}
     }));
   }

   // Layer 5: analyze the option premium itself for final directional candidates.
   if(st)st.textContent=`Layer 5 · checking premium support on ${finalists.length} finalists`;
   for(let n=0;n<finalists.length;n+=3){
     const batch=finalists.slice(n,n+3);
     await Promise.all(batch.map(async x=>{
       const direction=premiumDirectionFor(x);
       if(!direction)return;
       try{
         premiumSupportMap[x.ticker]=await fetchPremiumSupportReliable(x.ticker,direction,3);
       }catch(e){
         premiumSupportMap[x.ticker]={available:false,retryable:true,direction,reason:`Temporary request failure · ${e.message}`};
         console.warn("premium support",x.ticker,e);
       }
     }));
   }

   globalTopSetupData=finalists;
   automaticTopSetupsLastRun=Date.now();
   setTimeout(()=>rehydrateMissingPremiumSupport(finalists),1800);'''
new='''   for(let n=0;n<finalists.length;n+=2){
     const batch=finalists.slice(n,n+2);
     await Promise.all(batch.map(async x=>{
       const [cj,sj]=await Promise.allSettled([
         safeTickerFetchJson("/api/chart-preview",x.ticker,{period:"1m",timeframe:"1d"},{ttl:30000,timeoutMs:12000}),
         safeTickerFetchJson("/api/strat",x.ticker,{},{ttl:30000,timeoutMs:12000})
       ]);
       if(cj.status==="fulfilled"&&cj.value?.ok)valueAcceptanceMap[x.ticker]=classifyValueAcceptance(cj.value);
       if(sj.status==="fulfilled"&&sj.value?.ok)stratSignalMap[x.ticker]=sj.value;
     }));
   }

   // Premium support is display-only, so never block time-to-first-visible setups on it.
   const runPremiumSupportInBackground=async()=>{
     for(let n=0;n<finalists.length;n+=3){
       const batch=finalists.slice(n,n+3);
       await Promise.all(batch.map(async x=>{
         const direction=premiumDirectionFor(x);
         if(!direction)return;
         try{
           premiumSupportMap[x.ticker]=await fetchPremiumSupportReliable(x.ticker,direction,3);
         }catch(e){
           premiumSupportMap[x.ticker]={available:false,retryable:true,direction,reason:`Temporary request failure · ${e.message}`};
           console.warn("premium support",x.ticker,e);
         }
       }));
       try{renderTopSetups()}catch(_e){}
     }
   };

   globalTopSetupData=finalists;
   automaticTopSetupsLastRun=Date.now();
   runPremiumSupportInBackground().then(()=>{
     setTimeout(()=>rehydrateMissingPremiumSupport(finalists),1800);
   });'''
assert old in s
s=s.replace(old,new,1)

# Early Turn Watch timeout/Safari hardening from v27.3 portion of supplied patch.
s=s.replace('''       const sr=await fetch(`/api/sector/${encodeURIComponent(topLaggingSector.ticker)}?limit=10`);
       sj=await sr.json();''','''       const secAc=new AbortController();
       const secTimer=setTimeout(()=>secAc.abort(),20000);
       let sr;
       try{ sr=await fetch(`/api/sector/${encodeURIComponent(topLaggingSector.ticker)}?limit=10`,{signal:secAc.signal}); }
       finally{ clearTimeout(secTimer); }
       sj=await sr.json();''',1)
s=s.replace('''         const or=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols:holdings.map(h=>h.ticker)})});
         const oj=await or.json();''','''         const optAc=new AbortController();
         const optTimer=setTimeout(()=>optAc.abort(),30000);
         let or;
         try{ or=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols:holdings.map(h=>h.ticker)}),signal:optAc.signal}); }
         finally{ clearTimeout(optTimer); }
         const oj=await or.json();''',1)
s=s.replace('''       const r=await fetch(`/api/premium-support/${encodeURIComponent(x.ticker)}?direction=bullish`);
       const j=await r.json();''','''       const psAc=new AbortController();
       const psTimer=setTimeout(()=>psAc.abort(),15000);
       let r;
       try{ r=await fetch(safeTickerUrl("/api/premium-support",x.ticker,{direction:"bullish"}),{signal:psAc.signal}); }
       finally{ clearTimeout(psTimer); }
       const j=await r.json();''',1)

p.write_text(s)

r=Path('README.txt')
readme=r.read_text()
entry='''v27.4 — SCAN SPEED: NON-BLOCKING PREMIUM SUPPORT + CONCURRENCY MATCHED TO SERVER THREADS
- Profiled the Top Setups pipeline stage by stage. Premium support was blocking the whole scan even though it only changes card display, not hardPass or qualification score. It now runs in the background and fills in progressively after setups render.
- Layer 2 holdings concurrency is raised from 2 to 4 to match the 4-thread Render server. Layer 4 is reduced from 3 tickers (6 simultaneous requests) to 2 tickers (4 simultaneous requests), matching server capacity instead of oversubscribing it.
- v27.3 reliability changes are included here: bounded timeouts across the remaining scan stages, Safari-safe ticker URLs for premium/STRAT/value calls, independent settled Layer 4 requests, and removal of dead auditHoldings().
- Expected effect: substantially faster time-to-first-visible setups while preserving the same qualification/ranking behavior; premium details continue populating afterward.

v27.3 — SCAN RELIABILITY: TIMEOUTS + SAFARI-SAFE URLS ACROSS THE FULL PIPELINE
- Added bounded request timeouts to Layers 2, 3, 4 and 5 plus Early Turn Watch so one hung request cannot freeze a Promise.all batch indefinitely.
- Routed Layer 4/5 ticker requests through the app's Safari-safe URL helpers and made Layer 4 endpoint failures independent with Promise.allSettled.
- Removed dead auditHoldings(), which referenced a non-existent DOM element and was never called.

'''
assert readme.startswith('v27.2')
r.write_text(entry+readme)

# Static validation: app.py is a Flask module and CI runner intentionally does not install runtime deps.
out=p.read_text()
assert 'APP_VERSION = "27.4"' in out
assert 'for(let n=0;n<supportive.length;n+=4)' in out
assert 'for(let n=0;n<finalists.length;n+=2)' in out
assert 'const runPremiumSupportInBackground=async()=>{' in out
assert 'safeTickerFetchJson("/api/chart-preview"' in out
assert 'safeTickerUrl("/api/premium-support",ticker,{direction})' in out
assert 'async function auditHoldings()' not in out
print('v27.4 patch applied')
