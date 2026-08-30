from pathlib import Path

# One-shot migration helper for the v27.7 early-turn institutional-flow patch.
path = Path("app.py")
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once('APP_VERSION = "27.6"', 'APP_VERSION = "27.7"', "version")

replace_once(
'''function earlyTurnScore(x){
 const opt=optionScanMap[x.ticker];let s=preliminaryRRGScore(x);
 if(opt?.liquidity==="Liquid")s+=2;
 if(opt?.iv_state==="Cheap / Crushed")s+=2;
 return s;
}''',
'''function institutionalFlowForTicker(ticker){
 const t=String(ticker||"").toUpperCase();
 return (institutionalRadarResults||[]).find(r=>String(r?.ticker||"").toUpperCase()===t)||null;
}
function institutionalFlowConfirmed(flow){
 if(!flow?.ok)return false;
 return Number(flow.largest_multiple||0)>=1.5&&Number(flow.repeat_days||0)>=2;
}
function institutionalFlowBonus(flow){
 if(!flow?.ok)return 0;
 const activity=Number(flow.activity_score||0),mult=Number(flow.largest_multiple||0),repeat=Number(flow.repeat_days||0);
 let bonus=Math.min(1.5,Math.max(0,activity)*0.15);
 if(mult>=1.25)bonus+=0.5;
 if(mult>=1.5)bonus+=0.75;
 if(mult>=2)bonus+=0.5;
 if(repeat>=2)bonus+=0.75;
 if(repeat>=3)bonus+=0.5;
 if(institutionalFlowConfirmed(flow))bonus+=0.75;
 return Math.min(4.5,bonus);
}
function mergeEarlyTurnInstitutionalFlow(){
 (earlyTurnWatchData||[]).forEach(row=>{
   const flow=institutionalFlowForTicker(row?.x?.ticker);
   row.institutional_flow=flow;
   row.activity_score=Number(flow?.activity_score||0);
   row.largest_multiple=Number(flow?.largest_multiple||0);
   row.repeat_days=Number(flow?.repeat_days||0);
   row.flow_confirmed=institutionalFlowConfirmed(flow);
   if(row.x)row.x._institutionalFlow=flow;
 });
 earlyTurnWatchData=(earlyTurnWatchData||[]).sort((a,b)=>earlyTurnScore(b.x)-earlyTurnScore(a.x));
}
function earlyTurnScore(x){
 const opt=optionScanMap[x.ticker];let s=preliminaryRRGScore(x);
 if(opt?.liquidity==="Liquid")s+=2;
 if(opt?.iv_state==="Cheap / Crushed")s+=2;
 s+=institutionalFlowBonus(x?._institutionalFlow||institutionalFlowForTicker(x?.ticker));
 return s;
}''',
"flow scoring helpers",
)

replace_once(
' const rows=(window.allSupportiveCandidates||[]).slice().sort((a,b)=>opportunityScore(b)-opportunityScore(a)).slice(0,12);',
' const rows=(earlyTurnWatchData||[]).map(row=>row?.x).filter(Boolean);',
"institutional candidate pool",
)

replace_once(
' if(!rows.length){if(st)st.textContent="Run Top Setups first to build the candidate pool.";return}',
' if(!rows.length){institutionalRadarResults=[];renderInstitutionalRadar();if(st)st.textContent="No early-turn candidates available for institutional confirmation.";return}',
"empty institutional pool status",
)

replace_once(
'   institutionalRadarResults=j.results||[];renderInstitutionalRadar();',
'   institutionalRadarResults=j.results||[];mergeEarlyTurnInstitutionalFlow();renderInstitutionalRadar();renderEarlyTurnWatch();',
"merge institutional results",
)

replace_once(
''' await Promise.all([runEarlyTurnWatch(),runInstitutionalRadar()]);
 const earlyCount=earlyTurnWatchData.length;''',
''' await runEarlyTurnWatch();
 await runInstitutionalRadar();
 const earlyCount=earlyTurnWatchData.length;''',
"sequential speculative scan",
)

replace_once(
'''   const sourceLabel=source==="sector"?"SECTOR-LED":"STOCK-LED";
   const premiumStateClass=pc?''',
'''   const sourceLabel=source==="sector"?"SECTOR-LED":"STOCK-LED";
   const flow=x?._institutionalFlow||institutionalFlowForTicker(x.ticker),flowConfirmed=institutionalFlowConfirmed(flow);
   const flowBadge=flowConfirmed?`<span class="instGood" style="display:inline-block;margin-top:5px;padding:3px 7px;border-radius:999px;font-size:11px;font-weight:800">FLOW CONFIRMED · ${Number(flow.largest_multiple||0).toFixed(1)}× · ${Number(flow.repeat_days||0)}/4 sessions · activity ${Number(flow.activity_score||0).toFixed(1)}/10</span>`:"";
   const premiumStateClass=pc?''',
"early-turn flow badge vars",
)

replace_once(
'''     <div class="topSetupHead"><div><div class="topSetupTicker">${x.ticker}</div><div class="topSetupStatus">${sourceLabel} · ${tailNote}${source!=="sector"&&x._parentTicker?` · ${x._parentTicker}`:""}</div></div></div>
${premiumLine}''',
'''     <div class="topSetupHead"><div><div class="topSetupTicker">${x.ticker}</div><div class="topSetupStatus">${sourceLabel} · ${tailNote}${source!=="sector"&&x._parentTicker?` · ${x._parentTicker}`:""}</div>${flowBadge}</div></div>
${premiumLine}''',
"early-turn flow badge markup",
)

path.write_text(text)
print("Applied v27.7 early-turn institutional-flow integration")
