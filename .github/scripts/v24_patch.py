from pathlib import Path

p=Path('app.py')
text=p.read_text()
text=text.replace('APP_VERSION = "23.6"','APP_VERSION = "24.0"',1)

backend=r'''

def _ctx_return(series, sessions):
    try:
        s=series.dropna()
        if len(s)<=sessions:return None
        return float((s.iloc[-1]/s.iloc[-1-sessions]-1)*100)
    except Exception:
        return None


def _context_earnings_catalyst(ticker):
    today=pd.Timestamp.now().normalize()
    try: dates=get_earnings_dates(ticker,16)
    except Exception: dates=[]
    future=[]
    for d in dates:
        try:
            x=pd.Timestamp(d).normalize()
            if x>=today: future.append(x)
        except Exception: pass
    if not future:return {"next_earnings":None,"days_to_earnings":None,"risk":"Unknown"}
    nxt=min(future); days=int((nxt-today).days)
    risk="Binary / imminent" if days<=3 else ("Near-term" if days<=10 else "Clear")
    return {"next_earnings":nxt.strftime("%Y-%m-%d"),"days_to_earnings":days,"risk":risk}


def _context_structure(ticker):
    df=dl_ohlc(ticker,"1y")
    if df is None or len(df)<55:return {"available":False}
    df=df.dropna(subset=["Open","High","Low","Close"]).copy()
    c=df["Close"].astype(float);h=df["High"].astype(float);l=df["Low"].astype(float);spot=float(c.iloc[-1])
    prev=c.shift(1);tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    atr=float(tr.rolling(14).mean().iloc[-1]);sma20=float(c.rolling(20).mean().iloc[-1]);sma50=float(c.rolling(50).mean().iloc[-1])
    hi20=float(h.iloc[-21:-1].max());lo20=float(l.iloc[-21:-1].min());hi10=float(h.iloc[-11:-1].max());lo10=float(l.iloc[-11:-1].min())
    direction="bullish" if spot>=sma20>=sma50 else ("bearish" if spot<=sma20<=sma50 else "neutral")
    if direction=="bullish":
        trigger=hi20;confirmation=trigger+.15*atr;invalidation=max(lo10,sma20-.35*atr);hard_fail=max(lo20,sma50-.5*atr);target1=max(trigger+1.5*atr,spot+1.25*atr);target2=max(trigger+3*atr,spot+2.5*atr);risk=max(.01,trigger-invalidation);reward=max(0,target2-trigger)
    elif direction=="bearish":
        trigger=lo20;confirmation=trigger-.15*atr;invalidation=min(hi10,sma20+.35*atr);hard_fail=min(hi20,sma50+.5*atr);target1=min(trigger-1.5*atr,spot-1.25*atr);target2=min(trigger-3*atr,spot-2.5*atr);risk=max(.01,invalidation-trigger);reward=max(0,trigger-target2)
    else:
        trigger=hi20;confirmation=hi20+.15*atr;invalidation=lo10;hard_fail=lo20;target1=hi20+1.5*atr;target2=hi20+3*atr;risk=max(.01,trigger-invalidation);reward=max(0,target2-trigger)
    r20=_ctx_return(c,20);r50=_ctx_return(c,50);trend_strength=int(spot>sma20)+int(sma20>sma50)+int(r20 is not None and r20>0)+int(r50 is not None and r50>0)
    return {"available":True,"spot":round(spot,2),"atr14":round(atr,2),"direction":direction,"trend_strength":trend_strength,"sma20":round(sma20,2),"sma50":round(sma50,2),"trigger":round(trigger,2),"confirmation":round(confirmation,2),"invalidation":round(invalidation,2),"hard_fail":round(hard_fail,2),"target1":round(target1,2),"target2":round(target2,2),"rr_to_target2":round(reward/risk,2) if risk else None,"return_20d":round(r20,2) if r20 is not None else None,"return_50d":round(r50,2) if r50 is not None else None}


def institutional_context_payload(ticker,parent=None):
    ticker=ticker.upper().strip();parent=(parent or "").upper().strip() or None
    universe=[ticker,"SPY"]+([parent] if parent and parent not in (ticker,"SPY") else [])
    px=dl_prices(universe,"1y");stock=px[ticker].dropna() if ticker in px else pd.Series(dtype=float);spy=px["SPY"].dropna() if "SPY" in px else pd.Series(dtype=float);par=px[parent].dropna() if parent and parent in px else pd.Series(dtype=float)
    rs={};positives=0;observed=0
    for n in (5,10,20):
        sr=_ctx_return(stock,n);mr=_ctx_return(spy,n);pr=_ctx_return(par,n) if len(par) else None;vm=(sr-mr) if sr is not None and mr is not None else None;vp=(sr-pr) if sr is not None and pr is not None else None
        rs[str(n)]={"stock":round(sr,2) if sr is not None else None,"vs_spy":round(vm,2) if vm is not None else None,"vs_parent":round(vp,2) if vp is not None else None}
        for v in (vm,vp):
            if v is not None: observed+=1;positives+=int(v>0)
    persistence=round(100*positives/observed) if observed else None
    triple=bool(parent and all((rs[str(n)].get("vs_spy") if rs[str(n)].get("vs_spy") is not None else -999)>0 and (rs[str(n)].get("vs_parent") if rs[str(n)].get("vs_parent") is not None else -999)>0 for n in (5,10,20)))
    structure=_context_structure(ticker);catalyst=_context_earnings_catalyst(ticker)
    horizon="1–3 week swing" if structure.get("trend_strength",0)>=4 and persistence is not None and persistence>=67 else ("2–5 day swing" if structure.get("trend_strength",0)>=2 else "Tactical / wait for confirmation")
    signature=f"v24|{parent or 'NONE'}|{horizon}|{'triple' if triple else 'mixed-rs'}|{structure.get('direction','neutral')}"
    try:save_setup_snapshot({"ticker":ticker,"spot":structure.get("spot"),"bias":structure.get("direction"),"score":persistence,"signature":signature,"raw":{"parent":parent,"relative_strength":rs,"structure":structure,"horizon":horizon,"catalyst":catalyst}})
    except Exception:pass
    try:
        hist=setup_history_stats(ticker,signature)
        if not hist.get("count"):
            hist=setup_history_stats(ticker);hist["fallback_all_signatures"]=True
    except Exception as e:hist={"count":0,"returns":{},"error":str(e)}
    return {"ticker":ticker,"parent":parent,"relative_strength":rs,"rotation_persistence":persistence,"triple_relative_strength":triple,"structure":structure,"horizon":horizon,"catalyst":catalyst,"signature":signature,"historical_expectancy":hist}

@app.get("/api/institutional-context/<ticker>")
def api_institutional_context(ticker):
    try:
        parent=(request.args.get("parent") or "").upper().strip() or None;key=f"institutional-v24:{ticker.upper()}:{parent or 'NONE'}";payload=cached(key,lambda:institutional_context_payload(ticker,parent),ttl=900);return jsonify({"ok":True,**payload})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500

'''
if 'def institutional_context_payload(' not in text:
    anchor='def auth_required():'
    if anchor not in text:raise SystemExit('backend anchor missing')
    text=text.replace(anchor,backend+anchor,1)

