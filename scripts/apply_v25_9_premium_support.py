from pathlib import Path

p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "25.8"' in s
s=s.replace('APP_VERSION = "25.8"','APP_VERSION = "25.9"',1)

marker='def modeled_dealer_positioning(rows, spot):\n'
assert marker in s
helpers='''def alpaca_option_daily_bars(symbols, lookback_days=55):
    """Historical OPRA/indicative daily option bars for a small candidate set."""
    symbols=[str(x).strip() for x in (symbols or []) if str(x).strip()][:20]
    if not symbols:return {}
    end=pd.Timestamp.now().normalize()+pd.Timedelta(days=1)
    start=end-pd.Timedelta(days=max(25,int(lookback_days or 55)))
    params={"symbols":",".join(symbols),"timeframe":"1Day","start":start.strftime("%Y-%m-%d"),"end":end.strftime("%Y-%m-%d"),"feed":ALPACA_OPTIONS_FEED,"limit":10000,"sort":"asc"}
    out={sym:[] for sym in symbols}; token=None
    for _ in range(4):
        if token:params["page_token"]=token
        elif "page_token" in params:params.pop("page_token",None)
        r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)
        if r.status_code in (401,403):raise RuntimeError(f"Alpaca {ALPACA_OPTIONS_FEED} historical option-bar access was rejected.")
        if r.status_code==429:
            time.sleep(.75);r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)
        r.raise_for_status();j=r.json() or {};bars=j.get("bars") or {}
        if isinstance(bars,dict):
            for sym,arr in bars.items():out.setdefault(sym,[]).extend(arr or [])
        token=j.get("next_page_token")
        if not token:break
    return out


def _premium_support_metrics(bars,current_mid=None):
    """Score premium support as a decaying option-specific zone, not stock support."""
    clean=[]
    for b in bars or []:
        o=_safe_float(b.get("o",b.get("open")));h=_safe_float(b.get("h",b.get("high")));l=_safe_float(b.get("l",b.get("low")));c=_safe_float(b.get("c",b.get("close")))
        if None in (h,l,c) or h<=0 or l<=0 or c<=0:continue
        clean.append({"o":o if o and o>0 else c,"h":h,"l":l,"c":c,"v":_safe_float(b.get("v",b.get("volume"))) or 0,"t":b.get("t",b.get("timestamp"))})
    clean=clean[-20:]
    if len(clean)<5:return {"available":False,"reason":"Need at least 5 daily premium bars."}
    lows=np.array([x["l"] for x in clean],dtype=float);highs=np.array([x["h"] for x in clean],dtype=float);closes=np.array([x["c"] for x in clean],dtype=float)
    floor=float(np.min(lows));q25=float(np.percentile(lows,25));support_hi=min(max(floor*1.12,q25),floor*1.35)
    px=_safe_float(current_mid) or float(closes[-1]);distance=(px/floor-1)*100 if floor>0 else None
    touches=int(np.sum(lows<=support_hi));prior_high=float(np.max(highs));expansion=(prior_high/px) if px>0 else None
    ranges=np.array([(x["h"]-x["l"])/max(x["c"],.01) for x in clean],dtype=float)
    recent=float(np.mean(ranges[-5:])) if len(ranges)>=5 else None;prior=float(np.mean(ranges[-15:-5])) if len(ranges)>=10 else None;compression=(recent/prior) if prior and prior>0 else None
    reversal=bool(len(closes)>=3 and closes[-1]>closes[-2] and closes[-2]>=closes[-3]*.92 and px>floor*1.05)
    score=0.0
    if distance is not None:score+=35 if distance<=10 else 30 if distance<=20 else 18 if distance<=35 else 8 if distance<=50 else 0
    score+=min(20.0,touches*4.0)
    if expansion is not None:score+=20 if expansion>=3 else 15 if expansion>=2 else 8 if expansion>=1.5 else 0
    if compression is not None:score+=15 if compression<=.75 else 10 if compression<=1.0 else 3 if compression<=1.2 else 0
    if reversal:score+=10
    score=max(0.0,min(100.0,score))
    if distance is not None and distance<=20 and touches>=2:state="REVERSAL CONFIRMED" if reversal else "AT SUPPORT"
    elif distance is not None and distance<=35 and touches>=2:state="NEAR SUPPORT"
    elif distance is not None and distance<=20:state="CHEAP / UNPROVEN"
    else:state="AWAY FROM SUPPORT"
    return {"available":True,"score":round(score,1),"state":state,"current_premium":round(px,4),"support_low":round(floor,4),"support_high":round(support_hi,4),"distance_from_support_pct":round(distance,1) if distance is not None else None,"support_touches":touches,"prior_20d_high":round(prior_high,4),"prior_expansion_multiple":round(expansion,2) if expansion is not None else None,"range_compression_ratio":round(compression,2) if compression is not None else None,"reversal_confirmed":reversal,"bars_used":len(clean),"last_bar_date":str(clean[-1].get("t") or "")[:10] or None}


def premium_support_payload(ticker,direction="bullish",options_payload=None):
    ticker=ticker.upper().strip();direction=str(direction or "bullish").lower();want_put=direction.startswith("bear")
    base=options_payload or options_quality_payload(ticker,"0-30",35,7);spot=_safe_float(base.get("spot"))
    if not spot:return {"ticker":ticker,"direction":direction,"available":False,"reason":"Spot unavailable."}
    candidates=[]
    for r in base.get("contracts") or []:
        typ=str(r.get("type") or "").lower();is_put=typ.startswith("p")
        if is_put!=want_put:continue
        strike=_safe_float(r.get("strike"));mid=_safe_float(r.get("mid"));bid=_safe_float(r.get("bid"));ask=_safe_float(r.get("ask"));spread=_safe_float(r.get("spread_pct"));delta=abs(_safe_float(r.get("delta")) or 0);oi=int(_safe_float(r.get("open_interest")) or 0);vol=int(_safe_float(r.get("volume")) or 0);dte=r.get("dte")
        if not strike or not mid or mid<=0 or not bid or bid<=0 or not ask or ask<=bid:continue
        if dte is None or dte<7 or dte>35:continue
        otm=((spot-strike)/spot*100) if want_put else ((strike-spot)/spot*100)
        if otm<=0 or otm>10:continue
        if spread is None or spread>22 or oi<75 or vol<10:continue
        if delta and not (.15<=delta<=.55):continue
        exec_score=(20 if spread<=8 else 16 if spread<=12 else 11)+(8 if oi>=500 else 5 if oi>=200 else 2)+(6 if vol>=100 else 3 if vol>=25 else 1)
        shape_score=(10 if 2<=otm<=7 else 6)+(8 if .22<=delta<=.45 else 4)+(6 if mid<=3 else 4 if mid<=5 else 1)
        candidates.append((exec_score+shape_score,dict(r,otm_pct=round(otm,2))))
    candidates.sort(key=lambda z:z[0],reverse=True);selected=[x[1] for x in candidates[:8]]
    if not selected:return {"ticker":ticker,"direction":direction,"available":False,"reason":"No liquid OTM candidate passed the premium-history prefilter."}
    histories=alpaca_option_daily_bars([r["symbol"] for r in selected],55);scored=[];rank_by_symbol={r["symbol"]:rank for rank,r in candidates}
    for r in selected:
        m=_premium_support_metrics(histories.get(r["symbol"]) or [],r.get("mid"))
        if not m.get("available"):continue
        execution_component=min(100.0,rank_by_symbol.get(r["symbol"],0)*2.0);combined=.78*float(m.get("score") or 0)+.22*execution_component
        rr=dict(r);rr.update(m);rr["premium_support_score"]=round(combined,1);scored.append(rr)
    if not scored:return {"ticker":ticker,"direction":direction,"available":False,"reason":"Historical premium bars were unavailable for the candidate contracts."}
    scored.sort(key=lambda r:(-r["premium_support_score"],r.get("distance_from_support_pct") if r.get("distance_from_support_pct") is not None else 999,r.get("mid") or 999))
    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:5],"contracts_screened":len(selected),"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}


'''
s=s.replace(marker,helpers+marker,1)

