from pathlib import Path
p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "26.2"','APP_VERSION = "26.3"',1)
# Upgrade the daily signal detector to recognize both reversal entries and legitimate continuation.
old="""function v262DailyReversalSignal(payload){
 const b=(payload?.bars||[]).filter(x=>Number.isFinite(Number(x.close))&&Number.isFinite(Number(x.open))&&Number.isFinite(Number(x.high))&&Number.isFinite(Number(x.low)));
 if(b.length<4)return null;
 const a=b[b.length-3],d1=b[b.length-2],d2=b[b.length-1];
 const aLow=Number(a.low),aHigh=Number(a.high),d1Low=Number(d1.low),d1Close=Number(d1.close),d1Open=Number(d1.open),d2Close=Number(d2.close),d2Open=Number(d2.open),d2High=Number(d2.high);
 // Failed 2-down: prior bar trades below the preceding low but closes back above it.
 const failed2d=d1Low<aLow && d1Close>aLow;
 const green1=d1Close>d1Open,green2=d2Close>d2Open;
 const followThrough=d2Close>d1Close || d2High>Number(d1.high);
 if(failed2d&&green1&&green2&&followThrough)return {kind:'FAILED 2D + 2 GREEN',score:5,detail:'Failed daily 2-down reclaimed the prior low, followed by two green daily bars'};
 if(failed2d&&green1)return {kind:'FAILED 2D REVERSAL',score:3,detail:'Daily 2-down failed and reclaimed the prior low'};
 return null;
}
"""
new="""function v262DailyReversalSignal(payload){
 const b=(payload?.bars||[]).filter(x=>Number.isFinite(Number(x.close))&&Number.isFinite(Number(x.open))&&Number.isFinite(Number(x.high))&&Number.isFinite(Number(x.low)));
 if(b.length<6)return null;
 const a=b[b.length-3],d1=b[b.length-2],d2=b[b.length-1];
 const aLow=Number(a.low),d1Low=Number(d1.low),d1Close=Number(d1.close),d1Open=Number(d1.open),d2Close=Number(d2.close),d2Open=Number(d2.open),d2High=Number(d2.high);
 const failed2d=d1Low<aLow && d1Close>aLow;
 const green1=d1Close>d1Open,green2=d2Close>d2Open;
 const followThrough=d2Close>d1Close || d2High>Number(d1.high);
 if(failed2d&&green1&&green2&&followThrough)return {kind:'FAILED 2D + 2 GREEN',phase:'EARLY',score:6,detail:'Failed daily 2-down reclaimed the prior low, followed by two green daily bars'};
 if(failed2d&&green1)return {kind:'FAILED 2D REVERSAL',phase:'EARLY',score:4,detail:'Daily 2-down failed and reclaimed the prior low'};
 // Do not throw out a move just because it already started. A clean multi-day
 // breakout with closes near the highs is a continuation candidate, not a late miss.
 const prior=b.slice(-7,-2),priorHigh=Math.max(...prior.map(x=>Number(x.high))),priorCloseHigh=Math.max(...prior.map(x=>Number(x.close)));
 const range2=Math.max(.01,Number(d2.high)-Number(d2.low)),closeNearHigh=(Number(d2.high)-d2Close)/range2<=.30;
 const rising=green1&&green2&&d2Close>d1Close;
 const breakout=rising&&d2Close>priorCloseHigh&&d2High>=priorHigh;
 const vols=b.slice(-12).map(x=>Number(x.volume)).filter(Number.isFinite),curVol=Number(d2.volume),priorVols=vols.slice(0,-1),avgVol=priorVols.length?priorVols.reduce((a,v)=>a+v,0)/priorVols.length:null;
 const volExpansion=avgVol&&Number.isFinite(curVol)?curVol>=avgVol*1.15:false;
 if(breakout&&closeNearHigh)return {kind:volExpansion?'VOLUME CONTINUATION':'CONTINUATION BREAKOUT',phase:'CONTINUATION',score:volExpansion?6:5,detail:`Two green daily bars with a fresh breakout${volExpansion?' on expanding volume':''}`};
 if(rising&&closeNearHigh)return {kind:'TREND CONTINUATION',phase:'CONTINUATION',score:3,detail:'Two green daily bars with higher close and strong close location'};
 return null;
}
"""
assert old in s
s=s.replace(old,new,1)
# Replace blanket extension penalty with a continuation-aware rule.
old2=''' // Extension penalty: favor the setup before the obvious move.\n const mom=Number(f.rs_momentum??f.momentum);\n if(Number.isFinite(mom)&&mom>105){raw-=7;reasons.push(["Extended","warn"]);}\n'''
new2=''' // Extension is a caution, not an automatic rejection. If price is in a\n // validated continuation phase, preserve the setup and let execution/entry\n // quality decide whether to chase, wait for a base, or enter on a hold.\n const mom=Number(f.rs_momentum??f.momentum),phase=x?._earlyPriceSignal?.phase;\n if(Number.isFinite(mom)&&mom>105){\n   if(phase==="CONTINUATION"){raw-=2;reasons.push(["Continuation already underway","good"]);}\n   else {raw-=7;reasons.push(["Extended · wait for entry","warn"]);}\n }\n'''
assert old2 in s
s=s.replace(old2,new2,1)
# Insert phase classifier before runAutomaticTopSetups.
needle='async function runAutomaticTopSetups(force=false){'
helper=r'''function v263OpportunityLane(x){
 const sig=x?._earlyPriceSignal||null,va=valueAcceptanceMap[x?.ticker],st=stratSignalMap[x?.ticker];
 const f=x?.fast||x||{},t=x?.trend||{};
 const fIn=(f?.tail_trajectory?f.tail_trajectory==='Rotating In':(f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory?t.tail_trajectory==='Rotating In':(t?.rs_up===true&&t?.mom_up===true));
 if(sig?.phase==='EARLY')return {lane:'EARLY',detail:sig.kind};
 if(sig?.phase==='CONTINUATION')return {lane:'CONTINUATION',detail:sig.kind};
 const confirmed=(va?.strength==='CONFIRMED')&&(st?.continuity==='bullish'||st?.continuity==='bearish')&&(fIn||tIn);
 if(confirmed)return {lane:'CONFIRMED',detail:'Value + STRAT + rotation aligned'};
 return {lane:'DEVELOPING',detail:'Setup building'};
}
function v263LaneRank(x){return ({EARLY:4,CONTINUATION:3,CONFIRMED:2,DEVELOPING:1}[v263OpportunityLane(x).lane]||0)}
'''
assert needle in s
s=s.replace(needle,helper+needle,1)
# Give phase a modest finalist tiebreaker without overpowering underlying quality.
old3='return (v262EarlyMoveScore(b)+bq)-(v262EarlyMoveScore(a)+aq);'
new3='return (v262EarlyMoveScore(b)+bq+v263LaneRank(b)*.6)-(v262EarlyMoveScore(a)+aq+v263LaneRank(a)*.6);'
assert old3 in s
s=s.replace(old3,new3,1)
# Add final visual lane label after all existing render wrappers, just before closing script.
needle4='</script>\n"""\n@app.errorhandler(500)'
addon=r'''// v26.3: temporal opportunity lanes. Early and continuation are both valid;
// confirmed is not treated as inherently superior if the entry has already run.
const _renderTopSetupsV263=renderTopSetups;
renderTopSetups=function(){
 const out=_renderTopSetupsV263(),g=document.getElementById('topSetupsGrid');if(!g)return out;
 g.querySelectorAll('[data-top-setup]').forEach(card=>{
   const x=(globalTopSetupData||[]).find(z=>z.ticker===card.dataset.topSetup);if(!x)return;
   const lane=v263OpportunityLane(x),head=card.querySelector('.topSetupStatus');
   if(head&&!head.dataset.v263Lane){head.dataset.v263Lane='1';head.innerHTML=`<span class="v263Lane v263${lane.lane}">${lane.lane}</span> · ${head.innerHTML}`;}
   const strip=card.querySelector('.v26DecisionStrip');if(strip){
     let note=strip.querySelector('.v263PhaseNote');if(!note){note=document.createElement('div');note.className='v263PhaseNote tiny';strip.appendChild(note)}
     note.textContent=lane.lane==='EARLY'?`EARLY · ${lane.detail} · higher timing edge, lower confirmation`:lane.lane==='CONTINUATION'?`CONTINUATION · ${lane.detail} · move has started; judge entry quality, not just extension`:lane.lane==='CONFIRMED'?`CONFIRMED · ${lane.detail} · strongest validation, may require pullback/base if extended`:`DEVELOPING · ${lane.detail}`;
   }
 });
 return out;
};
'''
assert needle4 in s
s=s.replace(needle4,addon+'\n'+needle4,1)
# CSS for compact lane chips, inserted before closing style if recognizable.
css='''.v263Lane{display:inline-block;padding:2px 6px;border-radius:999px;font-size:9px;font-weight:900;letter-spacing:.5px;border:1px solid #36536a}.v263EARLY{color:#67e8f9;border-color:#157a8a;background:#08262c}.v263CONTINUATION{color:#fbbf24;border-color:#8a6215;background:#2a1e05}.v263CONFIRMED{color:#6ee7a7;border-color:#167146;background:#082419}.v263DEVELOPING{color:#a8b4c2}.v263PhaseNote{margin-top:8px;color:#9cb0c2}\n'''
# append near existing v26 styles by replacing first script opener after HTML CSS closure would be risky; inject inline style before addon.
styleaddon="const v263Style=document.createElement('style');v263Style.textContent="+repr(css)+";document.head.appendChild(v263Style);\n"
s=s.replace(addon,styleaddon+addon,1)
p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
r.write_text('''v26.3 — TEMPORAL OPPORTUNITY LANES + CONTINUATION PRESERVATION\n- Top Setups now distinguishes EARLY, CONTINUATION, CONFIRMED, and DEVELOPING opportunities instead of treating maximum confirmation as automatically best.\n- EARLY includes failed daily 2-down/reclaim patterns such as the SLB/NOW examples, with extra weight for the second green follow-through day.\n- CONTINUATION detects two-green-day higher-close breakouts, close-near-high behavior, and optional volume expansion so an already-started move is not discarded simply for being underway.\n- The old blanket momentum-extension penalty is reduced for validated continuation plays; extension remains an execution warning rather than a reason to erase a strong continuation setup.\n- Cards now explicitly explain whether the timing edge is early, continuation, or confirmed so the trader can choose between catching the turn and joining a healthy move.\n\n'''+rs)
