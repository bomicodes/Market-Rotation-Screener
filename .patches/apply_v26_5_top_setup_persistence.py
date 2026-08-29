from pathlib import Path

p=Path('app.py')
s=p.read_text()

# Roll the user's v26.4 direction-agreement correction into this deploy, then
# add an invisible persistence guard for the existing Top Setups cards.
s=s.replace('APP_VERSION = "26.3"','APP_VERSION = "26.5"',1)

old=""" const confirmed=(va?.strength==='CONFIRMED')&&(st?.continuity==='bullish'||st?.continuity==='bearish')&&(fIn||tIn);"""
new=""" // Value Acceptance and STRAT must agree on direction. Merely having a\n // bullish/bearish STRAT continuity value is not enough for CONFIRMED.\n const directionAgrees=va?.direction&&st?.continuity&&va.direction===st.continuity;\n const confirmed=(va?.strength==='CONFIRMED')&&directionAgrees&&(fIn||tIn);"""
assert old in s, 'v26.3 confirmed-lane expression not found'
s=s.replace(old,new,1)

needle='</script>\n"""\n@app.errorhandler(500)'
assert needle in s, 'HTML script terminator not found'
addon=r'''// v26.5: keep the existing Top Setups presentation stable across transient
// empty refreshes / overnight and weekend rescans. This intentionally adds no
// card labels or status UI; it only preserves the last non-empty rendered board.
const V265_TOP_SETUP_CACHE='top-setups-last-nonempty-v265';
function v265CacheTopSetups(){
 try{
   const rows=Array.isArray(globalTopSetupData)?globalTopSetupData:[];
   const grid=document.getElementById('topSetupsGrid');
   if(!rows.length||!grid||!grid.innerHTML.trim())return;
   localStorage.setItem(V265_TOP_SETUP_CACHE,JSON.stringify({savedAt:Date.now(),rows:rows,html:grid.innerHTML}));
 }catch(e){}
}
function v265RestoreTopSetups(){
 try{
   if(Array.isArray(globalTopSetupData)&&globalTopSetupData.length)return false;
   const raw=localStorage.getItem(V265_TOP_SETUP_CACHE);if(!raw)return false;
   const saved=JSON.parse(raw),age=Date.now()-Number(saved?.savedAt||0);
   // Four calendar days safely spans a Fri-close -> Mon-premarket gap while
   // preventing an old board from lingering indefinitely.
   if(!Array.isArray(saved?.rows)||!saved.rows.length||!saved.html||age<0||age>96*60*60*1000){
     localStorage.removeItem(V265_TOP_SETUP_CACHE);return false;
   }
   const grid=document.getElementById('topSetupsGrid');if(!grid)return false;
   globalTopSetupData=saved.rows;
   grid.innerHTML=saved.html;
   return true;
 }catch(e){return false;}
}
const _runAutomaticTopSetupsV265=runAutomaticTopSetups;
runAutomaticTopSetups=async function(force=false){
 try{
   const out=await _runAutomaticTopSetupsV265(force);
   if(Array.isArray(globalTopSetupData)&&globalTopSetupData.length){setTimeout(v265CacheTopSetups,0);}
   else{v265RestoreTopSetups();}
   return out;
 }catch(e){
   if(v265RestoreTopSetups())return null;
   throw e;
 }
};
'''
s=s.replace(needle,addon+'\n'+needle,1)
p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
entry='''v26.5 — STABILIZE TOP SETUPS ACROSS EMPTY REFRESHES\n- Preserves the last non-empty Top Setups board in browser storage and silently restores it when a later scan unexpectedly returns zero or throws, so overnight/weekend refreshes do not make valid setups disappear at random.\n- No card UI changes: no carried/ready labels, timestamps, or extra status text were added. A normal non-empty scan immediately replaces the saved board.\n- Saved boards expire after four calendar days so stale setups cannot linger indefinitely across multiple sessions.\n- Includes the v26.4 direction-agreement fix: CONFIRMED now requires Value Acceptance direction to equal STRAT continuity direction before receiving confirmed-lane rank weight.\n\n'''
r.write_text(entry+rs)
