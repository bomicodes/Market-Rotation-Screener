from pathlib import Path
p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "25.28"' in s
s=s.replace('APP_VERSION = "25.28"','APP_VERSION = "25.29"',1)

# Backend news helpers + endpoint before health.
needle='\n@app.get("/health")\ndef health():\n'
insert=r'''

def _news_category(headline, summary=""):
    text=(str(headline or "")+" "+str(summary or "")).lower()
    tests=[
      ("earnings",("earnings","revenue","eps","guidance","quarter")),
      ("analyst",("upgrade","downgrade","price target","initiated","rating","analyst")),
      ("m&a",("acquire","acquisition","merger","takeover","buyout")),
      ("product",("launch","product","approval","fda","contract","partnership","deal")),
      ("legal/regulatory",("lawsuit","probe","investigation","sec ","doj","antitrust","regulator","recall")),
      ("macro",("fed ","federal reserve","rates","inflation","jobs","tariff","treasury","oil","china")),
    ]
    for cat,words in tests:
        if any(w in text for w in words): return cat
    return "company"

def _why_news_matters(category):
    return {
      "earnings":"Can reset near-term estimates, implied volatility, and the market's accepted valuation range.",
      "analyst":"Can change positioning and near-term expectations, especially when several firms revise targets together.",
      "m&a":"Can create abrupt repricing and alter sector sympathy/relative-strength behavior.",
      "product":"May change forward growth expectations or create a discrete catalyst that confirms or invalidates the setup.",
      "legal/regulatory":"Can introduce asymmetric headline risk that may overwhelm otherwise-clean technical signals.",
      "macro":"May affect the entire sector through rates, risk appetite, commodity inputs, or policy sensitivity.",
      "company":"Company-specific information that may explain unusual price/volume behavior and should be checked against the technical setup.",
    }.get(category,"Relevant context for the current setup.")

def _finnhub_company_news(ticker, days=4):
    if not FINNHUB_API_KEY:return []
    end=pd.Timestamp.now().normalize(); start=end-pd.Timedelta(days=days)
    url="https://finnhub.io/api/v1/company-news"
    try:
        r=requests.get(url,params={"symbol":ticker,"from":start.strftime("%Y-%m-%d"),"to":end.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY},timeout=15,headers={"User-Agent":"MarketRotationScreener/1.0"})
        r.raise_for_status(); rows=r.json() or []; out=[]
        for x in rows[:8]:
            h=str(x.get("headline") or "").strip()
            if not h:continue
            cat=_news_category(h,x.get("summary"))
            out.append({"ticker":ticker,"headline":h,"summary":str(x.get("summary") or "").strip(),"source":x.get("source") or "Finnhub","url":x.get("url"),"datetime":x.get("datetime"),"category":cat,"why":_why_news_matters(cat)})
        return out
    except Exception:return []

def _finnhub_market_news():
    if not FINNHUB_API_KEY:return []
    try:
        r=requests.get("https://finnhub.io/api/v1/news",params={"category":"general","token":FINNHUB_API_KEY},timeout=15,headers={"User-Agent":"MarketRotationScreener/1.0"})
        r.raise_for_status(); rows=r.json() or []; out=[]
        for x in rows[:8]:
            h=str(x.get("headline") or "").strip()
            if not h:continue
            out.append({"headline":h,"source":x.get("source") or "Finnhub","url":x.get("url"),"datetime":x.get("datetime")})
        return out
    except Exception:return []

@app.post("/api/news-context")
def api_news_context():
    try:
        body=request.get_json(silent=True) or {}; raw=body.get("symbols") or []
        symbols=[]
        for x in raw:
            t=str(x or "").upper().strip()
            if t and t not in symbols and len(t)<=20:symbols.append(t)
        symbols=symbols[:8]
        market=cached("news-market-v25-29",_finnhub_market_news,ttl=600)
        company={}
        with ThreadPoolExecutor(max_workers=min(4,max(1,len(symbols)))) as ex:
            futs={ex.submit(lambda t=t:cached(f"news-company-v25-29:{t}",lambda:_finnhub_company_news(t),ttl=600)):t for t in symbols}
            for f,t in [(f,t) for f,t in futs.items()]:
                try:company[t]=f.result()
                except Exception:company[t]=[]
        return jsonify({"ok":True,"market":market,"company":company,"symbols":symbols,"source":"Finnhub public news endpoints","deterministic":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500
'''
assert needle in s
s=s.replace(needle,insert+needle,1)

# CSS
cssneedle='.darkPoolContext{color:#7f97a8;line-height:1.5}'
cssadd=cssneedle+'\n.newsContextGrid{display:grid;grid-template-columns:1fr 1.25fr;gap:14px}.newsCol{min-width:0}.newsItem{padding:8px 0;border-bottom:1px solid #202936}.newsItem:last-child{border-bottom:none}.newsHeadline{font-size:12px;line-height:1.35;color:#e5e7eb}.newsMeta{font-size:10px;color:#64748b;margin-top:3px}.newsWhy{font-size:11px;color:#9fb0c2;margin-top:4px;line-height:1.35}.newsTicker{display:inline-block;font-size:10px;font-weight:800;color:#bfdbfe;border:1px solid #334155;border-radius:999px;padding:2px 6px;margin-right:5px}.newsCat{font-size:9px;color:#c4b5fd;text-transform:uppercase;letter-spacing:.4px}@media(max-width:800px){.newsContextGrid{grid-template-columns:1fr}}'
assert cssneedle in s
s=s.replace(cssneedle,cssadd,1)

