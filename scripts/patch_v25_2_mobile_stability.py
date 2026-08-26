from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()
orig=s

# Keep v25.1 badge for this hotfix to avoid touching unrelated version plumbing.

# 1) Replace ticker fetch helper with retry/backoff for transient service errors.
pat=r'const tickerRequestInflight=new Map\(\),tickerResponseCache=new Map\(\);\nasync function safeTickerFetchJson\(path,ticker,params=\{\},opts=\{\}\)\{.*?\n\}\nasync function openSectorStockTicker'
m=re.search(pat,s,re.S)
if not m:
    raise SystemExit('safeTickerFetchJson block not found')
new='''const tickerRequestInflight=new Map(),tickerResponseCache=new Map();
async function safeTickerFetchJson(path,ticker,params={},opts={}){
 const url=safeTickerUrl(path,ticker,params),ttl=Number(opts.ttl||0),now=Date.now();
 const cached=tickerResponseCache.get(url);
 if(ttl>0&&cached&&now-cached.at<ttl)return cached.value;
 if(tickerRequestInflight.has(url))return tickerRequestInflight.get(url);
 const promise=(async()=>{
   const waits=[0,1000,3000,7000,12000];
   let lastErr=null;
   for(let attempt=0;attempt<waits.length;attempt++){
     if(waits[attempt])await new Promise(r=>setTimeout(r,waits[attempt]));
     let r;
     try{r=await window.fetch(url,{method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}})}
     catch(e){lastErr=new Error(`Request could not be dispatched: ${e?.message||e}`);continue;}
     let raw="",j={};
     try{raw=await r.text();j=raw?JSON.parse(raw):{};}
     catch(e){
       lastErr=new Error(`Service returned an unreadable response (${r.status})`);
       if([429,502,503,504].includes(r.status))continue;
       throw lastErr;
     }
     if(r.ok&&j?.ok){
       if(ttl>0)tickerResponseCache.set(url,{at:Date.now(),value:j});
       return j;
     }
     lastErr=new Error(j?.error||`Request failed (${r.status})`);
     if(![429,502,503,504].includes(r.status))throw lastErr;
   }
   // If this browser has a prior good response for the same URL, degrade to it
   // rather than blanking the whole panel during a transient Render restart.
   const stale=tickerResponseCache.get(url);
   if(stale)return {...stale.value,_client_stale:true};
   throw lastErr||new Error("Request failed");
 })();
 tickerRequestInflight.set(url,promise);
 try{return await promise}finally{tickerRequestInflight.delete(url)}
}
async function openSectorStockTicker'''
s=s[:m.start()]+new+s[m.end():]

# 2) Stop the automatic market-wide Top Setups sector burst on mobile. The
# dashboard remains usable; Top Setups can be run from a non-mobile session.
marker='async function runAutomaticTopSetups(){'
if marker not in s:
    raise SystemExit('runAutomaticTopSetups marker not found')
s=s.replace(marker, marker+'''\n if(window.matchMedia&&window.matchMedia("(max-width: 760px)").matches){\n   const st=document.getElementById("topSetupStatus");\n   if(st)st.textContent="Top Setups auto-scan paused on mobile to keep ticker analysis fast.";\n   return;\n }''',1)

if s==orig:
    raise SystemExit('no changes made')
p.write_text(s)
print('patched mobile stability + retry')
