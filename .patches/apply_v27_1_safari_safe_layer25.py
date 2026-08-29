from pathlib import Path

p=Path('app.py')
rp=Path('README.txt')
s=p.read_text(); r=rp.read_text()

if 'APP_VERSION = "27.0"' in s:
    s=s.replace('APP_VERSION = "27.0"','APP_VERSION = "27.1"',1)
elif 'APP_VERSION = "27.1"' not in s:
    raise SystemExit('Expected v27.0 APP_VERSION not found')

s=s.replace('@app.post("/api/early-reversal-scan")\ndef api_early_reversal_scan():\n    body=request.get_json(silent=True) or {}\n    raw=body.get("tickers") or []',
'''@app.route("/api/early-reversal-scan",methods=["GET","POST"])
def api_early_reversal_scan():
    if request.method=="GET":
        raw=(request.args.get("tickers") or "").split(",")
    else:
        body=request.get_json(silent=True) or {}
        raw=body.get("tickers") or []''',1)

old='''     const ac=(typeof AbortController!=="undefined")?new AbortController():null;
     const timer=ac?setTimeout(()=>ac.abort(),60000):null;
     const resp=await fetch("/api/early-reversal-scan",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json",Accept:"application/json"},body:JSON.stringify({tickers:candidates.map(x=>x.ticker)}),...(ac?{signal:ac.signal}:{})});
     if(timer)clearTimeout(timer);
     const j=await resp.json();'''
new='''     const tickerList=candidates.map(x=>String(x.ticker||"").toUpperCase()).filter(Boolean).join(",");
     const url=`/api/early-reversal-scan?tickers=${encodeURIComponent(tickerList)}`;
     const request=fetch(url);
     const timeout=new Promise((_,reject)=>setTimeout(()=>reject(new Error("Layer 2.5 bulk scan timeout")),60000));
     const resp=await Promise.race([request,timeout]);
     const j=await resp.json();'''
if old not in s:
    if new not in s: raise SystemExit('Expected v27.0 Layer 2.5 fetch block not found')
else:
    s=s.replace(old,new,1)

entry='''v27.1 — SAFARI-SAFE LAYER 2.5 BULK REQUEST
- v27.0 successfully moved Layer 2.5 to a single bulk server endpoint, but its browser call used a JSON POST with a complex fetch RequestInit object. That request pattern has previously produced opaque iOS Safari/WebKit DOMException failures elsewhere in this app.
- Render logs after v27.0 showed no /api/early-reversal-scan request reaching the server during the reported failure, confirming the failure was occurring client-side before dispatch rather than inside the bulk endpoint.
- Layer 2.5 now uses a plain same-origin GET with one URL-encoded comma-separated ticker list. The server accepts GET while retaining POST compatibility. No JSON body, custom headers, spread RequestInit, or AbortController is required for dispatch.
- A 60-second Promise.race still prevents the UI from waiting forever, and failure remains optional enrichment: Top Setups continues without early-price bonuses.

'''
if not r.startswith('v27.1 — SAFARI-SAFE LAYER 2.5'):
    r=entry+r

p.write_text(s); rp.write_text(r)
assert 'APP_VERSION = "27.1"' in s
assert '@app.route("/api/early-reversal-scan",methods=["GET","POST"])' in s
start=s.find('Layer 2.5 · checking early daily reversals')
end=s.find('candidates=candidates.sort',start)
layer25=s[start:end]
assert start >= 0 and end > start
assert 'const request=fetch(url);' in layer25
assert 'encodeURIComponent(tickerList)' in layer25
assert 'fetch("/api/early-reversal-scan",{method:"POST"' not in layer25
print('v27.1 patch applied')
