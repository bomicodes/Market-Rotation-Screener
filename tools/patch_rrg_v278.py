from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()


def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.7"','APP_VERSION = "27.8"','version')

# Compute quantitative metrics from the same normalized 100/100 RRG coordinates
# already used to draw the tails. 0 degrees = east, 90 = north.
needle='''        if dx_tail > eps and dy_tail > eps:\n            tail_trajectory = "Rotating In"\n        elif dx_tail < -eps and dy_tail < -eps:\n            tail_trajectory = "Rotating Out"\n        else:\n            tail_trajectory = "Neutral"\n        score = 0.0\n'''
repl='''        if dx_tail > eps and dy_tail > eps:\n            tail_trajectory = "Rotating In"\n        elif dx_tail < -eps and dy_tail < -eps:\n            tail_trajectory = "Rotating Out"\n        else:\n            tail_trajectory = "Neutral"\n\n        # Quantitative RRG diagnostics. Keep these derived from the plotted\n        # coordinates so chart, grid and scanner always describe the same tail.\n        if len(tail_pts) >= 2:\n            step_dx = float(tail_pts[-1]["x"] - tail_pts[-2]["x"])\n            step_dy = float(tail_pts[-1]["y"] - tail_pts[-2]["y"])\n        else:\n            step_dx = step_dy = 0.0\n        velocity = math.hypot(step_dx, step_dy)\n        heading_deg = (math.degrees(math.atan2(step_dy, step_dx)) + 360.0) % 360.0 if velocity > eps else None\n        heading_names = ("E","NE","N","NW","W","SW","S","SE")\n        heading = heading_names[int(((heading_deg or 0.0) + 22.5) // 45.0) % 8] if heading_deg is not None else "FLAT"\n        distance = math.hypot(x - 100.0, y - 100.0)\n        radial_angle = (math.degrees(math.atan2(y - 100.0, x - 100.0)) + 360.0) % 360.0 if distance > eps else None\n        prev_distance = math.hypot(px - 100.0, py - 100.0)\n        prev_angle = (math.degrees(math.atan2(py - 100.0, px - 100.0)) + 360.0) % 360.0 if prev_distance > eps else None\n        angle_roc = None\n        if radial_angle is not None and prev_angle is not None:\n            angle_roc = ((radial_angle - prev_angle + 180.0) % 360.0) - 180.0\n\n        score = 0.0\n'''
once(needle,repl,'metric calculation insertion')

needle='''            "tail_trajectory":tail_trajectory,\n            "tail_dx":round(dx_tail,4),"tail_dy":round(dy_tail,4),\n            "date":pair.index[li].strftime("%Y-%m-%d")\n'''
repl='''            "tail_trajectory":tail_trajectory,\n            "tail_dx":round(dx_tail,4),"tail_dy":round(dy_tail,4),\n            "heading":heading,"heading_deg":round(heading_deg,1) if heading_deg is not None else None,\n            "velocity":round(velocity,4),"distance":round(distance,4),\n            "radial_angle":round(radial_angle,1) if radial_angle is not None else None,\n            "angle_roc":round(angle_roc,2) if angle_roc is not None else None,\n            "date":pair.index[li].strftime("%Y-%m-%d")\n'''
once(needle,repl,'metric payload fields')