api_marker='@app.get("/api/options/<ticker>")\ndef api_options(ticker):\n'
assert api_marker in s
api='''@app.get("/api/premium-support/<ticker>")
def api_premium_support(ticker):
    try:
        direction=(request.args.get("direction") or "bullish").lower()
        if direction not in ("bullish","bearish"):direction="bullish"
        base,_,_=cached_refresh_safe(f"options-v24-1:{ticker.upper()}:0-30:7:35",lambda:options_quality_payload(ticker,"0-30",35,7),ttl=600)
        payload,stale,err=cached_refresh_safe(f"premium-support-v25-9:{ticker.upper()}:{direction}",lambda:premium_support_payload(ticker,direction,base),ttl=300)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500

'''
s=s.replace(api_marker,api+api_marker,1)

js_marker='function topSetupEvaluation(x){\n'
assert js_marker in s
s=s.replace(js_marker,'const premiumSupportMap=window.premiumSupportMap||(window.premiumSupportMap={});\n'+js_marker,1)
old=' const reasons=[],f=x.fast||x,t=x.trend||{},opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker];\n'
assert old in s
s=s.replace(old,' const reasons=[],f=x.fast||x,t=x.trend||{},opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker],premium=premiumSupportMap[x.ticker];\n',1)
anchor=' // Value acceptance.\n'
assert anchor in s
premium_score=''' // Contract-level premium support/compression: independent confirmation from the option itself.
 const pc=premium?.best_contract,ps=Number(pc?.premium_support_score);
 if(Number.isFinite(ps)){
   if(ps>=80){raw+=15;reasons.push([`Premium ${pc.state||"support"} · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=65){raw+=10;reasons.push([`Premium near support · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=50){raw+=5;reasons.push([`Premium base developing · ${ps.toFixed(0)}`,"warn"]);}
   else if(pc?.state==="AWAY FROM SUPPORT"){raw-=4;reasons.push(["Premium away from support","warn"]);}
 }

'''
s=s.replace(anchor,premium_score+anchor,1)
old=' return {score,reasons,va,stratPass,hardPass,alignment:align};\n'
assert old in s
s=s.replace(old,' return {score,reasons,va,stratPass,hardPass,alignment:align,premiumSupport:premium};\n',1)

