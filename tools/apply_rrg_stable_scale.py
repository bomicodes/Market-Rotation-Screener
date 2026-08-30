from pathlib import Path
p=Path('app.py')
s=p.read_text()

def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1: raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.11"','APP_VERSION = "27.12"','version')
once('function drawRRG(id,rows,focusTicker=undefined){','function drawRRG(id,rows,focusTicker=undefined,fixedScale=undefined){','draw sig')
once(''' let xs=[],ys=[];
 rows.forEach(r=>(r.tail||[]).forEach(pt=>{if(Number.isFinite(Number(pt.x)))xs.push(Number(pt.x));if(Number.isFinite(Number(pt.y)))ys.push(Number(pt.y));}));
 // Keep 100/100 exactly centered and use ONE pixels-per-RRG-unit scale for
 // both axes. This preserves tail angles and quadrant geometry instead of
 // stretching X and Y independently to fill the panel.
 const dx=Math.max(2,...xs.map(v=>Math.abs(v-100)))+.65;
 const dy=Math.max(2,...ys.map(v=>Math.abs(v-100)))+.65;
''',''' // Axis scale: normally auto-fit to whatever's in `rows` this call (live
 // view, unchanged). While scrubbing the history timeline, a fixedScale is
 // passed in instead -- each scrub position only carries one day's 8-point
 // tail, and that day's spread naturally differs slightly from the next
 // day's, so recomputing bounds fresh on every drag frame rescaled the
 // WHOLE chart every frame. A stable scale computed once across the full
 // scrubbable range keeps the grid fixed while the dots/tails move.
 let dx,dy;
 if(fixedScale&&Number.isFinite(fixedScale.dx)&&Number.isFinite(fixedScale.dy)){
   dx=fixedScale.dx;dy=fixedScale.dy;
 } else {
   let xs=[],ys=[];
   rows.forEach(r=>(r.tail||[]).forEach(pt=>{if(Number.isFinite(Number(pt.x)))xs.push(Number(pt.x));if(Number.isFinite(Number(pt.y)))ys.push(Number(pt.y));}));
   // Keep 100/100 exactly centered and use ONE pixels-per-RRG-unit scale for
   // both axes. This preserves tail angles and quadrant geometry instead of
   // stretching X and Y independently to fill the panel.
   dx=Math.max(2,...xs.map(v=>Math.abs(v-100)))+.65;
   dy=Math.max(2,...ys.map(v=>Math.abs(v-100)))+.65;
 }
''','axis block')
once('let rrgTimelineDates=[],rrgTimelineIndex=null;','let rrgTimelineDates=[],rrgTimelineIndex=null,rrgTimelineScale={fast:null,trend:null};','timeline state')
once('''function historicalPointFor(history,dateStr,tailLen=8){
''','''function computeStableScale(mode){
 let xs=[],ys=[];
 for(const r of (sectorData||[])){
   const h=mode==="trend"?r.trend?.history:r.fast?.history;
   (h||[]).forEach(p=>{if(Number.isFinite(p.x))xs.push(p.x);if(Number.isFinite(p.y))ys.push(p.y);});
 }
 if(!xs.length||!ys.length)return null;
 return {dx:Math.max(2,...xs.map(v=>Math.abs(v-100)))+.65,dy:Math.max(2,...ys.map(v=>Math.abs(v-100)))+.65};
}
function historicalPointFor(history,dateStr,tailLen=8){
''','stable scale fn')
once('''function setupRRGTimeline(){
 buildRRGTimelineDates();
''','''function setupRRGTimeline(){
 buildRRGTimelineDates();
 rrgTimelineScale={fast:computeStableScale("fast"),trend:computeStableScale("trend")};
''','setup scale')
once('drawRRG("sectorChart",data);','drawRRG("sectorChart",data,undefined,viewingHistorical?rrgTimelineScale[sectorRRGMode]:undefined);','draw historical fixed')
p.write_text(s)
print('patched app.py to v27.12')
