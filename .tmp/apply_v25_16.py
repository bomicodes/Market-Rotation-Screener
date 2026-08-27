from pathlib import Path

p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "25.15"','APP_VERSION = "25.16"',1)

old_css='.topSetupsEmpty{grid-column:1/-1;padding:13px;border:1px dashed #2b3b4b;border-radius:8px;color:#8092a4;font-size:10px}@media(max-width:900px){.topSetupsGrid{grid-template-columns:1fr}}'
new_css='.topSetupsEmpty{grid-column:1/-1;padding:13px;border:1px dashed #2b3b4b;border-radius:8px;color:#8092a4;font-size:10px}\n.nearestMisses{grid-column:1/-1}\n.nearestMissRow{padding:6px 0;border-bottom:1px solid #1b2835;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px}\n.nearestMissRow b{font-size:11px}\n@media(max-width:900px){.topSetupsGrid{grid-template-columns:1fr}}'
assert old_css in s, 'Top Setups empty-state CSS anchor not found'
s=s.replace(old_css,new_css,1)

old_eval=''' const hardPass=rrgPass && !fOut && !tOut &&\n   (liq==="Liquid"||liq==="Tradable") &&\n   va?.strength!=="REJECTION" && structureOk;\n\n return {score,reasons,va,stratPass,hardPass,alignment:align,premiumSupport:premium};'''
new_eval=''' const hardPass=rrgPass && !fOut && !tOut &&\n   (liq==="Liquid"||liq==="Tradable") &&\n   va?.strength!=="REJECTION" && structureOk;\n\n const gateFailures=[];\n if(!rrgPass)gateFailures.push("RRG not aligned");\n if(fOut||tOut)gateFailures.push("Tail rotating out");\n if(!(liq==="Liquid"||liq==="Tradable"))gateFailures.push("Options not liquid/tradable");\n if(va?.strength==="REJECTION")gateFailures.push("Value acceptance rejected");\n if(!structureOk)gateFailures.push(ctx?.structure?.plan_error||"Invalid trade-plan structure");\n if(hardPass&&score<55)gateFailures.push(`Score ${score}/100 below the 55 A-quality bar`);\n\n return {score,reasons,va,stratPass,hardPass,alignment:align,premiumSupport:premium,gateFailures};'''
assert old_eval in s, 'topSetupEvaluation anchor not found'
s=s.replace(old_eval,new_eval,1)

old_empty=''' if(!rows.length){\n   const msg=automaticTopSetupsRunning?"Scanning all supportive sectors / themes…":"No market-wide A-quality setup or qualified premium-support watch currently. The scanner will not force a pick.";\n   g.innerHTML=`<div class="topSetupsEmpty">${msg}</div>`;return\n }'''
new_empty=''' if(!rows.length){\n   const msg=automaticTopSetupsRunning?"Scanning all supportive sectors / themes…":"No market-wide A-quality setup or qualified premium-support watch currently. The scanner will not force a pick.";\n   // True worst case: nothing qualifies and no premium-support watch exists either.\n   // Show the nearest misses by raw score so it's clear whether this is a\n   // genuinely quiet market or something is actually broken.\n   const nearest=evaluated.sort((a,b)=>b.e.score-a.e.score).slice(0,5);\n   const nearestHTML=nearest.length?`<div class="nearestMisses"><div class="tiny" style="margin-top:10px;color:#7f97a8">NEAREST MISSES · why they didn't qualify</div>${\n     nearest.map(({x,e})=>`<div class="nearestMissRow"><b>${x.ticker}</b> <span class="tiny">${e.score}/100</span><div class="tiny" style="color:#c98a3a">${(e.gateFailures||[]).join(" · ")||"—"}</div></div>`).join("")\n   }</div>`:"";\n   g.innerHTML=`<div class="topSetupsEmpty">${msg}</div>${nearestHTML}`;return\n }'''
assert old_empty in s, 'renderTopSetups empty-state anchor not found'
s=s.replace(old_empty,new_empty,1)

p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
r.write_text('''v25.16 — TOP SETUPS DIAGNOSTIC: NEAREST MISSES\n- Complements v25.15's premium-support-watch fallback: when even the watch tier is empty (true worst case — no A-quality setup and no qualifying premium watch), there was previously no way to tell "the scanner correctly found nothing today" apart from "something is silently broken."\n- topSetupEvaluation() now returns gateFailures: a plain-language list of which specific gate(s) a candidate failed (RRG not aligned, tail rotating out, options not liquid/tradable, value acceptance rejected, invalid trade-plan structure, or a below-threshold score with the actual number shown).\n- The empty-state Top Setups panel now shows a "Nearest misses" list in that true-worst-case scenario: the top 5 candidates by raw score regardless of hardPass, each with its score and specific failure reason(s).\n\n'''+rs)
