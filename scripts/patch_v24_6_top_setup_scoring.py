from pathlib import Path

p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "24.5"','APP_VERSION = "24.6"')

start=s.index('function topSetupEvaluation(x){')
end=s.index('function groupTrajectoryPass(g){',start)
new=r'''function topSetupEvaluation(x){
 const reasons=[],f=x.fast||x,t=x.trend||{},opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker];
 let raw=0;

 // ---------------- ROTATION / LEADERSHIP (intentionally capped) ----------------
 // RRG alignment, tail direction and quadrant are correlated views of relative
 // strength. They should not independently dominate the full 100-point score.
 const parent=x._parentGroup||null;
 if(parent){
   const pf=parent.fast||parent,pt=parent.trend||{};
   const pFastIn=(pf?.tail_trajectory ? pf.tail_trajectory==="Rotating In" : (pf?.rs_up===true&&pf?.mom_up===true));
   const pTrendIn=(pt?.tail_trajectory ? pt.tail_trajectory==="Rotating In" : (pt?.rs_up===true&&pt?.mom_up===true));
   const pGood=["Leading","Improving"].includes(pf?.quadrant)&&(["Leading","Improving"].includes(pt?.quadrant)||pTrendIn);
   if(pGood||pFastIn){raw+=5;reasons.push([`${x._parentTicker||"Group"} supportive`,"good"]);}
   else {raw-=5;reasons.push([`${x._parentTicker||"Group"} mixed`,"warn"]);}
 }

 const fq=String(f?.quadrant||""),tq=String(t?.quadrant||"");
 const fIn=(f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory ? t.tail_trajectory==="Rotating In" : (t?.rs_up===true&&t?.mom_up===true));
 const fOut=(f?.tail_trajectory ? f.tail_trajectory==="Rotating Out" : (f?.rs_up===false&&f?.mom_up===false));
 const tOut=(t?.tail_trajectory ? t.tail_trajectory==="Rotating Out" : (t?.rs_up===false&&t?.mom_up===false));
 const fGood=["Improving","Leading"].includes(fq),tGood=["Improving","Leading"].includes(tq);
 let align="NONE";
 if(fGood&&tGood&&fIn&&!tOut) align="FULL";
 else if(fIn&&(fGood||fq==="Lagging")&&tIn&&!tOut) align="EARLY";
 else if(fGood&&tGood&&!fOut&&!tOut) align="FULL";

 let rotation=0;
 if(align==="FULL"){rotation+=20;reasons.push(["Full RRG alignment","good"]);}
 else if(align==="EARLY"){rotation+=17;reasons.push(["Early RRG alignment","good"]);}
 else if(fIn&&!fOut){rotation+=8;reasons.push(["Fast rotation only","warn"]);}
 if(fIn){rotation+=5;reasons.push(["Fast tail NE","good"]);}
 if(tIn){rotation+=5;reasons.push(["Trend tail NE","good"]);}
 if(fGood)rotation+=2;
 if(tGood)rotation+=2;
 if(fOut){rotation-=10;reasons.push(["Fast rotating out","warn"]);}
 if(tOut){rotation-=12;reasons.push(["Trend rotating out","warn"]);}
 raw+=Math.max(-12,Math.min(35,rotation));

 // ---------------- EXECUTION QUALITY ----------------
 const liq=opt?.liquidity;
 if(liq==="Liquid"){raw+=10;reasons.push(["Options liquid","good"]);}
 else if(liq==="Tradable"){raw+=7;reasons.push(["Options tradable","good"]);}
 if(opt?.iv_state==="Cheap / Crushed"){raw+=5;reasons.push(["IV attractive","good"]);}
 else if(opt?.iv_state==="Normal")raw+=3;
 else if(opt?.iv_state==="Juiced"){raw-=4;reasons.push(["IV juiced","warn"]);}

 // ---------------- PRICE / VALUE STRUCTURE ----------------
 if(va?.strength==="CONFIRMED"){raw+=10;reasons.push([va.state,"good"]);}
 else if(va?.strength==="DEVELOPING"){raw+=5;reasons.push([va.state,"warn"]);}
 else if(va?.strength==="REJECTION"){raw-=12;reasons.push([va.state,"warn"]);}
 else if(!va){reasons.push(["Value pending","warn"]);}

 let stratPass=false;
 if(strat){
   stratPass=strat.continuity==="bullish"||strat.continuity==="bearish";
   if(stratPass){raw+=8;reasons.push([`${strat.continuity==="bullish"?"Bullish":"Bearish"} STRAT`,"good"]);}
   else reasons.push(["STRAT mixed","warn"]);
 }else reasons.push(["STRAT pending","warn"]);

 let directionConflict=false;
 if(stratPass&&va?.direction&&va.direction!=="neutral"){
   if(strat.continuity===va.direction){raw+=7;reasons.push(["STRAT + value agree","good"]);}
   else {raw-=15;directionConflict=true;reasons.push(["STRAT/value conflict","warn"]);}
 }

 // ---------------- POSITIONING / PATH ----------------
 const pos=opt?.positioning;
 const tradeDir=(va?.direction&&va.direction!=="neutral")?va.direction:(stratPass?strat.continuity:null);
 if(pos?.available&&tradeDir){
   if(pos.gamma_regime==="Negative / amplifying"){raw+=4;reasons.push(["Negative gamma (amplifying)","good"]);}
   else if(pos.gamma_regime==="Positive / dampening"){raw-=2;reasons.push(["Positive gamma (dampening)","warn"]);}
   const spot=Number(opt.spot),wall=tradeDir==="bullish"?Number(pos.call_wall):Number(pos.put_wall);
   if(Number.isFinite(spot)&&spot>0&&Number.isFinite(wall)){
     const roomPct=tradeDir==="bullish"?((wall-spot)/spot*100):((spot-wall)/spot*100);
     if(roomPct>3){raw+=5;reasons.push(["Room to next wall","good"]);}
     else if(roomPct<=1){raw-=5;reasons.push(["Near gamma wall","warn"]);}
   }
 }

 const mom=Number(f.rs_momentum??f.momentum);
 if(Number.isFinite(mom)&&mom>105){raw-=5;reasons.push(["Extended","warn"]);}

 const score=Math.max(0,Math.min(100,Math.round(raw)));
 const rrgPass=(align==="FULL"||align==="EARLY");
 // A high-confidence finalist must have real structure and scenario data; missing
 // chart/STRAT evidence is no longer treated like a neutral pass.
 const hardPass=rrgPass&&!fOut&&!tOut&&
   (liq==="Liquid"||liq==="Tradable")&&
   !!va&&va.strength!=="REJECTION"&&
   !!strat&&stratPass&&!directionConflict;

 return {score,reasons,va,stratPass,hardPass,alignment:align};
}
'''
s=s[:start]+new+s[end:]
p.write_text(s)
print('patched top setup scoring v24.6')
