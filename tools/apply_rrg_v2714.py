from pathlib import Path
p=Path('app.py')
s=p.read_text()

def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.13"','APP_VERSION = "27.14"','version')

once('''RRG_HISTORY_LEN = 200  # ~9-10 months of trading days for the main dashboard's
                        # timeline slider. Only the main sector/industry
                        # dashboard call passes this -- other rrg_rows/
                        # dual_rrg_rows callers (per-ticker deep-dives, sector
                        # drill-downs) leave history_len unset and see no
                        # payload growth.
''','''RRG_HISTORY_LEN = 200  # ~9-10 months of daily observations for the main dashboard.
RRG_WEEKLY_HISTORY_LEN = 104  # ~2 years of true weekly observations for the 1W timeline.
''','history constants')

marker='''def compute_rrg(bench, asset, n1=10, n2=5, std_window=RRG_STD_WINDOW):
'''
insert='''def rrg_resample_prices(prices, timeframe="1d"):
    """Return the observation grid used by the RRG calculation.

    Fast/Trend remain sensitivity settings; timeframe controls the actual input
    observations. 1W therefore means weekly closes, not a slower smoothing
    preset applied to daily bars.
    """
    if timeframe != "1w":
        return prices
    if prices is None or len(prices) == 0:
        return prices
    out = prices.copy().sort_index()
    out.index = pd.DatetimeIndex(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    return out.resample("W-FRI").last().dropna(how="all")

'''+marker
once(marker,insert,'resample helper')

once('''def dual_rrg_rows(prices, bench_ticker, members, tail_fast=8, tail_trend=8, history_len=None):
    fast = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 10, 5, tail_fast, history_len)}
    trend = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 25, 12, tail_trend, history_len)}
''','''def dual_rrg_rows(prices, bench_ticker, members, tail_fast=8, tail_trend=8, history_len=None, timeframe="1d"):
    rrg_prices = rrg_resample_prices(prices, timeframe)
    fast = {r["ticker"]: r for r in rrg_rows(rrg_prices, bench_ticker, members, 10, 5, tail_fast, history_len)}
    trend = {r["ticker"]: r for r in rrg_rows(rrg_prices, bench_ticker, members, 25, 12, tail_trend, history_len)}
''','dual timeframe')

once('''    # Bumped from 18mo -> 3y so the RRG history slider (below) has enough
    # trading days to scrub back RRG_HISTORY_LEN periods even after the
    # z-score formula's own ~126-day (2*RRG_STD_WINDOW) warm-up is subtracted.
    prices=dl_prices(tickers,"3y")
''','''    # Six years supports both the 200-session daily timeline and a true weekly
    # RRG with a 63-observation volatility window plus ~2 years of history.
    prices=dl_prices(tickers,"6y")
''','market lookback')

once('''    rows=dual_rrg_rows(prices,"SPY",list(RRG_UNIVERSE),8,8,history_len=RRG_HISTORY_LEN)
    for r in rows:
        r["name"]=RRG_UNIVERSE.get(r["ticker"],r["ticker"])
        r["group"]="Core Sector" if r["ticker"] in SECTORS else "Industry / Theme"
        r["alignment"]=alignment_label(r.get("fast"), r.get("trend"))
''','''    rows=dual_rrg_rows(prices,"SPY",list(RRG_UNIVERSE),8,8,history_len=RRG_HISTORY_LEN,timeframe="1d")
    weekly_rows=dual_rrg_rows(prices,"SPY",list(RRG_UNIVERSE),8,8,history_len=RRG_WEEKLY_HISTORY_LEN,timeframe="1w")
    for collection in (rows, weekly_rows):
        for r in collection:
            r["name"]=RRG_UNIVERSE.get(r["ticker"],r["ticker"])
            r["group"]="Core Sector" if r["ticker"] in SECTORS else "Industry / Theme"
            r["alignment"]=alignment_label(r.get("fast"), r.get("trend"))
''','market daily weekly rows')

once('''    return {"asof":valid_index.max().strftime("%Y-%m-%d"),"internals":internals,"participation":participation,"risk_appetite":risk_appetite,"risk_score":risk_score,"sectors":rows}
''','''    return {"asof":valid_index.max().strftime("%Y-%m-%d"),"internals":internals,"participation":participation,"risk_appetite":risk_appetite,"risk_score":risk_score,"sectors":rows,"sectors_weekly":weekly_rows}
''','market payload weekly key')