# Dashboard panel inserted before Feed Comparison.
needle2='''  <div class="panel" id="feedCompPanel">'''
panel='''  <div class="panel" id="newsContextPanel">\n    <div class="dashTopline"><div><span class="dashTitle">NEWS + CATALYST CONTEXT</span><span class="note" style="margin-left:8px">Market headlines plus ticker-specific context for the strongest current candidates</span></div><div style="display:flex;align-items:center;gap:8px"><button class="secondary" id="runNewsContext" type="button">Refresh news</button><span id="newsContextStatus" class="note">Ready</span></div></div>\n    <div class="newsContextGrid" style="margin-top:10px">\n      <div class="newsCol"><strong>Market News</strong><div id="marketNewsRows" class="tiny" style="margin-top:5px">Refresh to load current headlines.</div></div>\n      <div class="newsCol"><strong>Why it matters for current setups</strong><div id="setupNewsRows" class="tiny" style="margin-top:5px">Uses the Top Setups / speculative candidate pool and deterministic catalyst labels — no AI-generated claims.</div></div>\n    </div>\n  </div>\n\n'''
assert needle2 in s
s=s.replace(needle2,panel+needle2,1)

# JS before live watchlist block.
jsneedle='\nconst LIVE_WATCHLIST_KEY="marketRotationLiveWatchlistV1";'
js=r'''
function newsTime(ts){
 if(!ts)return "";const d=new Date(Number(ts)*1000);if(Number.isNaN(d.getTime()))return "";
 return d.toLocaleString([], {month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});
}
function newsLink(x){const h=String(x?.headline||"");const safe=h.replace(/</g,"&lt;").replace(/>/g,"&gt;");return x?.url?`<a href="${x.url}" target="_blank" rel="noopener" class="newsHeadline">${safe}</a>`:`<span class="newsHeadline">${safe}</span>`}
function currentNewsSymbols(){
 const seen=[];
 const add=t=>{t=String(t||"").toUpperCase();if(t&&!seen.includes(t))seen.push(t)};
 (window.currentTopSetupRows||[]).forEach(x=>add(x?.x?.ticker||x?.ticker));
 (earlyTurnWatchData||[]).forEach(x=>add(x?.ticker));
 (institutionalRadarResults||[]).filter(x=>x.ok).forEach(x=>add(x?.ticker));
 (window.allSupportiveCandidates||[]).slice().sort((a,b)=>opportunityScore(b)-opportunityScore(a)).slice(0,8).forEach(x=>add(x?.ticker));
 return seen.slice(0,8);
}
async function runNewsContext(){
 const st=document.getElementById("newsContextStatus"),btn=document.getElementById("runNewsContext");if(btn)btn.disabled=true;if(st)st.textContent="Loading current news…";
 try{
   const symbols=currentNewsSymbols();
   const r=await fetch("/api/news-context",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({symbols})});
   const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||`News failed (${r.status})`);
   const m=document.getElementById("marketNewsRows");
   if(m)m.innerHTML=(j.market||[]).slice(0,6).map(x=>`<div class="newsItem">${newsLink(x)}<div class="newsMeta">${x.source||""}${newsTime(x.datetime)?" · "+newsTime(x.datetime):""}</div></div>`).join("")||`<div class="newsItem">No current market headlines returned by the configured news feed.</div>`;
   const c=document.getElementById("setupNewsRows"),blocks=[];
   (symbols||[]).forEach(t=>{const rows=(j.company?.[t]||[]).slice(0,2);rows.forEach(x=>blocks.push(`<div class="newsItem"><div><span class="newsTicker">${t}</span><span class="newsCat">${x.category||"company"}</span></div>${newsLink(x)}<div class="newsMeta">${x.source||""}${newsTime(x.datetime)?" · "+newsTime(x.datetime):""}</div><div class="newsWhy"><b>Why it matters:</b> ${x.why||"Relevant context for the current setup."}</div></div>`))});
   if(c)c.innerHTML=blocks.join("")||`<div class="newsItem">No ticker-specific headlines returned for the current candidate pool.</div>`;
   if(st)st.textContent=`${(j.market||[]).length} market headlines · ${blocks.length} setup headlines`;
 }catch(e){if(st)st.innerHTML=`<span class="error">${e.message}</span>`}finally{if(btn)btn.disabled=false}
}
setTimeout(()=>{const b=document.getElementById("runNewsContext");if(b&&!b.dataset.bound){b.dataset.bound="1";b.addEventListener("click",runNewsContext)}},0);
'''
assert jsneedle in s
s=s.replace(jsneedle,'\n'+js+jsneedle,1)
p.write_text(s)

r=Path('README.txt'); rs=r.read_text(); entry='''v25.29 — NEWS + CATALYST CONTEXT\n- Added a Dashboard News + Catalyst Context panel inspired by the requested market-news / why-it-matters layout.\n- Market News loads current general headlines from the existing Finnhub connection; ticker-specific news is fetched only for the strongest current Top Setup / Speculative Signal candidates.\n- Each ticker headline gets a deterministic catalyst category (earnings, analyst, M&A, product, legal/regulatory, macro, company) and a concise "Why it matters" explanation. No LLM calls and no speculative claim about causality or direction.\n- News is bounded (8 candidate tickers, 2 displayed headlines each), parallelized, and cached for 10 minutes to protect the small Render instance.\n\n'''
if not rs.startswith('v25.29'):r.write_text(entry+rs)
