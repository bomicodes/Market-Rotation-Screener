from pathlib import Path

p = Path('app.py')
s = p.read_text()

# Version bump for the focused RRG/heat-map correctness pass.
s = s.replace('APP_VERSION = "24.1"', 'APP_VERSION = "24.2"', 1)

old_score = '''function heatTone(score){return `h${Math.max(0,Math.min(10,Math.round(score||0)))}`;}\nfunction sectorHeatScore(x){\n const st=rotationStage(x).level; let s=st*2; const f=x.fast||x||{},t=x.trend||{};\n if(f.tail_trajectory==="Rotating In")s+=1; if(f.tail_trajectory==="Rotating Out")s-=1;\n if((t.quadrant==="Leading"||t.quadrant==="Improving")&&t.rs_up&&t.mom_up)s+=1;\n return Math.max(0,Math.min(10,s));\n}'''
new_score = '''function heatTone(score){return `h${Math.max(0,Math.min(10,Math.round(score||0)))}`;}\nfunction _rrgEndpoint(src){\n const pts=src?.tail||[]; const p=pts.length?pts[pts.length-1]:null;\n return p&&Number.isFinite(Number(p.x))&&Number.isFinite(Number(p.y))?{x:Number(p.x),y:Number(p.y)}:null;\n}\nfunction _meanStd(vals){\n const a=vals.filter(Number.isFinite); if(!a.length)return {mean:100,sd:1};\n const mean=a.reduce((s,v)=>s+v,0)/a.length;\n const variance=a.reduce((s,v)=>s+(v-mean)*(v-mean),0)/Math.max(1,a.length);\n return {mean,sd:Math.max(.18,Math.sqrt(variance))};\n}\nfunction sectorHeatScore(x){\n // True relative composite: compare the current Fast/Trend RS and momentum\n // coordinates with peers in the same universe. The old implementation was\n // a coarse stage counter, which could display 0.0 for a sector that was\n // literally in the Leading quadrant.\n const peers=(sectorData||[]).filter(r=>r?.group===x?.group);\n const universe=peers.length>=4?peers:(sectorData||[]);\n const rows=universe.map(r=>({r,f:_rrgEndpoint(r.fast||r),t:_rrgEndpoint(r.trend||{})}));\n const fx=_meanStd(rows.map(o=>o.f?.x)),fy=_meanStd(rows.map(o=>o.f?.y));\n const tx=_meanStd(rows.map(o=>o.t?.x)),ty=_meanStd(rows.map(o=>o.t?.y));\n const f=_rrgEndpoint(x.fast||x),t=_rrgEndpoint(x.trend||{});\n if(!f&&!t)return 5;\n const z=(v,st)=>Number.isFinite(v)?(v-st.mean)/st.sd:0;\n let composite=.35*z(f?.x,fx)+.25*z(f?.y,fy)+.25*z(t?.x,tx)+.15*z(t?.y,ty);\n // Small absolute quadrant anchor prevents "best of a weak group" from\n // looking strong solely because of cross-sectional standardization.\n const qv=q=>({Leading:1,Improving:.45,Weakening:-.45,Lagging:-1}[q]||0);\n composite=.78*composite+.22*(.6*qv((x.fast||x)?.quadrant)+.4*qv(x.trend?.quadrant));\n return Math.max(0,Math.min(10,5+1.8*composite));\n}'''
if old_score not in s:
    raise SystemExit('sectorHeatScore block not found')
s = s.replace(old_score, new_score, 1)

old_draw_head = '''function drawRRG(id,rows,focusTicker=undefined){\n rows=rrgRowsForChart(id,rows);\n const c=document.getElementById(id),ctx=c.getContext("2d"),W=c.width,H=c.height,p=42;'''
new_draw_head = '''function _rrgCanvasContext(c){\n const rect=c.getBoundingClientRect();\n const W=Math.max(320,Math.round(rect.width||c.clientWidth||900));\n const H=Math.max(280,Math.round(rect.height||c.clientHeight||600));\n const dpr=Math.min(2.5,Math.max(1,window.devicePixelRatio||1));\n const bw=Math.max(1,Math.round(W*dpr)),bh=Math.max(1,Math.round(H*dpr));\n if(c.width!==bw||c.height!==bh){c.width=bw;c.height=bh;}\n const ctx=c.getContext("2d");\n ctx.setTransform(dpr,0,0,dpr,0,0);\n return {ctx,W,H};\n}\nfunction drawRRG(id,rows,focusTicker=undefined){\n rows=rrgRowsForChart(id,rows);\n const c=document.getElementById(id),cv=_rrgCanvasContext(c),ctx=cv.ctx,W=cv.W,H=cv.H,p=48;'''
if old_draw_head not in s:
    raise SystemExit('drawRRG head not found')