css=r'''
/* v24 Institutional Decision Layer */
.instDecisionPanel{border:1px solid #31506c;background:linear-gradient(180deg,#0d1720,#0a1118);margin:12px 0}.instDecisionHead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:10px}.instDecisionHead h3{margin:0;font-size:14px}.instDecisionHead .horizon{font-size:10px;font-weight:900;color:#7dd3fc;border:1px solid #24566e;border-radius:999px;padding:4px 8px}.instGrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.instCard{border:1px solid #26394b;background:#0b141d;border-radius:8px;padding:9px;min-height:76px}.instCard .k{font-size:8px;letter-spacing:.6px;color:#7f93a8;font-weight:900}.instCard .v{font-size:15px;font-weight:900;margin-top:5px}.instCard .d{font-size:9px;color:#9badbf;line-height:1.45;margin-top:4px}.instSection{margin-top:9px;border-top:1px solid #1d2d3b;padding-top:9px}.instSectionTitle{font-size:9px;font-weight:900;letter-spacing:.7px;color:#cbd5e1;margin-bottom:6px}.instLevelGrid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}.instLevel{padding:7px;border:1px solid #26394b;border-radius:7px;background:#09121a}.instLevel b{display:block;font-size:12px}.instLevel span{font-size:8px;color:#8193a6}.instFactors{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:5px;margin-top:8px}.instFactor{border:1px solid #2a3e50;border-radius:6px;padding:6px;font-size:8px}.instFactor strong{display:block;font-size:11px;margin-top:2px}.instGood{color:#69e6a6}.instWarn{color:#f6c667}.instBad{color:#fb8d96}.topSetupInstitutional{margin-top:8px;border-top:1px solid #203141;padding-top:7px}.topSetupInstGrid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}.topSetupInstMetric{font-size:8px;color:#8294a6}.topSetupInstMetric b{display:block;color:#dbe7f2;font-size:10px;margin-top:2px}@media(max-width:900px){.instGrid{grid-template-columns:repeat(2,1fr)}.instLevelGrid{grid-template-columns:repeat(3,1fr)}.instFactors{grid-template-columns:repeat(2,1fr)}.topSetupInstGrid{grid-template-columns:repeat(2,1fr)}}
'''
if 'v24 Institutional Decision Layer */' not in text:
    i=text.rfind('</style>')
    if i<0:raise SystemExit('style close missing')
    text=text[:i]+css+text[i:]

