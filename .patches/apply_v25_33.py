from pathlib import Path
p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "25.32"','APP_VERSION = "25.33"',1)
# News endpoint: allow GET so Safari can avoid the JSON POST RequestInit path.
s=s.replace('@app.post("/api/news-context")','@app.route("/api/news-context", methods=["GET","POST"])',1)
s=s.replace('''        body=request.get_json(silent=True) or {}; raw=body.get("symbols") or []''','''        if request.method=="GET":
            raw=[x for x in str(request.args.get("symbols") or "").split(",") if x]
        else:
            body=request.get_json(silent=True) or {}; raw=body.get("symbols") or []''',1)
# Remove Intl/locales edge cases and only render valid absolute http(s) links.
s=s.replace('''function newsTime(ts){
 if(!ts)return "";const d=new Date(Number(ts)*1000);if(Number.isNaN(d.getTime()))return "";
 return d.toLocaleString([], {month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});
}
function newsLink(x){const h=String(x?.headline||"");const safe=h.replace(/</g,"&lt;").replace(/>/g,"&gt;");return x?.url?`<a href="${x.url}" target="_blank" rel="noopener" class="newsHeadline">${safe}</a>`:`<span class="newsHeadline">${safe}</span>`}''','''function newsTime(ts){
 if(!ts)return "";const d=new Date(Number(ts)*1000);if(Number.isNaN(d.getTime()))return "";
 const m=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getMonth()];
 let h=d.getHours(),ap=h>=12?"PM":"AM";h=h%12||12;return `${m} ${d.getDate()} · ${h}:${String(d.getMinutes()).padStart(2,"0")} ${ap}`;
}
function newsLink(x){
 const esc=v=>String(v||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
 const safe=esc(x?.headline||"");const u=String(x?.url||"").trim();
 const ok=/^https?:\\/\\//i.test(u);
 return ok?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer" class="newsHeadline">${safe}</a>`:`<span class="newsHeadline">${safe}</span>`;
}''',1)
old='''   const symbols=currentNewsSymbols();
   const r=await fetch("/api/news-context",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({symbols})});
   const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||`News failed (${r.status})`);'''
new='''   const symbols=currentNewsSymbols();
   const safeSymbols=(symbols||[]).map(normalizeStockTicker).filter(isSafeStockTicker).slice(0,8);
   const newsUrl="/api/news-context"+(safeSymbols.length?`?symbols=${safeSymbols.map(x=>encodeURIComponent(x)).join(",")}`:"");
   let r;
   try{r=await window.fetch(newsUrl)}catch(e){throw new Error(`News request dispatch failed: ${e?.name||"Error"}: ${e?.message||e}`)}
   const raw=await r.text();let j={};
   try{j=raw?JSON.parse(raw):{}}catch(e){throw new Error(`News response unreadable (${r.status})`)}
   if(!r.ok||!j.ok)throw Error(j.error||`News failed (${r.status})`);'''
if old not in s:
    raise SystemExit('news request block not found')
s=s.replace(old,new,1)
p.write_text(s)
r=Path('README.txt');rs=r.read_text();r.write_text('''v25.33 — IOS SAFARI NEWS CONTEXT DOMEXCEPTION HARDENING
- Moved News + Catalyst Context refresh from a JSON POST RequestInit path to a plain same-origin GET with a bounded encoded ticker list, matching the simpler Safari-safe request pattern used elsewhere.
- Added GET support to /api/news-context while retaining POST compatibility.
- Removed locale/Intl formatting from headline timestamps and only emits clickable links for valid http(s) URLs, reducing additional WebKit parsing surfaces.
- News errors now distinguish request-dispatch failures from unreadable/server responses instead of surfacing only the opaque DOMException.

'''+rs)