auto_anchor='   globalTopSetupData=finalists;\n'
assert auto_anchor in s
hydrate='''   // Layer 5: analyze the option premium itself for final directional candidates.
   if(st)st.textContent=`Layer 5 · checking premium support on ${finalists.length} finalists`;
   for(let n=0;n<finalists.length;n+=3){
     const batch=finalists.slice(n,n+3);
     await Promise.all(batch.map(async x=>{
       const va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker];
       const direction=(va?.direction&&va.direction!=="neutral")?va.direction:((strat?.continuity==="bullish"||strat?.continuity==="bearish")?strat.continuity:null);
       if(!direction)return;
       try{
         const r=await fetch(`/api/premium-support/${encodeURIComponent(x.ticker)}?direction=${encodeURIComponent(direction)}`),j=await r.json();
         if(r.ok&&j.ok)premiumSupportMap[x.ticker]=j;
       }catch(e){console.warn("premium support",x.ticker,e)}
     }));
   }

'''
s=s.replace(auto_anchor,hydrate+auto_anchor,1)

card_anchor=' const va=e.va,complete=setupCompleteness(x,e),label=e.score>=80&&va?.strength==="CONFIRMED"&&e.stratPass&&complete.complete?"A+ SETUP":"A-QUALITY WATCH",alignmentLabel=e.alignment==="EARLY"?"EARLY ALIGNMENT":"FULL ALIGNMENT";\n'
assert card_anchor in s
card_new=card_anchor+' const pc=e.premiumSupport?.best_contract;\n const premiumHTML=pc?`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>${pc.expiration} $${Number(pc.strike).toFixed(0)} ${String(pc.type||"").toLowerCase().startsWith("p")?"P":"C"} · $${Number(pc.mid||0).toFixed(2)} · ${pc.state}</b><div class="tiny">support $${Number(pc.support_low).toFixed(2)}–$${Number(pc.support_high).toFixed(2)} · ${pc.distance_from_support_pct==null?"—":Number(pc.distance_from_support_pct).toFixed(1)+"%"} above floor · ${pc.support_touches||0} tests · prior high $${Number(pc.prior_20d_high||0).toFixed(2)} (${pc.prior_expansion_multiple==null?"—":Number(pc.prior_expansion_multiple).toFixed(1)+"×"}) · score ${Number(pc.premium_support_score||0).toFixed(0)}/100</div></div>`:"";\n'
s=s.replace(card_anchor,card_new,1)
html_anchor='</div><div class="topSetupTrigger">TRIGGER · <b>${trigger}</b>'
assert html_anchor in s
s=s.replace(html_anchor,'</div>${premiumHTML}<div class="topSetupTrigger">TRIGGER · <b>${trigger}</b>',1)