s = s.replace(old_draw_head, new_draw_head, 1)

old_bounds = ''' let xs=[],ys=[];\n rows.forEach(r=>(r.tail||[]).forEach(pt=>{xs.push(pt.x);ys.push(pt.y)}));\n let xmin=Math.min(98,...xs)-.8,xmax=Math.max(102,...xs)+.8,\n     ymin=Math.min(98,...ys)-.8,ymax=Math.max(102,...ys)+.8;\n\n const X=x=>p+(x-xmin)/(xmax-xmin)*(W-2*p),\n       Y=y=>H-p-(y-ymin)/(ymax-ymin)*(H-2*p),\n       cx=X(100),cy=Y(100);'''
new_bounds = ''' let xs=[],ys=[];\n rows.forEach(r=>(r.tail||[]).forEach(pt=>{if(Number.isFinite(Number(pt.x)))xs.push(Number(pt.x));if(Number.isFinite(Number(pt.y)))ys.push(Number(pt.y));}));\n // Keep 100/100 exactly centered and use ONE pixels-per-RRG-unit scale for\n // both axes. This preserves tail angles and quadrant geometry instead of\n // stretching X and Y independently to fill the panel.\n const dx=Math.max(2,...xs.map(v=>Math.abs(v-100)))+.65;\n const dy=Math.max(2,...ys.map(v=>Math.abs(v-100)))+.65;\n const plotW=Math.max(1,W-2*p),plotH=Math.max(1,H-2*p);\n const unitsPerPx=Math.max((2*dx)/plotW,(2*dy)/plotH);\n const halfX=unitsPerPx*plotW/2,halfY=unitsPerPx*plotH/2;\n const xmin=100-halfX,xmax=100+halfX,ymin=100-halfY,ymax=100+halfY;\n const X=x=>p+(x-xmin)/(xmax-xmin)*plotW,\n       Y=y=>H-p-(y-ymin)/(ymax-ymin)*plotH,\n       cx=X(100),cy=Y(100);'''
if old_bounds not in s:
    raise SystemExit('RRG bounds block not found')
s = s.replace(old_bounds, new_bounds, 1)

# Append a final CSS override so older fixed-height rules cannot distort the RRG.
css_marker = '/* v24.2 RRG clarity + proportional canvas */'
if css_marker not in s:
    insert = '''\n/* v24.2 RRG clarity + proportional canvas */\n#sectorChart,#stockChart{width:100%!important;height:auto!important;aspect-ratio:3/2;display:block}\n@media(max-width:760px){#sectorChart,#stockChart{aspect-ratio:4/3;height:auto!important}}\n'''
    idx = s.find('</style>')
    if idx < 0:
        raise SystemExit('</style> not found')
    s = s[:idx] + insert + s[idx:]

# Redraw at the new device-pixel ratio after viewport/container changes.
resize_marker = '/* v24.2 responsive RRG redraw */'
if resize_marker not in s:
    hook = '''\n/* v24.2 responsive RRG redraw */\nlet _rrgResizeTimer=null;\nwindow.addEventListener("resize",()=>{\n clearTimeout(_rrgResizeTimer);\n _rrgResizeTimer=setTimeout(()=>{\n   ["sectorChart","stockChart","historyChart"].forEach(id=>{\n     const st=rrgFocusState[id];\n     if(st?.rows?.length)drawRRG(id,st.rows,st.selected);\n   });\n },120);\n});\n'''
    target = 'loadLiveWatchlist();renderLiveWatchlist();checkAlpacaStatus();loadMarket(false);'
    if target not in s:
        raise SystemExit('startup hook not found')
    s = s.replace(target, hook + '\n' + target, 1)

p.write_text(s)
print('patched app.py for v24.2')