js=r'''
// -------------------- v24 Institutional Decision Layer --------------------
const institutionalContextMap={};let activeInstitutionalTicker=null;
function instFmt(v,d=1){const n=Number(v);return Number.isFinite(n)?`${n>0?"+":""}${n.toFixed(d)}%`:"—"}function instMoney(v){const n=Number(v);return Number.isFinite(n)?`$${n.toFixed(2)}`:"—"}
function ensureInstitutionalPanel(){let el=document.getElementById("institutionalDecisionPanel");if(el)return el;el=document.createElement("div");el.id="institutionalDecisionPanel";el.className="panel instDecisionPanel";el.innerHTML='<div class="note">Institutional decision layer loads with the selected ticker.</div>';const anchor=document.getElementById("stockDeepDiveAnchor")||document.getElementById("pricePreviewChart");if(anchor&&anchor.parentNode)anchor.parentNode.insertBefore(el,anchor);return el}
function flowEvidenceFor(ticker){const x=(activeFlowData?.ticker===ticker)?activeFlowData:null;if(!x)return {label:"Pending",score:null,detail:"Load options/flow"};const cp=Number(x.institutional_call_pct),pp=Number(x.institutional_put_pct),cov=Number(x.activity_coverage_pct),high=Number(x.high_relevance_events||0);let score=0;if(Number.isFinite(cov))score+=Math.min(45,cov*.45);score+=Math.min(35,high*7);if((x.institutional_events||0)>0)score+=20;const mix=Number.isFinite(cp)&&cp>=65?"Call-heavy":Number.isFinite(pp)&&pp>=65?"Put-heavy":"Balanced";return {label:`${mix} evidence`,score:Math.round(Math.min(100,score)),detail:`${x.coverage_confidence||"?"} coverage · ${high} high relevance · ${x.direction_available?"direction classified":"direction unconfirmed"}`}}
function gexImplicationFor(ticker,ctx){const o=(activeOptionsData?.ticker===ticker)?activeOptionsData:optionScanMap[ticker],p=o?.positioning;if(!p?.available)return {label:"Pending",detail:"Load GEX/options positioning"};const dir=ctx?.structure?.direction||"neutral",spot=Number(o?.spot||ctx?.structure?.spot),wall=dir==="bearish"?Number(p.put_wall):Number(p.call_wall),reg=String(p.gamma_regime||"");let room=null;if(Number.isFinite(spot)&&spot>0&&Number.isFinite(wall))room=dir==="bearish"?(spot-wall)/spot*100:(wall-spot)/spot*100;let label=reg.includes("Negative")?"Amplifying regime":reg.includes("Positive")?"Dampening regime":"Mixed gamma",detail=reg||"Dealer gamma unavailable";if(room!=null)detail+=` · ${room.toFixed(1)}% room to ${dir==="bearish"?"put":"call"} wall`;if(reg.includes("Positive")&&room!=null&&room<=1.5)detail+=" · breakout headwind";else if(reg.includes("Negative")&&room!=null&&room>2)detail+=" · continuation can accelerate";return {label,detail,room}}
function histExpectancyLabel(h){if(!h||!h.count)return {label:"Building sample",detail:"Snapshot database will accumulate this setup signature"};const r=h.returns?.["5"]||{};return {label:`${h.count} snapshots`,detail:`5D win ${r.win_rate==null?"—":r.win_rate+"%"} · median ${r.median==null?"—":r.median+"%"}`}}
function factorBreakdownFor(x,b,c){const rs=c?.relative_strength||{},p=c?.rotation_persistence,cat=c?.catalyst||{},hist=c?.historical_expectancy||{},flow=flowEvidenceFor(x.ticker),gx=gexImplicationFor(x.ticker,c),r5=rs["5"]||{},r20=rs["20"]||{};return [["Rotation",b.alignment==="FULL"?10:b.alignment==="EARLY"?9:5],["Market RS",r20.vs_spy>0?10:r5.vs_spy>0?7:3],["Sector RS",r20.vs_parent>0?10:r5.vs_parent>0?7:c?.parent?3:5],["Persistence",p==null?5:Math.round(p/10)],["Structure",c?.structure?.trend_strength==null?5:Math.min(10,c.structure.trend_strength*2.5)],["Flow",flow.score==null?5:Math.round(flow.score/10)],["GEX",gx.detail.includes("headwind")?3:gx.detail.includes("accelerate")?9:6],["Execution",optionScanMap[x.ticker]?.liquidity==="Liquid"?10:optionScanMap[x.ticker]?.liquidity==="Tradable"?8:4],["Expectancy",hist.count?7:5],["Catalyst",cat.days_to_earnings!=null&&cat.days_to_earnings<=3?1:cat.days_to_earnings!=null&&cat.days_to_earnings<=10?5:9]]}
async function loadInstitutionalContext(ticker,parent=null,quiet=false){ticker=normalizeStockTicker(ticker);if(!ticker)return null;activeInstitutionalTicker=ticker;const el=ensureInstitutionalPanel();if(el&&!quiet)el.innerHTML=`<div class="note">Loading ${ticker} institutional decision layer…</div>`;try{const q=parent?`?parent=${encodeURIComponent(parent)}`:"",r=await fetch(`/api/institutional-context/${encodeURIComponent(ticker)}${q}`),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Institutional context failed");institutionalContextMap[ticker]=j;if(activeInstitutionalTicker===ticker)renderInstitutionalContext(ticker);renderTopSetups();return j}catch(e){if(el&&!quiet)el.innerHTML=`<span class="warn">Institutional layer: ${e.message}</span>`;return null}}
function renderInstitutionalContext(ticker){const c=institutionalContextMap[ticker],el=ensureInstitutionalPanel();if(!c||!el)return;const rs=c.relative_strength||{},s=c.structure||{},cat=c.catalyst||{},flow=flowEvidenceFor(ticker),gx=gexImplicationFor(ticker,c),hist=histExpectancyLabel(c.historical_expectancy),r5=rs["5"]||{},r10=rs["10"]||{},r20=rs["20"]||{},ct=cat.next_earnings?`${cat.next_earnings} · ${cat.days_to_earnings}d`:'No confirmed upcoming earnings',cc=cat.days_to_earnings!=null&&cat.days_to_earnings<=3?'instBad':cat.days_to_earnings!=null&&cat.days_to_earnings<=10?'instWarn':'instGood';el.innerHTML=`<div class="instDecisionHead"><div><h3>${ticker} · Institutional Decision Layer</h3><div class="tiny">Observe → Rank → Explain → Execute → Invalidate → Measure</div></div><span class="horizon">${c.horizon||"—"}</span></div><div class="instGrid"><div class="instCard"><div class="k">ROTATION PERSISTENCE</div><div class="v ${Number(c.rotation_persistence)>=67?'instGood':''}">${c.rotation_persistence==null?'—':c.rotation_persistence+'/100'}</div><div class="d">5/10/20D relative leadership${c.triple_relative_strength?' · triple RS confirmed':''}</div></div><div class="instCard"><div class="k">RELATIVE STRENGTH</div><div class="v">${c.parent?`${instFmt(r20.vs_parent)} vs ${c.parent}`:instFmt(r20.vs_spy)}</div><div class="d">20D vs SPY ${instFmt(r20.vs_spy)} · 5D ${instFmt(r5.vs_spy)}</div></div><div class="instCard"><div class="k">FLOW EVIDENCE</div><div class="v">${flow.label}</div><div class="d">${flow.detail}</div></div><div class="instCard"><div class="k">GEX TRADE EFFECT</div><div class="v">${gx.label}</div><div class="d">${gx.detail}</div></div><div class="instCard"><div class="k">CATALYST RISK</div><div class="v ${cc}">${cat.risk||"Unknown"}</div><div class="d">${ct}</div></div><div class="instCard"><div class="k">HISTORICAL EXPECTANCY</div><div class="v">${hist.label}</div><div class="d">${hist.detail}</div></div><div class="instCard"><div class="k">PRICE STRUCTURE</div><div class="v">${s.direction||"—"}</div><div class="d">Trend ${s.trend_strength??'—'}/4 · ATR ${instMoney(s.atr14)}</div></div><div class="instCard"><div class="k">R:R TO TARGET 2</div><div class="v">${s.rr_to_target2==null?'—':Number(s.rr_to_target2).toFixed(1)+'×'}</div><div class="d">Structure/volatility heuristic</div></div></div><div class="instSection"><div class="instSectionTitle">RELATIVE LEADERSHIP</div><div class="instFactors"><div class="instFactor">5D vs SPY<strong>${instFmt(r5.vs_spy)}</strong></div><div class="instFactor">10D vs SPY<strong>${instFmt(r10.vs_spy)}</strong></div><div class="instFactor">20D vs SPY<strong>${instFmt(r20.vs_spy)}</strong></div><div class="instFactor">5D vs ${c.parent||'group'}<strong>${instFmt(r5.vs_parent)}</strong></div><div class="instFactor">20D vs ${c.parent||'group'}<strong>${instFmt(r20.vs_parent)}</strong></div></div></div><div class="instSection"><div class="instSectionTitle">STRUCTURE REVIEW · EXECUTION / INVALIDATION</div><div class="instLevelGrid"><div class="instLevel"><span>TRIGGER</span><b>${instMoney(s.trigger)}</b></div><div class="instLevel"><span>CONFIRMATION</span><b>${instMoney(s.confirmation)}</b></div><div class="instLevel"><span>INVALIDATION</span><b>${instMoney(s.invalidation)}</b></div><div class="instLevel"><span>HARD FAIL</span><b>${instMoney(s.hard_fail)}</b></div><div class="instLevel"><span>TARGET 1</span><b>${instMoney(s.target1)}</b></div><div class="instLevel"><span>TARGET 2</span><b>${instMoney(s.target2)}</b></div></div><div class="tiny" style="margin-top:6px">Confirmation is trigger ±0.15 ATR. Invalidation uses recent structure + 20D mean; hard fail uses broader 20D/50D structure. Decision heuristics, not stop instructions.</div></div>`}
const _topSetupEvaluationV23=topSetupEvaluation;topSetupEvaluation=function(x){const b=_topSetupEvaluationV23(x),c=institutionalContextMap[x.ticker];if(!c)return {...b,factors:factorBreakdownFor(x,b,null)};let score=b.score,r20=c.relative_strength?.["20"]||{};if(c.triple_relative_strength)score+=6;else{if(Number(r20.vs_spy)>0)score+=3;if(Number(r20.vs_parent)>0)score+=3}if(Number(c.rotation_persistence)>=80)score+=4;else if(Number(c.rotation_persistence)<50)score-=4;if(c.catalyst?.days_to_earnings!=null&&c.catalyst.days_to_earnings<=3)score-=10;const gx=gexImplicationFor(x.ticker,c);if(gx.detail.includes("headwind"))score-=4;else if(gx.detail.includes("accelerate"))score+=3;return {...b,score:Math.max(0,Math.min(100,Math.round(score))),context:c,factors:factorBreakdownFor(x,b,c)}};
const _renderTopSetupsV23=renderTopSetups;renderTopSetups=function(){_renderTopSetupsV23();const g=document.getElementById("topSetupsGrid");if(!g)return;(globalTopSetupData||[]).forEach(x=>{});g.querySelectorAll('[data-top-setup]').forEach(card=>{const ticker=card.dataset.topSetup,x=(globalTopSetupData||[]).find(z=>z.ticker===ticker);if(!x)return;const e=topSetupEvaluation(x),c=e.context;if(!c)return;const s=c.structure||{},old=card.querySelector('.topSetupInstitutional');if(old)old.remove();const d=document.createElement('div');d.className='topSetupInstitutional';d.innerHTML=`<div class="topSetupInstGrid">${(e.factors||[]).map(z=>`<div class="topSetupInstMetric">${z[0]}<b>${z[1]}/10</b></div>`).join('')}</div><div class="tiny" style="margin-top:6px">${c.horizon} · trigger ${instMoney(s.trigger)} · invalidation ${instMoney(s.invalidation)} · T2 ${instMoney(s.target2)} · R:R ${s.rr_to_target2??'—'}×${c.catalyst?.days_to_earnings!=null&&c.catalyst.days_to_earnings<=10?` · <span class="instWarn">earnings ${c.catalyst.days_to_earnings}d</span>`:''}</div>`;const actions=card.querySelector('.topSetupActions');card.insertBefore(d,actions||null);const score=card.querySelector('.topSetupScore');if(score)score.textContent=`${e.score}/100`})};
const _runAutomaticTopSetupsV23=runAutomaticTopSetups;runAutomaticTopSetups=async function(force=false){await _runAutomaticTopSetupsV23(force);const rows=(globalTopSetupData||[]).slice(0,10);for(let n=0;n<rows.length;n+=3)await Promise.all(rows.slice(n,n+3).map(x=>loadInstitutionalContext(x.ticker,x._parentTicker||null,true)));renderTopSetups()};
const _openSectorStockTickerV23=openSectorStockTicker;openSectorStockTicker=async function(rawTicker,opts={}){const ticker=normalizeStockTicker(rawTicker),parent=currentSector,out=await _openSectorStockTickerV23(rawTicker,opts);loadInstitutionalContext(ticker,parent,false);return out};const _openTopSetupDeepDiveV23=openTopSetupDeepDive;openTopSetupDeepDive=function(ticker,parentTicker=null,target="chart"){const out=_openTopSetupDeepDiveV23(ticker,parentTicker,target);loadInstitutionalContext(ticker,parentTicker||currentSector,false);return out};const _renderFlowV23=renderFlow;renderFlow=function(x){const out=_renderFlowV23(x);if(x?.ticker&&institutionalContextMap[x.ticker])renderInstitutionalContext(x.ticker);return out};const _renderOptionsPanelV23=renderOptionsPanel;renderOptionsPanel=function(){const out=_renderOptionsPanelV23();if(activeOptionsData?.ticker&&institutionalContextMap[activeOptionsData.ticker])renderInstitutionalContext(activeOptionsData.ticker);return out};
'''
if 'v24 Institutional Decision Layer --------------------' not in text:
    i=text.rfind('</script>')
    if i<0:raise SystemExit('script close missing')
    text=text[:i]+js+text[i:]

p.write_text(text)
print('patched app.py')