once('''<div class="rrgHeader"><div><h2 id="dashboardRRGTitle">RRG LIVE · FAST ROTATION (10/5)</h2><div class="note">Benchmark: SPY · click a ticker to focus and load holdings</div></div><div class="rrgControlStack"><div class="rrgToggle"><button id="rrgFastBtn" class="active">FAST 10/5</button><button id="rrgTrendBtn">TREND 25/12</button></div></div></div>
''','''<div class="rrgHeader"><div><h2 id="dashboardRRGTitle">RRG LIVE · 1D · FAST ROTATION (10/5)</h2><div class="note">Benchmark: SPY · 1D/1W changes observation periodicity · Fast/Trend changes sensitivity</div></div><div class="rrgControlStack"><div class="rrgToggle"><button id="rrgDailyBtn" class="active" type="button" onclick="setSectorRRGTimeframe('1d')">1D</button><button id="rrgWeeklyBtn" type="button" onclick="setSectorRRGTimeframe('1w')">1W</button></div><div class="rrgToggle"><button id="rrgFastBtn" class="active">FAST 10/5</button><button id="rrgTrendBtn">TREND 25/12</button></div></div></div>
''','timeframe buttons')

once('''let sectorData=[],currentSector=null,earnResults=[],liveStockData=[],liveSearchData=[],liveSearchSector=null,liveSearchLoading=false,sectorRequestSeq=0,previewTicker=null,previewPeriod="1m",previewRequestSeq=0;
''','''let sectorData=[],sectorDataWeekly=[],currentSector=null,earnResults=[],liveStockData=[],liveSearchData=[],liveSearchSector=null,liveSearchLoading=false,sectorRequestSeq=0,previewTicker=null,previewPeriod="1m",previewRequestSeq=0;
''','weekly state data')
once('''let sectorRRGMode="fast", sectorQuadrantFilter="all", dashboardPayload=null, dashboardHeatMode="composite";
''','''let sectorRRGMode="fast", sectorRRGTimeframe="1d", sectorQuadrantFilter="all", dashboardPayload=null, dashboardHeatMode="composite";
''','timeframe state')

once('''function updateSelectedSectorCard(ticker){
 const el=document.getElementById("selectedSectorCard"); if(!el)return;
 const x=(sectorData||[]).find(r=>r.ticker===ticker);
''','''function activeSectorRRGData(){
 return sectorRRGTimeframe==="1w"&&sectorDataWeekly.length?sectorDataWeekly:sectorData;
}
function updateDashboardRRGTitle(){
 const ttl=document.getElementById("dashboardRRGTitle");if(!ttl)return;
 const tf=sectorRRGTimeframe==="1w"?"1W":"1D";
 ttl.textContent=sectorRRGMode==="fast"?`RRG LIVE · ${tf} · FAST ROTATION (10/5)`:`RRG LIVE · ${tf} · TREND (25/12)`;
}
function updateSelectedSectorCard(ticker){
 const el=document.getElementById("selectedSectorCard"); if(!el)return;
 const x=(activeSectorRRGData()||[]).find(r=>r.ticker===ticker);
''','active base helper')

once(''' const ttl=document.getElementById("dashboardRRGTitle");if(ttl)ttl.textContent=sectorRRGMode==="fast"?"RRG LIVE · FAST ROTATION (10/5)":"RRG LIVE · TREND (25/12)";
 renderGroups();
}
''',''' updateDashboardRRGTitle();
 renderGroups();
}
function setSectorRRGTimeframe(timeframe){
 sectorRRGTimeframe=timeframe==="1w"?"1w":"1d";
 document.getElementById("rrgDailyBtn")?.classList.toggle("active",sectorRRGTimeframe==="1d");
 document.getElementById("rrgWeeklyBtn")?.classList.toggle("active",sectorRRGTimeframe==="1w");
 updateDashboardRRGTitle();
 setupRRGTimeline();
 renderGroups();
 updateSelectedSectorCard(rrgFocusState["sectorChart"]?.selected||currentSector);
}
''','timeframe setter')

# Smooth visual interpolation only; data points/endpoints remain unchanged.
marker='''function drawRRG(id,rows,focusTicker=undefined,fixedScale=undefined){
'''
insert='''function smoothRRGPath(ctx,pts,X,Y){
 if(!pts.length)return;
 const xy=pts.map(p=>({x:X(p.x),y:Y(p.y)}));
 ctx.moveTo(xy[0].x,xy[0].y);
 if(xy.length<3){for(let i=1;i<xy.length;i++)ctx.lineTo(xy[i].x,xy[i].y);return;}
 const tension=.55;
 for(let i=0;i<xy.length-1;i++){
   const p0=xy[i-1]||xy[i],p1=xy[i],p2=xy[i+1],p3=xy[i+2]||p2;
   const k=tension/6;
   ctx.bezierCurveTo(p1.x+(p2.x-p0.x)*k,p1.y+(p2.y-p0.y)*k,p2.x-(p3.x-p1.x)*k,p2.y-(p3.y-p1.y)*k,p2.x,p2.y);
 }
}
'''+marker
once(marker,insert,'smooth path helper')
once('''   ctx.beginPath();
   pts.forEach((pt,j)=>{
     j?ctx.lineTo(X(pt.x),Y(pt.y)):ctx.moveTo(X(pt.x),Y(pt.y));
   });
   ctx.stroke();
''','''   ctx.beginPath();
   smoothRRGPath(ctx,pts,X,Y);
   ctx.stroke();
''','smooth draw')

