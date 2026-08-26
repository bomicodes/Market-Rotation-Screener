from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()
orig=s

s=re.sub(r'APP_VERSION = "24\.8"','APP_VERSION = "24.9"',s,count=1)

# Add a compact expandable risk-support breakdown directly beneath Breadth & Risk.
old='''      <div class="panel">\n        <div class="dashTopline"><span class="dashTitle">BREADTH & RISK</span><span id="regimeSummary" class="note">Loading…</span></div>\n        <div id="dashboardBreadth" class="breadthList"></div>\n      </div>'''
new='''      <div class="panel">\n        <div class="dashTopline"><span class="dashTitle">BREADTH & RISK</span><span id="regimeSummary" class="note">Loading…</span></div>\n        <button type="button" id="riskSupportToggle" class="riskSupportToggle" aria-expanded="false">Risk support details <span>▾</span></button>\n        <div id="riskSupportBreakdown" class="riskSupportBreakdown" hidden></div>\n        <div id="dashboardBreadth" class="breadthList"></div>\n      </div>'''
if old not in s:
    raise SystemExit('Breadth & Risk panel block not found')
s=s.replace(old,new,1)

# Make the score itself a clear tap target and populate the exact four components.
old_reg=''' const regime=document.getElementById("regimeSummary");\n if(regime){\n   const score=j.risk_score==null?"—":`${Math.max(0,Math.min(4,Math.round((j.risk_score+4)/2)))}/4`;\n   regime.innerHTML=`<b>${j.risk_appetite||"Mixed"}</b> · Participation: <b>${j.participation||"—"}</b> · Risk support ${score}`;\n }'''
new_reg=''' const regime=document.getElementById("regimeSummary");\n const riskScoreDisplay=j.risk_score==null?"—":`${Math.max(0,Math.min(4,Math.round((j.risk_score+4)/2)))}/4`;\n if(regime){\n   regime.innerHTML=`<b>${j.risk_appetite||"Mixed"}</b> · Participation: <b>${j.participation||"—"}</b> · <button type="button" class="riskScoreInline" id="riskScoreInline">Risk support <b>${riskScoreDisplay}</b></button>`;\n }\n const rb=document.getElementById("riskSupportBreakdown");\n if(rb){\n   const riskParts=[\n     ["Breadth",i.RSP?.d5,"RSP / SPY"],\n     ["Small caps",i.IWM?.d5,"IWM / SPY"],\n     ["Growth",i.QQQ?.d5,"QQQ / SPY"],\n     ["Credit",i.CREDIT?.d5,"HYG / LQD"]\n   ];\n   rb.innerHTML=riskParts.map(([label,v,detail])=>{\n     const known=v!=null,good=known&&Number(v)>0,bad=known&&Number(v)<0;\n     const mark=!known?"—":good?"✓":"✕";\n     const state=!known?"neutral":good?"good":"bad";\n     const move=!known?"—":`${Number(v)>=0?"+":""}${fmt(Number(v),2)}% / 5d`;\n     return `<div class="riskPart ${state}"><span class="riskMark">${mark}</span><div><b>${label}</b><small>${detail}</small></div><strong>${move}</strong></div>`;\n   }).join("");\n }'''
if old_reg not in s:
    raise SystemExit('regime summary block not found')
s=s.replace(old_reg,new_reg,1)

# Add one delegated click handler after the main script starts; works after every re-render.
marker='''document.addEventListener("DOMContentLoaded",refreshSourceHealth);'''
handler='''document.addEventListener("click",function(e){\n const trigger=e.target.closest("#riskSupportToggle,#riskScoreInline");\n if(!trigger)return;\n const box=document.getElementById("riskSupportBreakdown"),btn=document.getElementById("riskSupportToggle");\n if(!box)return;\n const opening=box.hasAttribute("hidden");\n if(opening)box.removeAttribute("hidden");else box.setAttribute("hidden","");\n if(btn){btn.setAttribute("aria-expanded",opening?"true":"false");const a=btn.querySelector("span");if(a)a.textContent=opening?"▴":"▾";}\n});\n'''
if marker not in s:
    raise SystemExit('DOMContentLoaded source-health marker not found')
