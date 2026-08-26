from pathlib import Path

p=Path('app.py')
s=p.read_text()

s=s.replace('APP_VERSION = "24.4"','APP_VERSION = "24.5"')
s=s.replace('ALPACA_OPTIONS_FEED = os.environ.get("ALPACA_OPTIONS_FEED", "indicative").strip().lower() or "indicative"','ALPACA_OPTIONS_FEED = os.environ.get("ALPACA_OPTIONS_FEED", "opra").strip().lower() or "opra"')
s=s.replace('ALPACA_STOCK_FEED = os.environ.get("ALPACA_STOCK_FEED", "iex").strip().lower() or "iex"','ALPACA_STOCK_FEED = os.environ.get("ALPACA_STOCK_FEED", "sip").strip().lower() or "sip"')

old='''function safeTickerEndpoint(path,ticker,query=""){
 const sym=normalizeStockTicker(ticker);
 if(!isSafeStockTicker(sym))throw new Error(`Invalid ticker symbol: ${sym||ticker}`);
 const p=String(path||"").startsWith("/")?String(path):`/${String(path||"")}`;
 // Keep same-origin API calls relative for Safari compatibility.
 return `${p}/${encodeURIComponent(sym)}${query||""}`;
}'''
new='''function safeTickerEndpoint(path,ticker,query=""){
 const sym=normalizeStockTicker(ticker);
 if(!isSafeStockTicker(sym))throw new Error(`Invalid ticker symbol: ${sym||ticker}`);
 const p=String(path||"").startsWith("/")?String(path):`/${String(path||"")}`;
 return `${p}/${encodeURIComponent(sym)}${query||""}`;
}
function safeTickerUrl(path,ticker,params={}){
 // Safari has intermittently thrown a DOMException ("The string did not match the expected pattern")
 // before dispatching chart requests when a prebuilt query string is passed through the generic helper.
 // Build the query from known scalar values and keep the final URL same-origin and relative.
 const base=safeTickerEndpoint(path,ticker);
 const q=[];
 Object.entries(params||{}).forEach(([k,v])=>{
   if(v===undefined||v===null)return;
   q.push(`${encodeURIComponent(String(k))}=${encodeURIComponent(String(v))}`);
 });
 return q.length?`${base}?${q.join("&")}`:base;
}'''
if old not in s: raise SystemExit('safeTickerEndpoint block not found')
s=s.replace(old,new)

marker='''function updatePreviewVPStatus(){'''
fallback='''function drawBasicPricePreview(payload){
 // Minimal Safari-safe fallback. If an advanced SVP/canvas feature fails, the user
 // still gets a readable OHLC chart instead of a blank panel.
 const c=document.getElementById("pricePreviewChart"),ctx=c?.getContext("2d");
 if(!c||!ctx)return;
 const rows=(payload?.bars||[]).filter(r=>Number.isFinite(Number(r.close)));
 const W=c.width,H=c.height;ctx.clearRect(0,0,W,H);ctx.fillStyle="#071018";ctx.fillRect(0,0,W,H);
 if(!rows.length){ctx.fillStyle="#94a3b8";ctx.font="12px sans-serif";ctx.fillText("Chart data unavailable",24,32);return;}
 const pad={l:62,r:34,t:28,b:42},highs=rows.map(r=>Number(r.high??r.close)),lows=rows.map(r=>Number(r.low??r.close));
 let lo=Math.min(...lows),hi=Math.max(...highs),rg=Math.max(.01,hi-lo);lo-=rg*.05;hi+=rg*.05;
 const X=i=>pad.l+(i+.5)*(W-pad.l-pad.r)/rows.length,Y=v=>pad.t+(hi-v)/(hi-lo)*(H-pad.t-pad.b),sp=(W-pad.l-pad.r)/rows.length,bw=Math.max(1.5,Math.min(8,sp*.45));
 rows.forEach((r,i)=>{const o=Number(r.open??r.close),cl=Number(r.close),h=Number(r.high??cl),l=Number(r.low??cl),up=cl>=o,x=X(i);ctx.strokeStyle=ctx.fillStyle=up?"#18c98b":"#f04b4b";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,Y(h));ctx.lineTo(x,Y(l));ctx.stroke();ctx.fillRect(x-bw/2,Math.min(Y(o),Y(cl)),bw,Math.max(1,Math.abs(Y(o)-Y(cl))));});
 ctx.fillStyle="#94a3b8";ctx.font="11px sans-serif";ctx.textAlign="left";ctx.fillText("Basic chart fallback · advanced volume-profile rendering unavailable",pad.l,H-14);
}

'''
if marker not in s: raise SystemExit('updatePreviewVPStatus marker not found')
s=s.replace(marker,fallback+marker,1)

old2='''   const chartUrl=safeTickerEndpoint("/api/chart-preview",ticker,`?period=${encodeURIComponent(period)}&timeframe=${encodeURIComponent(previewTimeframe)}`);
   const r=await fetch(chartUrl,{headers:{"Accept":"application/json"}});
   const j=await r.json();
   if(seq!==previewRequestSeq)return;
   if(!r.ok||!j.ok)throw Error(j.error||"Chart preview failed");
   previewPayload=j;previewTimeframe=(j.timeframe||previewTimeframe).toLowerCase();
   drawPricePreview(j);'''
new2='''   const chartUrl=safeTickerUrl("/api/chart-preview",ticker,{period,timeframe:previewTimeframe});
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
   previewPayload=j;previewTimeframe=(j.timeframe||previewTimeframe).toLowerCase();
   try{drawPricePreview(j);}catch(renderErr){console.error("Advanced chart render failed",ticker,renderErr);drawBasicPricePreview(j);}'''
if old2 not in s: raise SystemExit('chart loader block not found')
s=s.replace(old2,new2,1)

p.write_text(s)
print('patched app.py v24.5')
