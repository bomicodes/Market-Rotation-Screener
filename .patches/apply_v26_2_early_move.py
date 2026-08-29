from pathlib import Path
p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "26.0"','APP_VERSION = "26.2"')
# Add daily reversal helpers before automatic scanner.
needle='async function runAutomaticTopSetups(force=false){'
insert=r'''function v262DailyReversalSignal(payload){
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
function v262EarlyMoveScore(x){
 const sig=x?._earlyPriceSignal;let s=preliminaryRRGScore(x);
 if(sig)s+=Number(sig.score||0);
 const f=x?.fast||x||{},t=x?.trend||{};
 const fIn=(f?.tail_trajectory?f.tail_trajectory==='Rotating In':(f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory?t.tail_trajectory==='Rotating In':(t?.rs_up===true&&t?.mom_up===true));
 if(fIn)s+=2;if(tIn)s+=1;
 return s;
}
'''
assert needle in s
s=s.replace(needle,insert+needle,1)
# Expand supportive groups to include early-turning lagging sectors instead of waiting for confirmation.
old='const supportive=groups.filter(groupTrajectoryPass).sort((a,b)=>sectorHeatScore(b)-sectorHeatScore(a));'
new='const supportive=groups.filter(g=>groupTrajectoryPass(g)||strongestLaggingSectors(Math.max(3,groups.length)).some(z=>z.ticker===g.ticker)).sort((a,b)=>sectorHeatScore(b)-sectorHeatScore(a));'
assert old in s
s=s.replace(old,new,1)
# Keep stocks with a turning tail even before favorable quadrant confirmation.
old2='if(!stockTrajectoryPrefilter(x))return;\n         pool.push({...x,_parentTicker:g.ticker,_parentGroup:g,_parentHeat:sectorHeatScore(g)});'
new2='''const f=x?.fast||x||{},t=x?.trend||{};\n         const fIn=(f?.tail_trajectory?f.tail_trajectory==="Rotating In":(f?.rs_up===true&&f?.mom_up===true));\n         const tIn=(t?.tail_trajectory?t.tail_trajectory==="Rotating In":(t?.rs_up===true&&t?.mom_up===true));\n         if(!stockTrajectoryPrefilter(x)&&!fIn&&!tIn)return;\n         pool.push({...x,_parentTicker:g.ticker,_parentGroup:g,_parentHeat:sectorHeatScore(g)});'''
assert old2 in s
s=s.replace(old2,new2,1)
# Broaden candidate pool so price reversal can rescue an early name before top-16 cutoff.
s=s.replace('.sort((a,b)=>preliminaryRRGScore(b)-preliminaryRRGScore(a)).slice(0,60);','.sort((a,b)=>preliminaryRRGScore(b)-preliminaryRRGScore(a)).slice(0,90);',1)
# Before options scan, cheaply resolve daily price action for candidate pool and promote failed-2D reversals.
needle2='if(st)st.textContent=`Layer 3 · checking options on ${candidates.length} RRG candidates`;'
block=r'''if(st)st.textContent=`Layer 2.5 · checking early daily reversals on ${candidates.length} candidates`;
   for(let n=0;n<candidates.length;n+=6){
     await Promise.all(candidates.slice(n,n+6).map(async x=>{
       try{
         const r=await fetch(`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`),j=await r.json();
         if(r.ok&&j.ok)x._earlyPriceSignal=v262DailyReversalSignal(j);
       }catch(e){}
     }));
   }
   candidates=candidates.sort((a,b)=>v262EarlyMoveScore(b)-v262EarlyMoveScore(a)).slice(0,60);

   '''+needle2
assert needle2 in s
s=s.replace(needle2,block,1)
# Rank finalists with early reversal bonus.
s=s.replace('return (preliminaryRRGScore(b)+bq)-(preliminaryRRGScore(a)+aq);','return (v262EarlyMoveScore(b)+bq)-(v262EarlyMoveScore(a)+aq);',1)
# Surface early reversal in evaluation reasons by injecting after reasons init.
needle3='let raw=0,premiumAdjustment=0;'
rep3='let raw=0,premiumAdjustment=0;\n if(x?._earlyPriceSignal){raw+=x._earlyPriceSignal.score||0;reasons.push([x._earlyPriceSignal.kind,"instGood"]);}'
assert needle3 in s
s=s.replace(needle3,rep3,1)
# Do not auto-collapse speculative/early panel just because confirmed setups exist.
s=s.replace('if(qualifiedCount>0)el.removeAttribute("open");\n else el.setAttribute("open","");','el.setAttribute("open","");',1)
p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
r.write_text('''v26.2 — EARLIER MOVE DETECTION\n- Top Setups now considers Lagging-but-turning sectors before they fully graduate into Improving/Leading, reducing confirmation lag.\n- Added daily failed-2D reversal detection: a 2-down that trades below the prior low but reclaims it, with extra priority when followed by a second green day/follow-through.\n- Early price reversal strength now promotes candidates before the expensive Top-16 STRAT/value/premium stage instead of waiting for RRG confirmation after the move.\n- Broadened the preliminary pool and allows turning-tail stocks to survive the first stock gate even before favorable-quadrant confirmation.\n- Early Turn / speculative signals stay visible alongside confirmed Top Setups so the dashboard shows both EARLY and CONFIRMED opportunities.\n- v26.1 display fixes are retained separately in the next patch if not already merged.\n\n'''+rs)