# Add a dedicated quantitative grid directly below the stock RRG/table pair.
needle='''  <div class="grid2">\n    <div class="panel"><canvas id="stockChart" width="900" height="540"></canvas></div>\n    <div class="panel"><div class="scroll"><table><thead><tr><th></th><th>Ticker</th><th>Score</th><th>Fast</th><th>Trend</th><th>Rotation stage</th><th>Opportunity</th><th>Options</th></tr></thead><tbody id="stockRows"></tbody></table></div></div>\n  </div>\n\n  <div class="priceActionGrid">\n'''
repl='''  <div class="grid2">\n    <div class="panel"><canvas id="stockChart" width="900" height="540"></canvas></div>\n    <div class="panel"><div class="scroll"><table><thead><tr><th></th><th>Ticker</th><th>Score</th><th>Fast</th><th>Trend</th><th>Rotation stage</th><th>Opportunity</th><th>Options</th></tr></thead><tbody id="stockRows"></tbody></table></div></div>\n  </div>\n\n  <div class="panel" id="rrgMetricsPanel">\n    <div class="row" style="justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">\n      <div><b>RRG Metrics Grid</b><div class="tiny">Quantifies the same tail shown above · 100/100 center · click a row to open the ticker</div></div>\n      <div class="row" style="gap:8px">\n        <select id="rrgMetricsMode"><option value="fast">Fast</option><option value="trend">Trend</option></select>\n        <select id="rrgMetricsSort"><option value="rotation">Rotation quality</option><option value="velocity">Velocity</option><option value="distance">Distance</option><option value="angle_roc">Angle ROC</option><option value="rs">RS</option><option value="momentum">Momentum</option><option value="ticker">Ticker</option></select>\n      </div>\n    </div>\n    <div class="scroll" style="margin-top:8px"><table><thead><tr><th>Ticker</th><th>Quadrant</th><th>RS</th><th>Momentum</th><th>Heading</th><th>Velocity</th><th>Distance</th><th>Angle ROC</th><th>Rotation</th></tr></thead><tbody id="rrgMetricRows"></tbody></table></div>\n  </div>\n\n  <div class="priceActionGrid">\n'''
once(needle,repl,'metrics grid html')

# Quantification helpers. They deliberately add only a bounded bonus to Early Turn
# so flow/options and the underlying RRG gate still matter.
needle='''function earlyTurnScore(x){\n const opt=optionScanMap[x.ticker];let s=preliminaryRRGScore(x);\n'''
repl='''function rrgMetricSet(x,mode="fast"){\n return mode==="trend"?(x?.trend||{}):(x?.fast||x||{});\n}\nfunction rrgRotationQuality(m){\n if(!m)return 0;\n const h=String(m.heading||"FLAT");let s=0;\n if(h==="NE")s+=1.5; else if(h==="N"||h==="E")s+=0.75;\n const favorable=["NE","N","E"].includes(h);\n if(favorable){\n   s+=Math.min(1.25,Math.max(0,Number(m.velocity||0))*1.5);\n   s+=Math.min(0.75,Math.max(0,Number(m.distance||0))*0.25);\n   s+=Math.min(0.75,Math.abs(Number(m.angle_roc||0))/20*0.75);\n }\n return Math.min(4.25,s);\n}\nfunction rrgRotationQualityBonus(x){\n const f=rrgRotationQuality(rrgMetricSet(x,"fast"));\n const t=rrgRotationQuality(rrgMetricSet(x,"trend"));\n return Math.min(4.5,f*0.7+t*0.3);\n}\nfunction rrgRotationLabel(m){\n if(!m)return "—";\n const q=String(m.quadrant||""),h=String(m.heading||"FLAT"),v=Number(m.velocity||0);\n if(v<0.08)return "Flat / noise";\n if(q==="Lagging"&&["NE","N","E"].includes(h))return "Early Turn";\n if(q==="Improving"&&["NE","N","E"].includes(h))return "Accelerating";\n if(q==="Leading"&&["NE","N","E"].includes(h))return "Leading";\n if(["SW","S","W"].includes(h))return "Rotating Out";\n return "Mixed";\n}\nfunction earlyTurnScore(x){\n const opt=optionScanMap[x.ticker];let s=preliminaryRRGScore(x);\n'''
once(needle,repl,'rotation helpers')

