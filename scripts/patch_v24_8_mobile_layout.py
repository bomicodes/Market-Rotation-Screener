from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()
orig=s

s=re.sub(r'APP_VERSION = "24\.7"','APP_VERSION = "24.8"',s,count=1)

old_head='<thead><tr><th>#</th><th>Sector</th><th>Fast</th><th>Trend</th></tr></thead><tbody id="sectorRows"></tbody>'
new_head='<thead><tr><th>#</th><th>Sector</th><th>Fast</th><th>Trend</th><th>Signal</th></tr></thead><tbody id="sectorRows"></tbody>'
if old_head in s:
    s=s.replace(old_head,new_head,1)

marker='/* v24 Institutional Decision Layer */'
if marker not in s:
    raise SystemExit('CSS insertion marker not found')
css=r'''
/* v24.8 mobile dashboard + sector summary */
@media(max-width:760px){
  html,body{width:100%;max-width:100%;overflow-x:hidden}
  .wrap{width:100%;max-width:100%;padding:0 8px 18px}
  .appHeader{display:grid!important;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"brand meta" "nav nav";align-items:center!important;gap:8px 10px;margin:0 -8px 10px;padding:8px 10px;overflow:hidden}
  .brand{grid-area:brand;min-width:0!important;gap:8px;overflow:hidden}
  .brandMark{width:34px;height:34px;flex:0 0 34px;font-size:17px}
  .brandText{min-width:0;overflow:hidden}
  .brandText b{font-size:13px!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:.1px}
  .brandText span{display:none!important}
  .headerMeta{grid-area:meta;margin-left:0!important;gap:6px;white-space:nowrap}
  .headerRefresh{padding:6px 8px;font-size:9px}
  .versionPill{padding:5px 7px;font-size:9px}
  .appNav{grid-area:nav;order:unset!important;width:100%;max-width:100%;min-width:0;overflow-x:auto;overflow-y:hidden;display:flex;justify-content:flex-start;scrollbar-width:none;-webkit-overflow-scrolling:touch}
  .appNav::-webkit-scrollbar{display:none}
  .appNav .tab,.navJump{flex:0 0 auto;min-width:74px!important;padding:6px 7px;font-size:9px}
  .navIcon{font-size:13px}

  .dashboardGrid,.dashCol,.rrgShell,.dashRight,.panel,.sideSection{width:100%;max-width:100%;min-width:0}
  .dashboardGrid{gap:8px}
  .rrgShell>.panel{padding:12px}
  .rrgHeader{display:grid!important;grid-template-columns:1fr;gap:9px;align-items:start}
  .rrgHeader h2{font-size:16px;line-height:1.2;margin:0}
  .rrgControlStack{width:100%;align-items:stretch!important}
  .rrgToggle{display:grid!important;grid-template-columns:1fr 1fr;width:100%}
  .rrgToggle button{width:100%;min-height:42px}
  .rrgFilterBar{display:block!important;margin-top:10px}
  .rrgSelectFilters{display:grid!important;grid-template-columns:1fr!important;gap:8px;width:100%}
  .rrgSelectFilters label{width:100%;max-width:none!important;min-width:0!important}
  .rrgSelectFilters select{min-height:42px;font-size:12px;padding:8px 10px}
  .rrgInlineFilters{width:100%;margin:10px 0 0!important}
  .rrgInlineFilters .tiny{display:block;margin-bottom:6px}
  .rrgInlineFilters .filterPills{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;width:100%}
  .rrgInlineFilters .filterPill{width:100%;min-width:0;padding:7px 4px;font-size:9px;text-align:center}
  #sectorChart{width:100%!important;height:auto!important;aspect-ratio:1/1.05!important;min-height:0}

  .sectorSummaryPanel{height:auto!important;min-height:0!important;padding:10px!important;margin-top:8px!important}
  .sectorSummaryPanel .scroll{max-height:none!important;overflow:visible!important}
  .sectorSummaryPanel table,.sectorSummaryPanel tbody{display:block;width:100%}
  .sectorSummaryPanel thead{display:none}
  .sectorSummaryPanel tr.sectorTickerRow{display:grid;grid-template-columns:28px minmax(0,1fr);grid-template-areas:"rank sector" "rank fast" "rank trend" "rank signal";gap:5px 9px;padding:10px 2px;border-bottom:1px solid #1f3140;width:100%}
  .sectorSummaryPanel tr.sectorTickerRow:last-child{border-bottom:0}
  .sectorSummaryPanel tr.sectorTickerRow td{display:block!important;border:0!important;padding:0!important;min-width:0!important;width:auto!important}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(1){grid-area:rank;color:#9aa9b9;padding-top:2px!important}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2){grid-area:sector}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2)>b{font-size:14px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2) .tiny{font-size:10px;line-height:1.35;margin-top:2px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3){grid-area:fast;display:flex!important;align-items:center;gap:7px;flex-wrap:wrap}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(4){grid-area:trend;display:flex!important;align-items:center;gap:7px;flex-wrap:wrap}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(5){grid-area:signal;margin-top:1px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3)::before,.sectorSummaryPanel tr.sectorTickerRow td:nth-child(4)::before{font-size:8px;letter-spacing:.6px;color:#71859a;font-weight:900;min-width:48px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3)::before{content:"FAST 10/5"}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(4)::before{content:"TREND 25/12"}
  .sectorSummaryPanel .badge{font-size:9px;padding:3px 7px}
  .sectorSummaryPanel .flag{display:inline-block;font-size:9px}
  .sectorSummaryPanel td:nth-child(3) .tiny,.sectorSummaryPanel td:nth-child(4) .tiny{display:inline-block;margin:0;font-size:9px}
  .dashRight .sectorSummaryPanel .scroll{max-height:none!important}
}

@media(max-width:390px){
  .brandText b{font-size:12px!important}
  .headerRefresh{padding:6px 7px}
  .appNav .tab,.navJump{min-width:68px!important}
  .rrgInlineFilters .filterPills{grid-template-columns:repeat(2,minmax(0,1fr))}
}

'''
s=s.replace(marker,css+marker,1)

if s==orig:
    raise SystemExit('No changes made')
p.write_text(s)
print('patched app.py to v24.8 mobile layout')
