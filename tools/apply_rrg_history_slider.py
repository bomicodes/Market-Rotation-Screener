from pathlib import Path

p=Path('app.py')
s=p.read_text()

def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.10"','APP_VERSION = "27.11"','version')

once('''RRG_STD_WINDOW = 63  # ~1 trading quarter; shared here and in compute_rrg's default
                      # so the warm-up guard below can't silently drift out of sync.
''','''RRG_STD_WINDOW = 63  # ~1 trading quarter; shared here and in compute_rrg's default
                      # so the warm-up guard below can't silently drift out of sync.
RRG_HISTORY_LEN = 200  # ~9-10 months of trading days for the main dashboard's
                        # timeline slider. Only the main sector/industry
                        # dashboard call passes this -- other rrg_rows/
                        # dual_rrg_rows callers (per-ticker deep-dives, sector
                        # drill-downs) leave history_len unset and see no
                        # payload growth.
''','history constant')

once('def rrg_rows(prices, bench_ticker, members, n1=10, n2=5, tail=8):','def rrg_rows(prices, bench_ticker, members, n1=10, n2=5, tail=8, history_len=None):','rrg_rows signature')

once('''        # before the FIRST valid momentum value exists, before even counting
        # the requested tail length.
        min_needed = max(2*RRG_STD_WINDOW + tail + 10, n1+n2+tail+5)
''','''        # before the FIRST valid momentum value exists, before even counting
        # the requested tail length (or the longer history_len, when a caller
        # -- the main dashboard's timeline slider -- asks for one).
        span = max(tail, history_len or 0)
        min_needed = max(2*RRG_STD_WINDOW + span + 10, n1+n2+span+5)
''','warmup span')

once('''        tail_pts = [{"x":float(ratio[i]),"y":float(mom[i])} for i in idx[-tail:]]
''','''        tail_pts = [{"x":float(ratio[i]),"y":float(mom[i]),"date":pair.index[i].strftime("%Y-%m-%d")} for i in idx[-tail:]]
        # Full scrubbable history for the timeline slider: same fixed-length-tail
        # concept as the live chart, but computed once server-side so the
        # frontend can pick any as-of date and slice its own trailing tail
        # locally instead of re-fetching per drag. Distinct from `tail_pts`
        # above (kept small for the always-on live chart payload); this is
        # only populated when a caller explicitly asks for it via history_len,
        # so existing endpoints that don't need it see no payload growth.
        history_pts = None
        if history_len:
            hist_idx = idx[-history_len:]
            history_pts = [{"x":float(ratio[i]),"y":float(mom[i]),"date":pair.index[i].strftime("%Y-%m-%d")} for i in hist_idx]
''','tail history')

once('''            "score":round(min(10,score),1),"tail":tail_pts,
''','''            "score":round(min(10,score),1),"tail":tail_pts,"history":history_pts,
''','history payload')

once('''def dual_rrg_rows(prices, bench_ticker, members, tail_fast=8, tail_trend=8):
    fast = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 10, 5, tail_fast)}
    trend = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 25, 12, tail_trend)}
''','''def dual_rrg_rows(prices, bench_ticker, members, tail_fast=8, tail_trend=8, history_len=None):
    fast = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 10, 5, tail_fast, history_len)}
    trend = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 25, 12, tail_trend, history_len)}
''','dual history')

once('''    prices=dl_prices(tickers,"18mo")
''','''    # Bumped from 18mo -> 3y so the RRG history slider (below) has enough
    # trading days to scrub back RRG_HISTORY_LEN periods even after the
    # z-score formula's own ~126-day (2*RRG_STD_WINDOW) warm-up is subtracted.
    prices=dl_prices(tickers,"3y")
''','market lookback')
once('rows=dual_rrg_rows(prices,"SPY",list(RRG_UNIVERSE),8,8)','rows=dual_rrg_rows(prices,"SPY",list(RRG_UNIVERSE),8,8,history_len=RRG_HISTORY_LEN)','market history call')

once('''.rrgFilterBar{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin:8px 0 8px;flex-wrap:wrap}
''','''.rrgFilterBar{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin:8px 0 8px;flex-wrap:wrap}
.rrgTimeline{display:flex;align-items:center;gap:10px;margin:0 0 10px;padding:8px 10px;border:1px solid #223349;border-radius:8px;background:#0a121a}
.rrgTimeline input[type=range]{flex:1;accent-color:#1d5bd8}
.rrgTimeline .rrgTimelineLabel{font-size:11px;color:#9fb0c2;white-space:nowrap;min-width:150px;text-align:right}
.rrgTimeline .secondary{padding:5px 10px;font-size:10px}
''','timeline css')

once('''        </div>
        <canvas id="sectorChart" width="900" height="650"></canvas>
''','''        </div>
        <div class="rrgTimeline" id="rrgTimelineBar" style="display:none">
          <span class="tiny">HISTORY</span>
          <input type="range" id="rrgTimelineSlider" min="0" max="0" value="0" step="1">
          <span class="rrgTimelineLabel" id="rrgTimelineLabel">Live</span>
          <button type="button" class="secondary" id="rrgTimelineLiveBtn" style="display:none">Back to live</button>
        </div>
        <canvas id="sectorChart" width="900" height="650"></canvas>
''','timeline html')

