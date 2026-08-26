from pathlib import Path
import re
p=Path('app.py');s=p.read_text()

def one(old,new,label):
 global s
 n=s.count(old)
 if n!=1: raise SystemExit(f'{label}: expected 1, found {n}')
 s=s.replace(old,new,1)

def sub(pattern,repl,label,flags=0):
 global s
 s2,n=re.subn(pattern,repl,s,count=1,flags=flags)
 if n!=1: raise SystemExit(f'{label}: expected 1, found {n}')
 s=s2

# Server becomes authoritative after one-time local migration.
sub(r'async function syncWatchlistFromServer\(\)\{.*?\n\}\n\nfunction saveLiveWatchlist', '''async function syncWatchlistFromServer(){
 try{
   let r=await fetch("/api/watchlist"),j=await r.json();
   if(!j?.ok||!Array.isArray(j.items))return;
   // One-time migration: if the persistent server list is empty but this
   // browser has old local bookmarks, push them before reconciliation.
   if(!j.items.length&&liveWatchlist.length){
     await Promise.all(liveWatchlist.map(x=>fetch("/api/watchlist",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticker:x.ticker,added_price:x.added_price??null})}).catch(()=>null)));
     r=await fetch("/api/watchlist");j=await r.json();if(!j?.ok||!Array.isArray(j.items))return;
   }
   const oldByTicker=new Map(liveWatchlist.map(x=>[liveWatchKey(x.ticker),x]));
   liveWatchlist=j.items.map(row=>({...oldByTicker.get(liveWatchKey(row.ticker)),ticker:row.ticker,added_at:row.added_at,added_price:row.added_price}));
   try{localStorage.setItem(LIVE_WATCHLIST_KEY,JSON.stringify(liveWatchlist))}catch(e){}
   renderLiveWatchlist();refreshLiveBookmarkButtons();
 }catch(e){}
}

function saveLiveWatchlist''','watchlist sync',re.S)

one('''   document.querySelectorAll("[data-live-watch-remove]").forEach(btn=>btn.addEventListener("click",()=>{\n     const key=liveWatchKey(btn.dataset.liveWatchRemove);\n     liveWatchlist=liveWatchlist.filter(x=>liveWatchKey(x.ticker)!==key);\n     saveLiveWatchlist();\n   }));''','''   document.querySelectorAll("[data-live-watch-remove]").forEach(btn=>btn.addEventListener("click",()=>{\n     const ticker=btn.dataset.liveWatchRemove,key=liveWatchKey(ticker);\n     liveWatchlist=liveWatchlist.filter(x=>liveWatchKey(x.ticker)!==key);\n     saveLiveWatchlist();\n     fetch(`/api/watchlist/${encodeURIComponent(ticker)}`,{method:"DELETE"}).then(()=>syncWatchlistFromServer()).catch(()=>{});\n   }));''','watchlist remove')

# Macro card: surface importance + time so overnight/event risk is obvious.
sub(r'async function refreshMacroCalendar\(\)\{.*?\n\}\ndocument.addEventListener\("DOMContentLoaded",refreshMacroCalendar\);', '''async function refreshMacroCalendar(){
 const el=document.getElementById("dashboardMacro");if(!el)return;
 try{
   const r=await fetch("/api/macro-calendar?within_days=90"),j=await r.json();
   if(!j?.ok||!Array.isArray(j.events))return;
   if(!j.events.length){el.innerHTML=`<div class="note">No confirmed major macro dates in the next 90 days.</div>`;return;}
   el.innerHTML=j.events.map(e=>{
     const high=e.importance==="HIGH",urgent=e.days_away<=1&&high;
     const tag=high?"HIGH":(e.importance||"WATCH");
     return `<div class="breadthRow"><div class="name">${urgent?"⚠️ ":""}${e.label}<div class="tiny">${tag} · ${e.time||"time TBA"} · ${e.source||"official source"}</div></div><div class="val ${urgent?"neg":""}">${e.date}</div><div class="move ${urgent?"neg":""}">${e.days_away}d</div></div>`;
   }).join("");
 }catch(e){}
}
document.addEventListener("DOMContentLoaded",refreshMacroCalendar);''','macro UI',re.S)

