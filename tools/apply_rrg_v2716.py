from pathlib import Path
p=Path('app.py')
s=p.read_text()

def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.15"','APP_VERSION = "27.16"','version')

once('''let rrgTimelineDates=[],rrgTimelineIndex=null,rrgTimelineScale={fast:null,trend:null};''','''let rrgTimelineDates=[],rrgTimelineIndex=null,rrgTimelinePinnedDate=null,rrgTimelineScrubbing=false,rrgTimelineScale={fast:null,trend:null};''','timeline state')

once('''function setupRRGTimeline(){
 const previousDate=rrgTimelineIndex!=null?rrgTimelineDates[rrgTimelineIndex]:null;
 buildRRGTimelineDates();
 rrgTimelineScale={fast:computeStableScale("fast"),trend:computeStableScale("trend")};
 const bar=document.getElementById("rrgTimelineBar"),slider=document.getElementById("rrgTimelineSlider");
 if(!bar||!slider)return;
 if(rrgTimelineDates.length<2){bar.style.display="none";rrgTimelineIndex=null;return}
 bar.style.display="flex";
 slider.min="0";slider.max=String(rrgTimelineDates.length-1);
 let restored=null;
 if(previousDate){
   let idx=rrgTimelineDates.findIndex(d=>d===previousDate);
   if(idx<0){for(let i=rrgTimelineDates.length-1;i>=0;i--){if(rrgTimelineDates[i]<=previousDate){idx=i;break;}}}
   if(idx>=0&&idx<rrgTimelineDates.length-1)restored=idx;
 }
 rrgTimelineIndex=restored;
 slider.value=String(restored==null?rrgTimelineDates.length-1:restored);
 updateRRGTimelineLabel();
}
''','''function setupRRGTimeline(){
 // Preserve the user's chosen historical DATE independently of the array index.
 // Market refreshes replace the history arrays, so an index is not durable state.
 const previousDate=rrgTimelinePinnedDate || (rrgTimelineIndex!=null?rrgTimelineDates[rrgTimelineIndex]:null);
 buildRRGTimelineDates();
 rrgTimelineScale={fast:computeStableScale("fast"),trend:computeStableScale("trend")};
 const bar=document.getElementById("rrgTimelineBar"),slider=document.getElementById("rrgTimelineSlider");
 if(!bar||!slider)return;
 if(rrgTimelineDates.length<2){bar.style.display="none";rrgTimelineIndex=null;return}
 bar.style.display="flex";
 slider.min="0";slider.max=String(rrgTimelineDates.length-1);
 let restored=null;
 if(previousDate){
   let idx=rrgTimelineDates.findIndex(d=>d===previousDate);
   if(idx<0){for(let i=rrgTimelineDates.length-1;i>=0;i--){if(rrgTimelineDates[i]<=previousDate){idx=i;break;}}}
   if(idx>=0&&idx<rrgTimelineDates.length-1)restored=idx;
 }
 rrgTimelineIndex=restored;
 if(restored!=null)rrgTimelinePinnedDate=rrgTimelineDates[restored];
 else if(!rrgTimelineScrubbing)rrgTimelinePinnedDate=null;
 // Never let a background refresh move the physical thumb while the user is dragging.
 if(!rrgTimelineScrubbing)slider.value=String(restored==null?rrgTimelineDates.length-1:restored);
 updateRRGTimelineLabel();
}
''','setup timeline pinned date')

once('''   slider.addEventListener("input",()=>{
     const idx=Number(slider.value);
     rrgTimelineIndex=idx>=rrgTimelineDates.length-1?null:idx;
     updateRRGTimelineLabel();
     scheduleRRGTimelineChartRender();
   });
   slider.addEventListener("change",()=>{
     if(rrgTimelineFrame!=null){cancelAnimationFrame(rrgTimelineFrame);rrgTimelineFrame=null;}
     renderGroups();
   });
''','''   const beginScrub=()=>{rrgTimelineScrubbing=true;};
   const finishScrub=()=>{rrgTimelineScrubbing=false;};
   slider.addEventListener("pointerdown",beginScrub);
   slider.addEventListener("touchstart",beginScrub,{passive:true});
   slider.addEventListener("input",()=>{
     rrgTimelineScrubbing=true;
     const idx=Number(slider.value);
     if(idx>=rrgTimelineDates.length-1){
       rrgTimelineIndex=null;
       rrgTimelinePinnedDate=null;
     } else {
       rrgTimelineIndex=idx;
       rrgTimelinePinnedDate=rrgTimelineDates[idx]||rrgTimelinePinnedDate;
     }
     updateRRGTimelineLabel();
     scheduleRRGTimelineChartRender();
   });
   slider.addEventListener("change",()=>{
     finishScrub();
     const idx=Number(slider.value);
     if(idx>=rrgTimelineDates.length-1){rrgTimelineIndex=null;rrgTimelinePinnedDate=null;}
     else {rrgTimelineIndex=idx;rrgTimelinePinnedDate=rrgTimelineDates[idx]||rrgTimelinePinnedDate;}
     if(rrgTimelineFrame!=null){cancelAnimationFrame(rrgTimelineFrame);rrgTimelineFrame=null;}
     updateRRGTimelineLabel();
     renderGroups();
   });
   slider.addEventListener("pointerup",finishScrub);
   slider.addEventListener("pointercancel",finishScrub);
''','slider handlers pin date')

once('''   liveBtn.addEventListener("click",()=>{
     rrgTimelineIndex=null;
     if(slider)slider.value=String(rrgTimelineDates.length-1);
''','''   liveBtn.addEventListener("click",()=>{
     rrgTimelineScrubbing=false;
     rrgTimelineIndex=null;
     rrgTimelinePinnedDate=null;
     if(slider)slider.value=String(rrgTimelineDates.length-1);
''','live clears pin')

p.write_text(s)
print('patched app.py to v27.16')
