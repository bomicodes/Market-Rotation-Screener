from pathlib import Path
p=Path('app.py'); s=p.read_text()
def rep(a,b,n):
    global s
    if a not in s: raise SystemExit('missing '+n)
    s=s.replace(a,b,1)
if 'const GLOSSARY={' not in s:
    js=r'''<div id="glossTooltip" class="glossTooltip"></div>
<script>
const GLOSSARY={
 RRG:"Relative Rotation Graph — plots a stock or sector's relative strength (RS-Ratio) against the momentum of that strength (RS-Momentum) to show whether it's leading, weakening, lagging, or improving versus a benchmark.",
 GEX:"Gamma Exposure — a model of how options dealers are positioned. Positive/dampening gamma tends to pin price near current levels; negative/amplifying gamma tends to accelerate moves.",
 VAH:"Value Area High — the top of the price range where roughly 68% of a session's volume traded. A close above VAH suggests buyers are willing to pay outside the prior 'fair value' zone.",
 POC:"Point of Control — the single price level with the most traded volume in a session; often acts as a magnet or pivot.",
 VAL:"Value Area Low — the bottom of the price range where roughly 68% of a session's volume traded. A close below VAL suggests sellers are pushing outside the prior 'fair value' zone.",
 STRAT:"A price-action framework classifying each bar as Inside (1), Directional (2U/2D), or Outside (3) relative to the prior bar, used here across 1H/4H/1D/1W to gauge multi-timeframe agreement.",
 FTC:"Full Timeframe Continuity — how many of the 1H/4H/1D/1W timeframes are currently in an aligned directional STRAT scenario (not just a green/red candle).",
 IV:"Implied Volatility — the options market's forward-looking estimate of how much a stock will move, baked into an option's price.",
 DTE:"Days To Expiration — how many calendar days remain until an option contract expires.",
};
function glossTerm(label,key){const k=key||label;if(!GLOSSARY[k])return label;return `<span class="glossTerm" data-gloss="${k}">${label}</span>`;}
document.addEventListener("click",function(e){const el=e.target.closest(".glossTerm"),tip=document.getElementById("glossTooltip");if(!tip)return;if(!el){tip.classList.remove("show");return;}const def=GLOSSARY[el.dataset.gloss];if(!def){tip.classList.remove("show");return;}tip.innerHTML=`<b>${el.dataset.gloss}</b>${def}`;const r=el.getBoundingClientRect();tip.style.top=Math.min(window.innerHeight-20,r.bottom+8)+"px";tip.style.left=Math.max(8,Math.min(window.innerWidth-296,r.left))+"px";tip.classList.add("show");e.stopPropagation();});
const SOURCE_LABELS={yfinance:"Yahoo (prices)",alpaca_stocks:"Alpaca (stocks)",alpaca_options:"Alpaca (options)",finnhub:"Finnhub",unusual_whales:"Unusual Whales",nasdaq_yahoo_calendar:"Earnings calendar"};
function timeAgo(iso){if(!iso)return null;const x=Math.max(0,(Date.now()-new Date(iso).getTime())/1000);if(x<60)return "just now";if(x<3600)return Math.round(x/60)+"m ago";if(x<86400)return Math.round(x/3600)+"h ago";return Math.round(x/86400)+"d ago";}
async function refreshSourceHealth(){const el=document.getElementById("sourceHealthStrip");if(!el)return;try{const r=await fetch("/api/source-health"),j=await r.json();if(!j?.ok||!Array.isArray(j.sources))return;el.innerHTML=j.sources.map(x=>{const label=SOURCE_LABELS[x.name]||x.name;const detail=x.status==="ok"?`Last success ${timeAgo(x.last_success)||"—"}`:x.status==="degraded"?`Falling back — last success ${timeAgo(x.last_success)||"never this session"}, last error ${timeAgo(x.last_error)}`:"Not called yet this session";return `<span class="src" title="${detail.replace(/"/g,'&quot;')}"><span class="dot ${x.status}"></span>${label}</span>`;}).join("");}catch(e){}}
document.addEventListener("DOMContentLoaded",refreshSourceHealth);setInterval(refreshSourceHealth,5*60*1000);
async function refreshMacroCalendar(){const el=document.getElementById("dashboardMacro");if(!el)return;try{const r=await fetch("/api/macro-calendar?within_days=90"),j=await r.json();if(!j?.ok||!Array.isArray(j.events))return;if(!j.events.length){el.innerHTML=`<div class="note">No confirmed FOMC/CPI/jobs dates in the next 90 days.</div>`;return;}el.innerHTML=j.events.map(x=>`<div class="breadthRow"><div class="name">${x.label}</div><div class="val ${x.days_away<=3?"neg":""}">${x.date}</div><div class="move">${x.days_away}d</div></div>`).join("");}catch(e){}}
document.addEventListener("DOMContentLoaded",refreshMacroCalendar);setInterval(refreshMacroCalendar,60*60*1000);
'''
    rep('<script>\nfunction fmtCompact(n){',js+'\nfunction fmtCompact(n){','glossary js')
if 'async function syncWatchlistFromServer()' not in s:
    rep(''' }catch(e){liveWatchlist=[]}
}

function saveLiveWatchlist(){''',''' }catch(e){liveWatchlist=[]}
 syncWatchlistFromServer();
}

async function syncWatchlistFromServer(){
 try{
   const r=await fetch("/api/watchlist"),j=await r.json();
   if(!j?.ok||!Array.isArray(j.items))return;
   const known=new Set(liveWatchlist.map(x=>liveWatchKey(x.ticker)));let changed=false;
   j.items.forEach(row=>{const key=liveWatchKey(row.ticker);if(!known.has(key)){liveWatchlist.push({ticker:row.ticker,added_price:row.added_price});known.add(key);changed=true;}});
   if(changed){try{localStorage.setItem(LIVE_WATCHLIST_KEY,JSON.stringify(liveWatchlist))}catch(e){} renderLiveWatchlist();refreshLiveBookmarkButtons();}
 }catch(e){}
}

function saveLiveWatchlist(){''','watch sync')
    rep(''' if(i>=0)liveWatchlist.splice(i,1);
 else liveWatchlist.unshift(item);
 saveLiveWatchlist();''',''' if(i>=0){liveWatchlist.splice(i,1);fetch(`/api/watchlist/${encodeURIComponent(item.ticker)}`,{method:"DELETE"}).catch(()=>{});}
 else{liveWatchlist.unshift(item);fetch("/api/watchlist",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticker:item.ticker,added_price:item.added_price??null})}).catch(()=>{});}
 saveLiveWatchlist();''','watch toggle')
if 'BULLISH ${glossTerm("FTC")}' not in s:
    rep(' cont.textContent=data?.continuity==="bullish"?`${data.bullish_count}/4 BULLISH FTC`:data?.continuity==="bearish"?`${data.bearish_count}/4 BEARISH FTC`:`${data?.bullish_count||0}↑ / ${data?.bearish_count||0}↓ MIXED`;', ' cont.innerHTML=data?.continuity==="bullish"?`${data.bullish_count}/4 BULLISH ${glossTerm("FTC")}`:data?.continuity==="bearish"?`${data.bearish_count}/4 BEARISH ${glossTerm("FTC")}`:`${data?.bullish_count||0}↑ / ${data?.bearish_count||0}↓ MIXED`;','ftc')
p.write_text(s)