needle=''' if(opt?.iv_state==="Cheap / Crushed")s+=2;\n s+=institutionalFlowBonus(x?._institutionalFlow||institutionalFlowForTicker(x?.ticker));\n return s;\n}\n'''
repl=''' if(opt?.iv_state==="Cheap / Crushed")s+=2;\n s+=rrgRotationQualityBonus(x);\n s+=institutionalFlowBonus(x?._institutionalFlow||institutionalFlowForTicker(x?.ticker));\n return s;\n}\n'''
once(needle,repl,'early turn metric bonus')

# Render/sort the stock metrics grid from the currently filtered rows.
needle='''function renderLiveStocks(){\n const data=filteredLiveStocks();\n'''
repl='''function renderRRGMetricGrid(data){\n const body=document.getElementById("rrgMetricRows"),modeEl=document.getElementById("rrgMetricsMode"),sortEl=document.getElementById("rrgMetricsSort");\n if(!body)return;\n const mode=modeEl?.value||"fast",sort=sortEl?.value||"rotation";\n const rows=[...(data||[])];\n const val=(x,key)=>{const m=rrgMetricSet(x,mode);if(key==="rotation")return rrgRotationQuality(m);if(key==="rs")return Number(m.x||0);if(key==="momentum")return Number(m.y||0);if(key==="ticker")return String(x.ticker||"");return Number(m[key]||0)};\n rows.sort((a,b)=>sort==="ticker"?String(val(a,sort)).localeCompare(String(val(b,sort))):val(b,sort)-val(a,sort));\n const arrow={NE:"↗",N:"↑",E:"→",SE:"↘",S:"↓",SW:"↙",W:"←",NW:"↖",FLAT:"·"};\n body.innerHTML=rows.map(x=>{const m=rrgMetricSet(x,mode),h=m.heading||"FLAT",roc=m.angle_roc;return `<tr class="clickrow" data-rrg-metric-ticker="${x.ticker}"><td><b>${x.ticker}</b></td><td>${m.quadrant||"—"}</td><td>${m.x==null?"—":fmt(m.x,2)}</td><td>${m.y==null?"—":fmt(m.y,2)}</td><td><b>${arrow[h]||"·"} ${h}</b>${m.heading_deg==null?"":`<div class="tiny">${fmt(m.heading_deg,0)}°</div>`}</td><td>${m.velocity==null?"—":fmt(m.velocity,2)}</td><td>${m.distance==null?"—":fmt(m.distance,2)}</td><td class="${Number(roc||0)>=0?'up':'down'}">${roc==null?"—":`${roc>=0?'+':''}${fmt(roc,1)}°`}</td><td><b>${rrgRotationLabel(m)}</b><div class="tiny">Q ${rrgRotationQuality(m).toFixed(1)}/4.25</div></td></tr>`}).join("");\n document.querySelectorAll("[data-rrg-metric-ticker]").forEach(row=>row.addEventListener("click",()=>openSectorStockTicker(row.dataset.rrgMetricTicker,{scroll:true})));\n if(modeEl&&!modeEl.dataset.bound){modeEl.dataset.bound="1";modeEl.addEventListener("change",()=>renderRRGMetricGrid(filteredLiveStocks()));}\n if(sortEl&&!sortEl.dataset.bound){sortEl.dataset.bound="1";sortEl.addEventListener("change",()=>renderRRGMetricGrid(filteredLiveStocks()));}\n}\n\nfunction renderLiveStocks(){\n const data=filteredLiveStocks();\n'''
once(needle,repl,'grid renderer')

needle=''' drawRRG("stockChart",data);\n document.getElementById("stockRows").innerHTML=data.map((x,k)=>`<tr class="clickrow liveTickerRow" data-live-ticker="${x.ticker}">\n'''
repl=''' drawRRG("stockChart",data);\n renderRRGMetricGrid(data);\n document.getElementById("stockRows").innerHTML=data.map((x,k)=>`<tr class="clickrow liveTickerRow" data-live-ticker="${x.ticker}">\n'''
once(needle,repl,'grid render call')

p.write_text(s)
print('patched app.py to v27.8')
