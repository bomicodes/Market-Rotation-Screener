from pathlib import Path

p=Path('app.py')
s=p.read_text()
old='''async function runAutomaticTopSetups(force=false){\n if(window.matchMedia&&window.matchMedia("(max-width: 760px)").matches){\n   const st=document.getElementById("topSetupStatus")||document.getElementById("topSetupsStatus");\n   if(st)st.textContent="Top Setups auto-scan paused on mobile to keep ticker analysis fast.";\n   return;\n }\n'''
new='''async function runAutomaticTopSetups(force=false){\n // On mobile, suppress only background/automatic scans. An explicit user tap\n // (force=true) is allowed now that the Render service has more headroom.\n const isMobile=!!(window.matchMedia&&window.matchMedia("(max-width: 760px)").matches);\n if(isMobile&&!force){\n   const st=document.getElementById("topSetupStatus")||document.getElementById("topSetupsStatus");\n   if(st)st.textContent="Top Setups ready · tap Load Top Setups to scan.";\n   return;\n }\n'''
if old not in s: raise SystemExit('mobile guard anchor not found')
s=s.replace(old,new,1)
# Keep holdings scan controlled: paid Render gives headroom, but do not restore the old burst behavior.
s=s.replace('for(let n=0;n<supportive.length;n+=4){\n     const batch=supportive.slice(n,n+4);','for(let n=0;n<supportive.length;n+=2){\n     const batch=supportive.slice(n,n+2);',1)
s=s.replace('`Layer 2 · scanned ${Math.min(n+4,supportive.length)}/${supportive.length} supportive groups`','`Layer 2 · scanned ${Math.min(n+2,supportive.length)}/${supportive.length} supportive groups`',1)
# Limit the expensive options stage; the strongest RRG candidates are sufficient for the final 1-2 picks.
s=s.replace('.sort((a,b)=>preliminaryRRGScore(b)-preliminaryRRGScore(a)).slice(0,100);','.sort((a,b)=>preliminaryRRGScore(b)-preliminaryRRGScore(a)).slice(0,60);',1)
# Add an explicit button into the Top Setups status area without changing the larger layout.
anchor='<span id="topSetupsStatus"'
pos=s.find(anchor)
if pos<0: raise SystemExit('topSetupsStatus anchor not found')
# Find the containing status span end and append button once.
end=s.find('</span>',pos)
if end<0: raise SystemExit('topSetupsStatus closing span not found')
if 'id="loadTopSetups"' not in s:
    end+=len('</span>')
    s=s[:end]+' <button class="secondary" id="loadTopSetups" type="button">Load Top Setups</button>'+s[end:]
# Wire the button near other dashboard listeners.
listener='''document.getElementById("loadTopSetups")?.addEventListener("click",async()=>{\n const b=document.getElementById("loadTopSetups");\n if(b)b.disabled=true;\n try{await runAutomaticTopSetups(true)}finally{if(b)b.disabled=false}\n});\n'''
needle='document.getElementById("dashRefreshMarket")?.addEventListener("click",()=>loadMarket(true));'
if needle not in s: raise SystemExit('dashboard listener anchor not found')
if listener not in s: s=s.replace(needle,needle+'\n'+listener,1)
s=s.replace('APP_VERSION = "25.2"','APP_VERSION = "25.3"',1)
p.write_text(s)
