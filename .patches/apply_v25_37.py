from pathlib import Path

p=Path('app.py')
s=p.read_text()

s=s.replace('APP_VERSION = "25.36"','APP_VERSION = "25.37"',1)

# Keep completed premium analysis reusable across desktop/mobile for longer.
s=s.replace('cached_refresh_safe(f"premium-support-v25-9:{ticker.upper()}:{direction}",lambda:premium_support_payload(ticker,direction,base),ttl=300)',
            'cached_refresh_safe(f"premium-support-v25-9:{ticker.upper()}:{direction}",lambda:premium_support_payload(ticker,direction,base),ttl=1800)',1)

anchor='const premiumSupportMap=window.premiumSupportMap||(window.premiumSupportMap={});\n'
helper=r'''const premiumSupportMap=window.premiumSupportMap||(window.premiumSupportMap={});
function premiumDirectionFor(x){
 const va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker];
 return (va?.direction&&va.direction!=="neutral")?va.direction:((strat?.continuity==="bullish"||strat?.continuity==="bearish")?strat.continuity:null);
}
function sleepMs(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function fetchPremiumSupportReliable(ticker,direction,attempts=3){
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
}
async function rehydrateMissingPremiumSupport(rows){
 const missing=(rows||[]).filter(x=>{
   const p=premiumSupportMap[x.ticker];
   return !p || p.retryable===true;
 }).slice(0,10);
 if(!missing.length)return;
 for(let n=0;n<missing.length;n+=2){
   await Promise.all(missing.slice(n,n+2).map(async x=>{
     const direction=premiumDirectionFor(x);if(!direction)return;
     try{
       premiumSupportMap[x.ticker]=await fetchPremiumSupportReliable(x.ticker,direction,2);
     }catch(e){premiumSupportMap[x.ticker]={available:false,retryable:true,direction,reason:`Temporary request failure · ${e.message}`};}
   }));
 }
 renderTopSetups();
}
'''
if anchor not in s: raise SystemExit('premium map anchor not found')
s=s.replace(anchor,helper,1)

old=r'''       const va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker];
       const direction=(va?.direction&&va.direction!=="neutral")?va.direction:((strat?.continuity==="bullish"||strat?.continuity==="bearish")?strat.continuity:null);
       if(!direction)return;
       try{
         const r=await fetch(`/api/premium-support/${encodeURIComponent(x.ticker)}?direction=${encodeURIComponent(direction)}`),j=await r.json();
         if(r.ok&&j.ok)premiumSupportMap[x.ticker]=j;
         else premiumSupportMap[x.ticker]={available:false,reason:j?.error||`HTTP ${r.status}`};
       }catch(e){console.warn("premium support",x.ticker,e)}
'''
new=r'''       const direction=premiumDirectionFor(x);
       if(!direction)return;
       try{
         premiumSupportMap[x.ticker]=await fetchPremiumSupportReliable(x.ticker,direction,3);
       }catch(e){
         premiumSupportMap[x.ticker]={available:false,retryable:true,direction,reason:`Temporary request failure · ${e.message}`};
         console.warn("premium support",x.ticker,e);
       }
'''
if old not in s: raise SystemExit('layer5 premium fetch block not found')
s=s.replace(old,new,1)

# After the primary run renders, retry only missing/transient rows in the background.
old2='''   globalTopSetupData=finalists;\n   automaticTopSetupsLastRun=Date.now();'''
new2='''   globalTopSetupData=finalists;\n   automaticTopSetupsLastRun=Date.now();\n   setTimeout(()=>rehydrateMissingPremiumSupport(finalists),1800);'''
if old2 not in s: raise SystemExit('global finalists anchor not found')
s=s.replace(old2,new2,1)

p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
entry='''v25.37 — MOBILE TOP SETUP PREMIUM RELIABILITY\n- Premium Support results are now cached server-side for 30 minutes by ticker/direction so a result completed on desktop can be reused by mobile without repeating the expensive Alpaca history work immediately.\n- Added bounded retries for transient 429/502/503/504/network failures in the Top Setups premium layer.\n- Failed mobile requests are now explicitly stored as retryable failures instead of silently disappearing into console-only errors / showing as not evaluated.\n- Added a delayed rehydration pass that retries only missing/transient premium rows and re-renders Top Setups when results arrive.\n\n'''
r.write_text(entry+rs)