s=s.replace(marker,handler+marker,1)

# CSS: desktop subtle, mobile compact. Also tighten v24.8 Sector Summary without losing Fast/Trend/signal data.
css_marker='/* v24 Institutional Decision Layer */'
css=r'''
/* v24.9 risk-support explainability + denser mobile sector summary */
.riskSupportToggle{width:100%;display:flex;align-items:center;justify-content:space-between;border:1px solid #213445;background:#0a141d;color:#91a4b7;border-radius:7px;padding:7px 9px;margin:-2px 0 7px;font-size:9px;font-weight:800;letter-spacing:.25px;text-align:left}
.riskSupportToggle:hover{border-color:#34536d;color:#c6d4e0}.riskScoreInline{border:0;background:transparent;color:inherit;padding:0;font:inherit;cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}.riskScoreInline b{color:#dce8f2}
.riskSupportBreakdown{border:1px solid #203242;background:#09131b;border-radius:8px;padding:4px 9px;margin:0 0 8px}.riskSupportBreakdown[hidden]{display:none!important}.riskPart{display:grid;grid-template-columns:18px 1fr auto;gap:7px;align-items:center;padding:6px 0;border-bottom:1px solid #172735}.riskPart:last-child{border-bottom:0}.riskPart .riskMark{font-size:11px;font-weight:900}.riskPart b{display:block;font-size:10px}.riskPart small{display:block;color:#74889b;font-size:8px;margin-top:1px}.riskPart strong{font-size:9px}.riskPart.good .riskMark,.riskPart.good strong{color:#4ade80}.riskPart.bad .riskMark,.riskPart.bad strong{color:#fb7185}.riskPart.neutral{color:#94a3b8}
@media(max-width:760px){
 .riskSupportToggle{padding:6px 8px;margin:0 0 6px;font-size:8px}.riskSupportBreakdown{padding:3px 7px;margin-bottom:6px}.riskPart{padding:5px 0;grid-template-columns:16px 1fr auto}.riskPart b{font-size:9px}.riskPart small{font-size:7px}.riskPart strong{font-size:8px}
 .sectorSummaryPanel{padding:8px!important}.sectorSummaryPanel .dashTopline{margin-bottom:4px}.sectorSummaryPanel .dashTitle{font-size:11px}.sectorSummaryPanel .dashTopline .note{font-size:8px}
 .sectorSummaryPanel tr.sectorTickerRow{grid-template-columns:20px minmax(0,1fr);gap:2px 6px;padding:6px 1px!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(1){font-size:9px;padding-top:1px!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2)>b{font-size:12px!important;line-height:1.1}.sectorSummaryPanel tr.sectorTickerRow td:nth-child(2) .tiny{font-size:8px!important;line-height:1.2;margin-top:1px!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3),.sectorSummaryPanel tr.sectorTickerRow td:nth-child(4){gap:4px!important;min-height:18px}.sectorSummaryPanel tr.sectorTickerRow td:nth-child(5){margin-top:0!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3)::before,.sectorSummaryPanel tr.sectorTickerRow td:nth-child(4)::before{font-size:6.5px!important;min-width:42px!important;letter-spacing:.35px!important}
 .sectorSummaryPanel .badge{font-size:7px!important;padding:2px 5px!important}.sectorSummaryPanel .flag{font-size:7px!important;padding:2px 5px!important}.sectorSummaryPanel td:nth-child(3) .tiny,.sectorSummaryPanel td:nth-child(4) .tiny{font-size:7px!important}
}
'''
if css_marker not in s:
    raise SystemExit('CSS marker not found')
s=s.replace(css_marker,css+css_marker,1)

if s==orig:
    raise SystemExit('No changes made')
p.write_text(s)
print('patched app.py to v24.9')
