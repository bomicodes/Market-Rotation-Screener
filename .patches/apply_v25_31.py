from pathlib import Path
p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "25.30"','APP_VERSION = "25.31"',1)
s=s.replace('''function topSetupEvaluation(x){
 const reasons=[],f=x.fast||x,t=x.trend||{},opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker],premium=premiumSupportMap[x.ticker];
 let raw=0;''','''function topSetupEvaluation(x){
 const reasons=[],f=x.fast||x,t=x.trend||{},opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker],premium=premiumSupportMap[x.ticker];
 let raw=0,premiumAdjustment=0;''',1)
s=s.replace('''   if(ps>=80){raw+=6;reasons.push([`Premium entry attractive · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=65){raw+=4;reasons.push([`Premium near support · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=50){raw+=2;reasons.push([`Premium entry fair · ${ps.toFixed(0)}`,"warn"]);}
   else if(pc?.state==="AWAY FROM SUPPORT"){raw-=2;reasons.push(["Premium extended vs support","warn"]);}''','''   if(ps>=80){premiumAdjustment=6;raw+=premiumAdjustment;reasons.push([`Premium entry attractive · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=65){premiumAdjustment=4;raw+=premiumAdjustment;reasons.push([`Premium near support · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=50){premiumAdjustment=2;raw+=premiumAdjustment;reasons.push([`Premium entry fair · ${ps.toFixed(0)}`,"warn"]);}
   else if(pc?.state==="AWAY FROM SUPPORT"){premiumAdjustment=-2;raw+=premiumAdjustment;reasons.push(["Premium extended vs support","warn"]);}''',1)
s=s.replace(''' const score=Math.max(0,Math.min(100,Math.round(raw)));''',''' const score=Math.max(0,Math.min(100,Math.round(raw)));
 // Qualification intentionally ignores premium location. Premium can improve or
 // worsen the displayed/ranking score, but it cannot decide whether the
 // underlying setup is allowed to appear.
 const qualificationScore=Math.max(0,Math.min(100,Math.round(raw-premiumAdjustment)));''',1)
s=s.replace(''' if(hardPass&&score<55)gateFailures.push(`Score ${score}/100 below the 55 A-quality bar`);

 return {score,reasons,va,stratPass,hardPass,alignment:align,premiumSupport:premium,gateFailures};''',''' if(hardPass&&qualificationScore<45)gateFailures.push(`Underlying setup ${qualificationScore}/100 below the 45 qualification bar`);

 return {score,qualificationScore,reasons,va,stratPass,hardPass,alignment:align,premiumSupport:premium,gateFailures};''',1)
s=s.replace(''' const qualified=evaluated.filter(z=>z.e.hardPass&&z.e.score>=55).sort((a,b)=>b.e.score-a.e.score);''',''' const qualified=evaluated.filter(z=>z.e.hardPass&&z.e.qualificationScore>=45).sort((a,b)=>b.e.score-a.e.score);''',1)
s=s.replace('''if(st)st.textContent=rows.length?`${rows.length} candidate${rows.length===1?"":"s"} · premium shown as entry quality` : "No A-quality setup currently";''','''if(st)st.textContent=rows.length?`${rows.length} candidate${rows.length===1?"":"s"} · qualified on underlying setup · premium is entry quality` : "No A-quality setup currently";''',1)
# Make nearest misses show the underlying qualification score so debugging is transparent.
s=s.replace('''nearest.map(({x,e})=>`<div class="nearestMissRow"><b>${x.ticker}</b> <span class="tiny">${e.score}/100</span>''','''nearest.map(({x,e})=>`<div class="nearestMissRow"><b>${x.ticker}</b> <span class="tiny">setup ${e.qualificationScore}/100 · ranked ${e.score}/100</span>''',1)
p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
r.write_text('''v25.31 — FIX TOP SETUPS AFTER PREMIUM DECOUPLING\n- Fixed the regression where reducing premium-support weight caused previously valid XLK/IGV setups to fall below the same 55-point total-score bar and disappear.\n- Added a separate qualificationScore that explicitly removes the premium adjustment. Top Setups now qualifies on the underlying setup only (RRG/sector alignment, options liquidity, value/structure, STRAT/GEX context), using a 45-point underlying bar.\n- Premium still modifies the displayed/ranking score by a small amount, so AT SUPPORT can improve entry preference and AWAY FROM SUPPORT can slightly reduce rank, but neither can make or break qualification.\n- Nearest Misses now shows both the underlying setup score and ranked score for easier debugging.\n\n'''+rs)
