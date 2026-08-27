from pathlib import Path

p = Path("app.py")
s = p.read_text()

old_version = 'APP_VERSION = "25.24"'
assert old_version in s
s = s.replace(old_version, 'APP_VERSION = "25.25"', 1)

old_helper = '''function safeScrollIntoView(el,{smooth=false}={}){
 if(!el)return false;
 try{
   // Avoid the WebKit overload that can throw the opaque DOMException
   // “The string did not match the expected pattern.”
   el.scrollIntoView(smooth);
   return true;
 }catch(e){
   try{el.scrollIntoView();return true;}catch(_e){return false;}
 }
}'''
new_helper = '''function safeScrollIntoView(el,{smooth=false}={}){
 if(!el)return false;
 try{
   // Prefer standards-based top alignment. If an older WebKit build rejects
   // the options overload, immediately fall back to the legacy Boolean form.
   el.scrollIntoView({behavior:smooth?"smooth":"auto",block:"start"});
   return true;
 }catch(e){
   try{el.scrollIntoView(true);return true;}catch(_e){return false;}
 }
}'''
assert old_helper in s
s = s.replace(old_helper, new_helper, 1)

old_watch = '''   document.querySelectorAll("[data-watch-open]").forEach(row=>row.addEventListener("click",evt=>{
     if(evt.target.closest("[data-live-watch-remove]"))return;
     const t=row.dataset.watchOpen;if(!t)return;
     loadChartPreview(t);
     loadStrat(t);
     if(alpacaConfigured!==false)loadOptionsTicker(t,{scroll:false});
   }));'''
new_watch = '''   document.querySelectorAll("[data-watch-open]").forEach(row=>row.addEventListener("click",evt=>{
     if(evt.target.closest("[data-live-watch-remove]"))return;
     const t=row.dataset.watchOpen;if(!t)return;
     // Use the same isolated, stale-safe opener as RRG rows and heat-map tiles.
     Promise.resolve(openSectorStockTicker(t,{scroll:true}))
       .catch(e=>console.warn("Watchlist ticker open failed",e));
   }));'''
assert old_watch in s
s = s.replace(old_watch, new_watch, 1)

old_options = '''setTimeout(()=>{try{const el=document.getElementById("optionsPanel");if(el)el.scrollIntoView()}catch(e){}},100);'''
old_chart = '''setTimeout(()=>{try{const el=document.getElementById("stockDeepDiveAnchor")||document.getElementById("pricePreviewChart");if(el)el.scrollIntoView()}catch(e){}},100);'''
assert old_options in s
assert old_chart in s
s = s.replace(old_options, '''setTimeout(()=>{safeScrollIntoView(document.getElementById("optionsPanel"))},100);''', 1)
s = s.replace(old_chart, '''setTimeout(()=>{safeScrollIntoView(document.getElementById("stockDeepDiveAnchor")||document.getElementById("pricePreviewChart"))},100);''', 1)

p.write_text(s)