once(''' const next=state.selected===ticker?null:ticker;
 drawRRG(id,state.rows,next);
''',''' const next=state.selected===ticker?null:ticker;
 const fixed=(id==="sectorChart"&&rrgTimelineIndex!=null)?rrgTimelineScale[sectorRRGMode]:undefined;
 drawRRG(id,state.rows,next,fixed);
''','focus fixed scale')

once('''function buildRRGTimelineDates(){
 rrgTimelineDates=[];
 for(const r of (sectorData||[])){
''','''function buildRRGTimelineDates(){
 rrgTimelineDates=[];
 for(const r of (activeSectorRRGData()||[])){
''','timeline active dates')
once(''' for(const r of (sectorData||[])){
   const h=mode==="trend"?r.trend?.history:r.fast?.history;
''',''' for(const r of (activeSectorRRGData()||[])){
   const h=mode==="trend"?r.trend?.history:r.fast?.history;
''','stable scale active data')
once('''function sectorDataAsOf(dateStr){
 return (sectorData||[]).map(r=>{
''','''function sectorDataAsOf(dateStr){
 return (activeSectorRRGData()||[]).map(r=>{
''','asof active data')

once('''function setupRRGTimeline(){
 buildRRGTimelineDates();
 rrgTimelineScale={fast:computeStableScale("fast"),trend:computeStableScale("trend")};
 const bar=document.getElementById("rrgTimelineBar"),slider=document.getElementById("rrgTimelineSlider");
 if(!bar||!slider)return;
 if(rrgTimelineDates.length<2){bar.style.display="none";return}
 bar.style.display="flex";
 slider.min="0";slider.max=String(rrgTimelineDates.length-1);
 slider.value=String(rrgTimelineDates.length-1);
 rrgTimelineIndex=null;
 updateRRGTimelineLabel();
}
''','''function setupRRGTimeline(){
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
''','preserve timeline position')

once('''function installRRGTimelineHandlers(){
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
''','''let rrgTimelineFrame=null;
function scheduleRRGTimelineChartRender(){
 if(rrgTimelineFrame!=null)cancelAnimationFrame(rrgTimelineFrame);
 rrgTimelineFrame=requestAnimationFrame(()=>{rrgTimelineFrame=null;renderGroups({chartOnly:true});});
}
function installRRGTimelineHandlers(){
 const slider=document.getElementById("rrgTimelineSlider"),liveBtn=document.getElementById("rrgTimelineLiveBtn");
 if(slider&&!slider.dataset.wired){
   slider.dataset.wired="1";
   slider.addEventListener("input",()=>{
     const idx=Number(slider.value);
     rrgTimelineIndex=idx>=rrgTimelineDates.length-1?null:idx;
     updateRRGTimelineLabel();
     scheduleRRGTimelineChartRender();
   });
   slider.addEventListener("change",()=>{
     if(rrgTimelineFrame!=null){cancelAnimationFrame(rrgTimelineFrame);rrgTimelineFrame=null;}
     renderGroups();
   });
 }
''','rAF slider')

once('''function filteredGroups(source){
 let f=document.getElementById("groupFilter")?.value||"all";
 return (source||sectorData).filter(x=>{
''','''function filteredGroups(source){
 let f=document.getElementById("groupFilter")?.value||"all";
 return (source||activeSectorRRGData()).filter(x=>{
''','filtered active source')
once('''function renderGroups(){
 const viewingHistorical=rrgTimelineIndex!=null&&rrgTimelineDates[rrgTimelineIndex];
 const source=viewingHistorical?sectorDataAsOf(rrgTimelineDates[rrgTimelineIndex]):sectorData;
 let data=filteredGroups(source);
''','''function renderGroups(opts={}){
 const viewingHistorical=rrgTimelineIndex!=null&&rrgTimelineDates[rrgTimelineIndex];
 const source=viewingHistorical?sectorDataAsOf(rrgTimelineDates[rrgTimelineIndex]):activeSectorRRGData();
 let data=filteredGroups(source);
''','render active source')
once(''' drawRRG("sectorChart",data,undefined,viewingHistorical?rrgTimelineScale[sectorRRGMode]:undefined);
 document.getElementById("sectorRows").innerHTML=data.map((x,k)=>''',''' drawRRG("sectorChart",data,undefined,viewingHistorical?rrgTimelineScale[sectorRRGMode]:undefined);
 if(opts.chartOnly)return;
 document.getElementById("sectorRows").innerHTML=data.map((x,k)=>''','chart-only scrub render')

once('''function applyMarketPayload(j,fromCache=false){
 sectorData=j.sectors||[];
 setupRRGTimeline();
''','''function applyMarketPayload(j,fromCache=false){
 sectorData=j.sectors||[];
 sectorDataWeekly=j.sectors_weekly||[];
 setupRRGTimeline();
''','weekly payload apply')

p.write_text(s)
print('patched app.py to v27.14')
