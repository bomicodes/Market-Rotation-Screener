from pathlib import Path

app = Path('app.py')
readme = Path('README.txt')
s = app.read_text()
r = readme.read_text()

old_version = 'APP_VERSION = "26.7"'
new_version = 'APP_VERSION = "26.8"'
if old_version not in s:
    raise SystemExit('Expected v26.7 APP_VERSION not found')
s = s.replace(old_version, new_version, 1)

old = '''   const waits=[0,1000,3000,7000,12000];
   let lastErr=null;
   for(let attempt=0;attempt<waits.length;attempt++){
     if(waits[attempt])await new Promise(r=>setTimeout(r,waits[attempt]));
     let r;
     try{r=await window.fetch(url,{method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}})}
     catch(e){lastErr=new Error(`Request could not be dispatched: ${e?.message||e}`);continue;}
     let raw="",j={};'''

new = '''   const waits=[0,1000,3000,7000,12000];
   const requestTimeoutMs=Number(opts.timeoutMs)||12000;
   let lastErr=null;
   for(let attempt=0;attempt<waits.length;attempt++){
     if(waits[attempt])await new Promise(r=>setTimeout(r,waits[attempt]));
     let r;
     const ac=new AbortController();
     const timer=setTimeout(()=>ac.abort(),requestTimeoutMs);
     try{r=await window.fetch(url,{method:"GET",credentials:"same-origin",headers:{Accept:"application/json"},signal:ac.signal})}
     catch(e){
       const timedOut=e?.name==="AbortError";
       lastErr=new Error(timedOut?`Request timed out after ${requestTimeoutMs}ms`:`Request could not be dispatched: ${e?.message||e}`);
       continue;
     }
     finally{clearTimeout(timer)}
     let raw="",j={};'''

if old not in s:
    raise SystemExit('Expected safeTickerFetchJson retry block not found')
s = s.replace(old, new, 1)

entry = '''v26.8 — TIMEOUT FOR safeTickerFetchJson (FOLLOW-UP TO THE v26.7 LAYER 2.5 FIX)
- safeTickerFetchJson (shared by flow, options, STRAT, institutional-context, and single-stock chart-preview) had retry logic for HTTP error codes (429/502/503/504) but no actual request timeout — a request that hung with no response and no error would block forever, the same class of stall fixed for Layer 2.5's bulk scan in v26.7, just for these single-ticker deep-dive calls instead.
- Fixed: each attempt now runs under a 12-second AbortController timeout (configurable per call via opts.timeoutMs). A timeout is treated as retryable, same as the existing dispatch-failure handling, so it flows through the same backoff schedule and stale-cache fallback that already existed — no change to that behavior, just closing the "hangs forever with no timeout at all" gap.
- Verified with faithful retry-loop simulations: a request that hangs on early attempts can recover on a later attempt, while a request that hangs on every attempt exhausts retries and fails in bounded time rather than never resolving.
- NOTE: the v26.6 "never scanned" message-clarity patch is still pending — confirmed absent from main as of this version. v26.7's Layer 2.5 fix did land successfully.

'''
if not r.startswith('v26.7 — FIX LAYER 2.5 SCAN STALL'):
    raise SystemExit('README does not start with expected v26.7 entry')
r = entry + r

app.write_text(s)
readme.write_text(r)

assert 'APP_VERSION = "26.8"' in s
assert 'const requestTimeoutMs=Number(opts.timeoutMs)||12000;' in s
assert 'signal:ac.signal' in s
assert 'finally{clearTimeout(timer)}' in s
assert 'Request timed out after ${requestTimeoutMs}ms' in s
print('v26.8 patch applied')
