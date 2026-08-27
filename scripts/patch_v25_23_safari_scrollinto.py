from pathlib import Path
p=Path('app.py')
s=p.read_text()
old='APP_VERSION = "25.22"'
assert old in s
s=s.replace(old,'APP_VERSION = "25.23"',1)
# Safari/WebKit can throw SyntaxError DOMException "The string did not match the expected pattern"
# from Element.scrollIntoView(options) in some versions. The error was being caught by stock/watchlist
# workflows and incorrectly displayed as if the data request itself failed. Use a compatibility helper.
anchor='function focusOptionsPanel(){\n const panel=document.getElementById("optionsPanel");\n if(panel)panel.scrollIntoView({behavior:"smooth",block:"start"});\n}'
replacement='''function safeScrollIntoView(el,{smooth=false}={}){\n if(!el)return false;\n try{\n   // Avoid the WebKit overload that can throw the opaque DOMException\n   // “The string did not match the expected pattern.”\n   el.scrollIntoView(smooth);\n   return true;\n }catch(e){\n   try{el.scrollIntoView();return true;}catch(_e){return false;}\n }\n}\nfunction focusOptionsPanel(){\n const panel=document.getElementById("optionsPanel");\n safeScrollIntoView(panel,{smooth:true});\n}'''
assert anchor in s
s=s.replace(anchor,replacement,1)
# Replace remaining direct scrollIntoView calls with the Safari-safe wrapper.
s=s.replace('try{if(el)el.scrollIntoView()}catch(e){}','safeScrollIntoView(el)',1)
s=s.replace('try{document.getElementById("stockHeatTitle")?.scrollIntoView()}catch(e){}','safeScrollIntoView(document.getElementById("stockHeatTitle"))',1)
s=s.replace('if(el)el.scrollIntoView();','safeScrollIntoView(el);',1)
s=s.replace('try{mountGexPage();const el=document.getElementById("positioningSection");if(el)el.scrollIntoView()}catch(e){}','try{mountGexPage();safeScrollIntoView(document.getElementById("positioningSection"))}catch(e){}',1)
# Also make loadSector URL construction use the same scalar-only URL helper pattern.
old2='''   const params=new URLSearchParams({limit:String(lim)});\n   const url=`/api/sector/${encodeURIComponent(requestedSector)}?${params.toString()}`;\n   const r=await fetch(url,{headers:{"Accept":"application/json"}});'''
new2='''   const url=safeTickerUrl("/api/sector",requestedSector,{limit:String(lim)});\n   const r=await window.fetch(url,{method:"GET",credentials:"same-origin",headers:{"Accept":"application/json"}});'''
assert old2 in s
s=s.replace(old2,new2,1)
# Search-all sector fetch should use the same path helper too.
s=s.replace('const r=await fetch(`/api/sector/${currentSector}?limit=all`);','const r=await window.fetch(safeTickerUrl("/api/sector",currentSector,{limit:"all"}),{method:"GET",credentials:"same-origin",headers:{"Accept":"application/json"}});',1)
p.write_text(s)