# Historical expectancy factor now measures outcomes, not merely existence.
one('''function histExpectancyLabel(h){if(!h||!h.count)return {label:"Building sample",detail:"Snapshot database will accumulate this setup signature"};const r=h.returns?.["5"]||{};return {label:`${h.count} snapshots`,detail:`5D win ${r.win_rate==null?"—":r.win_rate+"%"} · median ${r.median==null?"—":r.median+"%"}`}}''','''function histExpectancyLabel(h){if(!h||!h.count)return {label:"Exact setup N=0",detail:"Building a clean signature-specific sample"};const r=h.returns?.["5"]||{};return {label:`Exact setup N=${h.count}`,detail:`5D win ${r.win_rate==null?"—":r.win_rate+"%"} · median ${r.median==null?"—":r.median+"%"}`}}\nfunction expectancyFactor(h){const r=h?.returns?.["5"]||{},n=Number(r.n||0),wr=Number(r.win_rate),med=Number(r.median);if(n<5)return 5;if(wr>=65&&med>0)return 10;if(wr>=55&&med>0)return 8;if(wr>=50&&med>=0)return 6;if(wr<45||med<0)return 3;return 5}''','expectancy label')
one('''["Expectancy",hist.count?7:5]''','''["Expectancy",expectancyFactor(hist)]''','expectancy factor')

# Add macro risk to the decision panel and prevent missing catalyst dates from looking green.
one('''const rs=c.relative_strength||{},s=c.structure||{},cat=c.catalyst||{},flow=flowEvidenceFor(ticker),gx=gexImplicationFor(ticker,c),hist=histExpectancyLabel(c.historical_expectancy),r5=rs["5"]||{},r10=rs["10"]||{},r20=rs["20"]||{},ct=cat.next_earnings?`${cat.next_earnings} · ${cat.days_to_earnings}d`:'No confirmed earnings date available',cc=cat.days_to_earnings!=null&&cat.days_to_earnings<=3?'instBad':cat.days_to_earnings!=null&&cat.days_to_earnings<=10?'instWarn':'instGood';''','''const rs=c.relative_strength||{},s=c.structure||{},cat=c.catalyst||{},macro=c.macro_risk||{},flow=flowEvidenceFor(ticker),gx=gexImplicationFor(ticker,c),hist=histExpectancyLabel(c.historical_expectancy),r5=rs["5"]||{},r10=rs["10"]||{},r20=rs["20"]||{},ct=cat.next_earnings?`${cat.next_earnings} · ${cat.days_to_earnings}d`:'No confirmed earnings date available',cc=cat.days_to_earnings==null?'instWarn':cat.days_to_earnings<=3?'instBad':cat.days_to_earnings<=10?'instWarn':'instGood';''','macro context')
one('''<div class="instCard"><div class="k">CATALYST RISK</div><div class="v ${cc}">${cat.risk||"Unknown"}</div><div class="d">${ct}</div></div><div class="instCard"><div class="k">HISTORICAL EXPECTANCY</div>''','''<div class="instCard"><div class="k">CATALYST RISK</div><div class="v ${cc}">${cat.risk||"Unknown"}</div><div class="d">${ct}</div></div><div class="instCard"><div class="k">MACRO RISK</div><div class="v ${macro.risk==="HIGH"?'instBad':macro.risk==="ELEVATED"?'instWarn':''}">${macro.risk||"—"}</div><div class="d">${(macro.events||[]).slice(0,2).map(e=>`${e.days_away}d · ${e.type} ${e.time||''}`).join(' · ')||'No major event in 7D'}</div></div><div class="instCard"><div class="k">HISTORICAL EXPECTANCY</div>''','macro card')