p.write_text(s)
r=Path('README.txt');rs=r.read_text();r.write_text('''v25.9 — PREMIUM SUPPORT / COMPRESSION SCANNER
- Added contract-level historical premium analysis for Top Setup finalists using Alpaca historical daily option bars. The scanner looks for liquid 7–35 DTE OTM calls/puts whose premium is near a repeatedly-tested 20-day support zone, with range compression and prior expansion potential.
- Premium Support Score combines distance to the premium floor, repeated support tests, range compression, reversal confirmation, prior-high expansion multiple, and execution quality. It is a confirmation layer rather than a hard gate because option premium support decays with theta/IV and is not equivalent to stock support.
- Top Setups runs premium history only on the final directional shortlist, reusing the already-cached options chain. Cards display contract, support zone, distance from support, tests, prior premium high/multiple, state, and score. New endpoint: GET /api/premium-support/<ticker>?direction=bullish|bearish.

'''+rs)

Path('tests/test_premium_support.py').write_text('''import app as appmod\n\ndef _bars(lows, closes=None, highs=None):\n    closes=closes or [x*1.08 for x in lows]\n    highs=highs or [max(l,c)*1.12 for l,c in zip(lows,closes)]\n    return [{"l":l,"c":c,"h":h,"o":c,"v":100,"t":f"2026-08-{i+1:02d}T20:00:00Z"} for i,(l,c,h) in enumerate(zip(lows,closes,highs))]\n\ndef test_repeated_floor_scores_as_support():\n    lows=[.70,.68,.72,.69,.71,.67,.70,.68,.69,.70,.68,.69]\n    closes=[.82,.78,.80,.76,.77,.74,.75,.73,.72,.74,.76,.82]\n    m=appmod._premium_support_metrics(_bars(lows,closes),.76)\n    assert m["available"]\n    assert m["support_touches"]>=2\n    assert m["distance_from_support_pct"]<20\n    assert m["score"]>=60\n\ndef test_far_from_floor_not_called_at_support():\n    lows=[.40,.42,.41,.43,.44,.45,.46,.47,.48,.50]\n    m=appmod._premium_support_metrics(_bars(lows),1.20)\n    assert m["available"]\n    assert m["state"]=="AWAY FROM SUPPORT"\n    assert m["distance_from_support_pct"]>50\n''')

# Semantic verification before CI picks it up.
assert 'APP_VERSION = "25.9"' in s
assert 'def premium_support_payload(' in s
assert '/api/premium-support/<ticker>' in s
assert 'premiumSupportMap' in s
assert 'Layer 5 · checking premium support' in s
print('v25.9 patch staged')
