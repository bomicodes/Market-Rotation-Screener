from pathlib import Path
p=Path('app.py'); s=p.read_text()
s=s.replace('APP_VERSION = "25.31"','APP_VERSION = "25.32"',1)
old='''function safeScrollIntoView(el,{smooth=false}={}){
 if(!el)return false;
 try{
   // Prefer standards-based top alignment. If an older WebKit build rejects
   // the options overload, immediately fall back to the legacy Boolean form.
   el.scrollIntoView({behavior:smooth?"smooth":"auto",block:"start"});
   return true;
 }catch(e){
   try{el.scrollIntoView(true);return true;}catch(_e){return false;}
 }
}'''
new='''function safeScrollIntoView(el,{smooth=false}={}){
 if(!el)return false;
 try{
   // iOS Safari/WebKit has repeatedly thrown the opaque DOMException
   // “The string did not match the expected pattern” on the options overload.
   // Use only the legacy Boolean overload; CSS handles scroll behavior.
   el.scrollIntoView(true);
   return true;
 }catch(e){return false}
}'''
assert old in s
s=s.replace(old,new,1)
old2='''   const url=safeTickerUrl("/api/sector",requestedSector,{limit:String(lim)});
   const r=await window.fetch(url,{method:"GET",credentials:"same-origin",headers:{"Accept":"application/json"}});
   const j=await r.json();'''
new2='''   // Keep this high-frequency Safari path maximally boring: a relative ASCII URL
   // and default fetch options. Avoid WebKit URL/Request overload edge cases.
   const url=`/api/sector/${encodeURIComponent(requestedSector)}?limit=${encodeURIComponent(String(lim))}`;
   const r=await fetch(url);
   const j=await r.json();'''
assert old2 in s
s=s.replace(old2,new2,1)
old3='''     const r=await window.fetch(safeTickerUrl("/api/sector",currentSector,{limit:"all"}),{method:"GET",credentials:"same-origin",headers:{"Accept":"application/json"}});'''
new3='''     const sectorSym=normalizeStockTicker(currentSector);
     if(!isSafeStockTicker(sectorSym))throw Error(`Invalid sector symbol: ${sectorSym}`);
     const r=await fetch(`/api/sector/${encodeURIComponent(sectorSym)}?limit=all`);'''
assert old3 in s
s=s.replace(old3,new3,1)
p.write_text(s)
r=Path('README.txt'); rs=r.read_text(); r.write_text('''v25.32 — IOS SAFARI STOCK SCREEN DOMEXCEPTION HARDENING
- Removed the scrollIntoView options-object overload again. iOS Safari/WebKit can throw the opaque “The string did not match the expected pattern” DOMException from this overload; all app scrolling now uses the legacy Boolean overload only.
- Simplified Stock Screen sector requests to plain same-origin relative fetch URLs with default Request options, removing another WebKit URL/Request construction surface from the PBW/ETF load path.
- Applied the same plain-fetch construction to the all-holdings ticker-search expansion path.

'''+rs)
