from pathlib import Path
p=Path('app.py'); s=p.read_text()
s=s.replace('APP_VERSION = "25.23"','APP_VERSION = "25.24"',1)
backend=r'''

def _institutional_trade_sample(ticker):
    """Sample recent SIP prints without pretending sampled tape is complete dark-pool data."""
    ticker=str(ticker or "").upper().strip()
    if not ticker or not ALPACA_API_KEY or not ALPACA_API_SECRET:
        return {"ticker":ticker,"ok":False,"error":"Alpaca SIP is not configured."}
    from zoneinfo import ZoneInfo
    et=ZoneInfo("America/New_York"); now=datetime.now(et)
    days=[]; d=now.date()
    while len(days)<4:
        if d.weekday()<5: days.append(d)
        d-=timedelta(days=1)
    samples=[]
    for day in days:
        start=datetime.combine(day,datetime.min.time(),tzinfo=et).replace(hour=9,minute=30)
        end=datetime.combine(day,datetime.min.time(),tzinfo=et).replace(hour=16)
        if day==now.date(): end=min(end,now)
        if end<=start: continue
        url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/trades"
        params={"start":start.isoformat(),"end":end.isoformat(),"feed":ALPACA_STOCK_FEED,
                "sort":"desc","limit":5000}
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=18)
        if r.status_code in (401,403):
            return {"ticker":ticker,"ok":False,"error":f"Alpaca {ALPACA_STOCK_FEED.upper()} trade access rejected ({r.status_code})."}
        r.raise_for_status(); rows=(r.json() or {}).get("trades") or []
        prints=[]
        for x in rows:
            try:
                price=float(x.get("p") or 0); size=float(x.get("s") or 0); notional=price*size
                if price>0 and size>0: prints.append((notional,x))
            except Exception: pass
        prints.sort(key=lambda z:z[0],reverse=True)
        largest=prints[0][0] if prints else 0
        samples.append({"date":str(day),"largest":largest,"count":len(rows),"top":prints[:8]})
    if not samples or not samples[0]["count"]:
        return {"ticker":ticker,"ok":False,"error":"No recent trade sample returned."}
    cur=samples[0]; prior=[x["largest"] for x in samples[1:] if x["largest"]>0]
    baseline=float(np.median(prior)) if prior else cur["largest"]
    multiple=(cur["largest"]/baseline) if baseline>0 else 1.0
    threshold=max(100000.0,baseline*.35)
    large=[z for z in cur["top"] if z[0]>=threshold]
    large_notional=sum(z[0] for z in large)
    repeated=sum(1 for x in samples if x["largest"]>=max(100000.0,baseline*.8))
    top=[]
    for n,x in cur["top"][:5]:
        top.append({"notional":round(n,2),"price":_safe_float(x.get("p")),"size":int(x.get("s") or 0),
                    "exchange":x.get("x"),"conditions":x.get("c") or [],"time":x.get("t")})
    activity=min(10.0,2.5+min(3.5,max(0,multiple-1)*2.0)+min(2.0,repeated*.55)+min(2.0,large_notional/max(1,baseline)*.55))
    return {"ticker":ticker,"ok":True,"largest_print":round(cur["largest"],2),"baseline_largest":round(baseline,2),
            "largest_multiple":round(multiple,2),"large_print_notional":round(large_notional,2),"repeat_days":repeated,
            "sampled_trades":cur["count"],"activity_score":round(activity,1),"top_prints":top,
            "source":f"Alpaca {ALPACA_STOCK_FEED.upper()} sampled trades","sampled":True}

@app.post("/api/institutional-radar")
def institutional_radar():
    try:
        body=request.get_json(silent=True) or {}; raw=body.get("symbols") or []
        meta=body.get("meta") or {}
        symbols=[]
        for x in raw:
            t=str(x or "").upper().strip()
            if t and t not in symbols and len(t)<=20: symbols.append(t)
        symbols=symbols[:12]
        if not symbols:return jsonify({"ok":False,"error":"No symbols supplied."}),400
        def one(t):
            return cached(f"institutional-radar-v25-24:{ALPACA_STOCK_FEED}:{t}",lambda:_institutional_trade_sample(t),ttl=600)
        rows=[]
        with ThreadPoolExecutor(max_workers=min(4,len(symbols))) as ex:
            futs={ex.submit(one,t):t for t in symbols}
            for f in as_completed(futs):
                t=futs[f]
                try:r=f.result()
                except Exception as e:r={"ticker":t,"ok":False,"error":str(e)}
                m=meta.get(t) or {}
                if r.get("ok"):
                    rotation=float(m.get("opportunity") or 0); stage=float(m.get("stage") or 0)
                    composite=min(10.0,.72*float(r.get("activity_score") or 0)+.20*rotation+.08*(stage/4*10))
                    r["rotation"]=m; r["composite_score"]=round(composite,1)
                rows.append(r)
        rows.sort(key=lambda x:(x.get("ok") is True,float(x.get("composite_score") or 0)),reverse=True)
        return jsonify({"ok":True,"results":rows,"feed":ALPACA_STOCK_FEED,"disclosure":"Large-print activity uses a bounded sample of Alpaca equity trades. It is not a complete tape and is not labeled dark-pool flow; exchange/condition codes are preserved when available."})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500
'''
anchor='\n@app.get("/health")\ndef health():'
assert anchor in s
s=s.replace(anchor,backend+anchor,1)
css=r'''
.institutionalScore{font-size:17px;font-weight:900;color:#93c5fd}.instRadarHead{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.instRadarHead .status{margin-left:auto}.instPrint{font-weight:800;color:#e2e8f0}.instHot{color:#86efac}.instWarm{color:#fde68a}.instMuted{color:#94a3b8}
'''
s=s.replace('</style>',css+'\n</style>',1)
panel=r'''
  <div class="panel" id="institutionalRadarPanel">
    <div class="instRadarHead">
      <div><strong>◉ Institutional Activity Top Setups</strong><div class="note">Separate from the rotation Top Setups · ranks unusual sampled equity prints with RRG/rotation confirmation.</div></div>
      <button class="primary" id="runInstitutionalRadar">Scan institutional activity</button>
      <span id="institutionalRadarStatus" class="status">Ready</span>
    </div>
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr><th>#</th><th>Ticker</th><th>Activity setup</th><th>Largest sampled print</th><th>Persistence</th><th>Rotation confirmation</th><th>Why it surfaced</th></tr></thead>
      <tbody id="institutionalRadarRows"><tr><td colspan="7" class="note">Run the scan after loading an ETF/group. The 12 strongest current RRG candidates are checked independently from the normal Top Setups.</td></tr></tbody>
    </table></div>
    <div id="institutionalRadarDisclosure" class="tiny" style="margin-top:8px">Uses bounded Alpaca SIP trade samples. This is large-print activity, not a claim of dark-pool direction or buyer/seller intent.</div>
  </div>

'''
anchor2='  <div class="panel" id="watchlistPanel">'
assert anchor2 in s
s=s.replace(anchor2,panel+anchor2,1)
js=r'''
let institutionalRadarResults=[];
function instMoney(v){v=Number(v||0);if(v>=1e9)return "$"+(v/1e9).toFixed(1)+"B";if(v>=1e6)return "$"+(v/1e6).toFixed(1)+"M";if(v>=1e3)return "$"+(v/1e3).toFixed(0)+"K";return "$"+v.toFixed(0)}
function renderInstitutionalRadar(){
 const body=document.getElementById("institutionalRadarRows");if(!body)return;
 const good=institutionalRadarResults.filter(x=>x.ok).slice(0,6);
 if(!good.length){body.innerHTML=`<tr><td colspan="7" class="note">No qualifying activity returned from this scan.</td></tr>`;return}
 body.innerHTML=good.map((x,i)=>{
   const m=x.rotation||{}, mult=Number(x.largest_multiple||0), cls=mult>=2?"instHot":mult>=1.25?"instWarm":"instMuted";
   const why=[];if(mult>=1.5)why.push(`${mult.toFixed(1)}× sampled baseline`);if((x.repeat_days||0)>=2)why.push(`${x.repeat_days} active sessions`);if((m.stage||0)>=3)why.push("confirmed rotation");if((m.tail||"")==="Rotating In")why.push("tail rotating in");
   return `<tr class="clickrow" data-inst-open="${x.ticker}"><td>${i+1}</td><td><b>${x.ticker}</b><div class="tiny">${m.etf||""}</div></td><td><span class="institutionalScore">${Number(x.composite_score||0).toFixed(1)}/10</span><div class="tiny">activity ${Number(x.activity_score||0).toFixed(1)}/10</div></td><td><span class="instPrint ${cls}">${instMoney(x.largest_print)}</span><div class="tiny">${mult.toFixed(1)}× prior sampled largest · ${x.sampled_trades||0} trades sampled</div></td><td>${x.repeat_days||0}/4 sessions<div class="tiny">large-print persistence</div></td><td>${m.stage||0}/4 · ${m.quadrant||"—"}<div class="tiny">${m.tail||"—"} · opportunity ${m.opportunity||0}/10</div></td><td>${why.join(" · ")||"Large-print activity under review"}<div class="tiny">Click to open chart/options</div></td></tr>`;
 }).join("");
 body.querySelectorAll("[data-inst-open]").forEach(row=>row.addEventListener("click",()=>openSectorStockTicker(row.dataset.instOpen,{scroll:true})));
}
async function runInstitutionalRadar(){
 const st=document.getElementById("institutionalRadarStatus"),btn=document.getElementById("runInstitutionalRadar");
 const rows=(liveStockData||[]).slice().sort((a,b)=>opportunityScore(b)-opportunityScore(a)).slice(0,12);
 if(!rows.length){if(st)st.textContent="Load an ETF/group first.";return}
 const symbols=rows.map(x=>x.ticker),meta={};rows.forEach(x=>{const rs=rotationStage(x);meta[x.ticker]={etf:currentSector||"",quadrant:x.fast?.quadrant||x.quadrant||"",tail:effectiveTailSignal(x)||x.tail_trajectory||"",stage:rs.level||0,opportunity:opportunityScore(x)}});
 if(btn)btn.disabled=true;if(st)st.textContent=`Scanning ${symbols.length} candidates…`;
 try{
   const r=await fetch("/api/institutional-radar",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({symbols,meta})});
   const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||`Scan failed (${r.status})`);
   institutionalRadarResults=j.results||[];renderInstitutionalRadar();
   const ok=institutionalRadarResults.filter(x=>x.ok).length;if(st)st.textContent=`${ok}/${symbols.length} candidates analyzed · separate activity ranking`;
   const d=document.getElementById("institutionalRadarDisclosure");if(d&&j.disclosure)d.textContent=j.disclosure;
 }catch(e){if(st)st.innerHTML=`<span class="error">${e.message}</span>`}finally{if(btn)btn.disabled=false}
}
setTimeout(()=>{const b=document.getElementById("runInstitutionalRadar");if(b&&!b.dataset.bound){b.dataset.bound="1";b.addEventListener("click",runInstitutionalRadar)}},0);

'''
anchor3='const LIVE_WATCHLIST_KEY="marketRotationLiveWatchlistV1";'
assert anchor3 in s
s=s.replace(anchor3,js+anchor3,1)
p.write_text(s)