marker='''function filteredGroups(){
 let f=document.getElementById("groupFilter")?.value||"all";
 return sectorData.filter(x=>{
'''
block='''// --- RRG history timeline slider ---------------------------------------
// Reconstructs a historical "as of" snapshot from the fast/trend `history`
// arrays the backend now attaches to each sector row (only on the main
// dashboard call -- see RRG_HISTORY_LEN server-side). Matched by date string
// per ticker rather than by raw array index, since a per-ticker dropna() on
// the backend could in principle leave slightly different date sets between
// tickers (unlikely for sector ETFs sharing SPY's calendar, but cheap to be
// robust about rather than assume alignment).
function quadrantJS(x,y){
 if(x>=100&&y>=100)return "Leading";
 if(x<100&&y>=100)return "Improving";
 if(x<100&&y<100)return "Lagging";
 return "Weakening";
}
let rrgTimelineDates=[],rrgTimelineIndex=null;
function buildRRGTimelineDates(){
 rrgTimelineDates=[];
 for(const r of (sectorData||[])){
   const h=r.fast?.history;
   if(h&&h.length>rrgTimelineDates.length)rrgTimelineDates=h.map(p=>p.date);
 }
}
function historicalPointFor(history,dateStr,tailLen=8){
 if(!history||!history.length)return null;
 let idx=history.findIndex(p=>p.date===dateStr);
 if(idx===-1){for(let i=history.length-1;i>=0;i--){if(history[i].date<=dateStr){idx=i;break;}}}
 if(idx===-1)return null;
 const pt=history[idx],tail=history.slice(Math.max(0,idx-tailLen+1),idx+1);
 const prev=tail.length>=2?tail[tail.length-2]:null;
 return {x:pt.x,y:pt.y,quadrant:quadrantJS(pt.x,pt.y),tail,date:pt.date,
         rs_up:prev?pt.x>prev.x:null,mom_up:prev?pt.y>prev.y:null,tail_trajectory:null};
}
function sectorDataAsOf(dateStr){
 return (sectorData||[]).map(r=>{
   const f=historicalPointFor(r.fast?.history,dateStr)||r.fast;
   const t=r.trend?.history?(historicalPointFor(r.trend.history,dateStr)||r.trend):r.trend;
   return {...r,fast:{...r.fast,...f},trend:r.trend?{...r.trend,...t}:null,x:f?.x??r.x,y:f?.y??r.y,quadrant:f?.quadrant??r.quadrant};
 });
}
function setupRRGTimeline(){
 buildRRGTimelineDates();
 const bar=document.getElementById("rrgTimelineBar"),slider=document.getElementById("rrgTimelineSlider");
 if(!bar||!slider)return;
 if(rrgTimelineDates.length<2){bar.style.display="none";return}
 bar.style.display="flex";
 slider.min="0";slider.max=String(rrgTimelineDates.length-1);
 // Default to live (rightmost) on every fresh payload load, not wherever the
 // slider happened to be left -- a background refresh shouldn't silently
 // re-anchor the view to whatever historical date was being viewed before.
 slider.value=String(rrgTimelineDates.length-1);
 rrgTimelineIndex=null;
 updateRRGTimelineLabel();
}
function updateRRGTimelineLabel(){
 const label=document.getElementById("rrgTimelineLabel"),liveBtn=document.getElementById("rrgTimelineLiveBtn");
 const live=rrgTimelineIndex==null||rrgTimelineIndex>=rrgTimelineDates.length-1;
 if(label)label.textContent=live?"Live":`As of ${rrgTimelineDates[rrgTimelineIndex]}`;
 if(liveBtn)liveBtn.style.display=live?"none":"inline-block";
}
function installRRGTimelineHandlers(){
 const slider=document.getElementById("rrgTimelineSlider"),liveBtn=document.getElementById("rrgTimelineLiveBtn");
 if(slider&&!slider.dataset.wired){
   slider.dataset.wired="1";
   slider.addEventListener("input",()=>{
     const idx=Number(slider.value);
     rrgTimelineIndex=idx>=rrgTimelineDates.length-1?null:idx;
     updateRRGTimelineLabel();
     renderGroups();
   });
 }
 if(liveBtn&&!liveBtn.dataset.wired){
   liveBtn.dataset.wired="1";
   liveBtn.addEventListener("click",()=>{
     rrgTimelineIndex=null;
     if(slider)slider.value=String(rrgTimelineDates.length-1);
     updateRRGTimelineLabel();
     renderGroups();
   });
 }
}
function filteredGroups(source){
 let f=document.getElementById("groupFilter")?.value||"all";
 return (source||sectorData).filter(x=>{
'''
once(marker,block,'timeline js insertion')

once('''function renderGroups(){
 let data=filteredGroups();
''','''function renderGroups(){
 const viewingHistorical=rrgTimelineIndex!=null&&rrgTimelineDates[rrgTimelineIndex];
 const source=viewingHistorical?sectorDataAsOf(rrgTimelineDates[rrgTimelineIndex]):sectorData;
 let data=filteredGroups(source);
''','historical render source')

once(''' updateSelectedSectorCard(rrgFocusState["sectorChart"]?.selected||currentSector);
''',''' if(!viewingHistorical)updateSelectedSectorCard(rrgFocusState["sectorChart"]?.selected||currentSector);
''','historical selected card')

once('''function applyMarketPayload(j,fromCache=false){
 sectorData=j.sectors||[];
''','''function applyMarketPayload(j,fromCache=false){
 sectorData=j.sectors||[];
 setupRRGTimeline();
 installRRGTimelineHandlers();
''','timeline payload init')

p.write_text(s)
print('patched app.py to v27.11')