# A+ means complete, not merely high score. Flow is intentionally optional because
# current flow direction is unclassified; macro, earnings, structure, options, VP and STRAT are not.
insert='''\nfunction setupCompleteness(x,e){\n const c=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null,opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],st=stratSignalMap[x.ticker];\n const checks={RRG:!!(e?.alignment&&e.alignment!=="NONE"),Value:!!va,STRAT:!!st,Options:!!opt,Context:!!c,Catalyst:!!(c?.catalyst&&c.catalyst.risk!=="Unknown"),Macro:!!c?.macro_risk};\n const missing=Object.entries(checks).filter(([,v])=>!v).map(([k])=>k);return {complete:missing.length===0,missing};\n}\n'''
marker='function renderTopSetups(){'
if marker not in s: raise SystemExit('renderTopSetups marker missing')
s=s.replace(marker,insert+marker,1)
one('''const va=e.va,label=e.score>=80&&va?.strength==="CONFIRMED"&&e.stratPass?"A+ SETUP":"A-QUALITY WATCH",alignmentLabel''','''const va=e.va,complete=setupCompleteness(x,e),label=e.score>=80&&va?.strength==="CONFIRMED"&&e.stratPass&&complete.complete?"A+ SETUP":"A-QUALITY WATCH",alignmentLabel''','A+ completeness')
one('''<div class="topSetupTrigger">TRIGGER · <b>${trigger}</b></div>\n<div class="topSetupActions">''','''<div class="topSetupTrigger">TRIGGER · <b>${trigger}</b>${complete.complete?'':`<div class="tiny instWarn">Incomplete: ${complete.missing.join(', ')}</div>`}</div>\n<div class="topSetupActions">''','A+ missing display')

# Avoid double-counting GEX in the v24 score wrapper; base Top Setup scoring already
# applies dealer-gamma and room-to-wall adjustments.
one('''const gx=gexImplicationFor(x.ticker,c);if(gx.detail.includes("headwind"))score-=4;else if(gx.detail.includes("accelerate"))score+=3;return {...b,score:Math.max(0,Math.min(100,Math.round(score))),context:c,factors:factorBreakdownFor(x,b,c)};''','''return {...b,score:Math.max(0,Math.min(100,Math.round(score))),context:c,factors:factorBreakdownFor(x,b,c)};''','GEX double count')

# Call/put mix is not directional without aggressor classification.
one('''const mix=Number.isFinite(cp)&&cp>=65?"Call-heavy":Number.isFinite(pp)&&pp>=65?"Put-heavy":"Balanced";return {label:`${mix} evidence`,score:Math.round(Math.min(100,score)),detail:`${x.coverage_confidence||"?"} coverage · ${high} high relevance · ${x.direction_available?"direction classified":"direction unconfirmed"}`}}''','''const mix=Number.isFinite(cp)&&cp>=65?"Call-contract concentration":Number.isFinite(pp)&&pp>=65?"Put-contract concentration":"Balanced contract mix";return {label:mix,score:Math.round(Math.min(100,score)),detail:`${Number.isFinite(cp)?cp.toFixed(0)+'% calls / '+pp.toFixed(0)+'% puts · ':''}${x.coverage_confidence||"?"} coverage · ${high} high relevance · ${x.direction_available?"direction classified":"direction unknown"}`}}''','flow wording')

# Swing-oriented chain load. GEX bucket remains independently selectable.
one('''const gw=document.getElementById("gexWindow")?.value||"0-30"; const r=await fetch(safeTickerEndpoint("/api/options",ticker)+`?gex_window=${encodeURIComponent(gw)}`,{headers:{"Accept":"application/json"}}),j=await r.json();''','''const gw=document.getElementById("gexWindow")?.value||"0-30"; const r=await fetch(safeTickerEndpoint("/api/options",ticker)+`?gex_window=${encodeURIComponent(gw)}&dte_min=7&dte_max=35`,{headers:{"Accept":"application/json"}}),j=await r.json();''','frontend DTE')

p.write_text(s)
print('v24.1 frontend migration applied')
