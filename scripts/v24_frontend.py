from pathlib import Path
p=Path('app.py'); s=p.read_text()
def rep(a,b,n):
    global s
    if a not in s: raise SystemExit('missing '+n)
    s=s.replace(a,b,1)
if 'id="sourceHealthStrip"' in s: raise SystemExit(0)
rep('.appNav .tab:hover,.navJump:hover{background:#0d1822;color:#f3f7fb}.appNav .tab.active{background:linear-gradient(180deg,#123024,#0e211a);color:#67ec92;box-shadow:inset 0 -2px #2edb71}.navIcon{font-size:15px;line-height:1}.versionPill{border-radius:8px;background:#092217;border-color:#176d3b;box-shadow:0 0 0 1px rgba(34,197,94,.05)}', '.appNav .tab:hover,.navJump:hover{background:#0d1822;color:#f3f7fb}.appNav .tab.active{background:linear-gradient(180deg,#123024,#0e211a);color:#67ec92;box-shadow:inset 0 -2px #2edb71}.navIcon{font-size:15px;line-height:1}.versionPill{border-radius:8px;background:#092217;border-color:#176d3b;box-shadow:0 0 0 1px rgba(34,197,94,.05)}\n.glossTerm{border-bottom:1px dotted #5b7a8f;cursor:help}\n.glossTooltip{position:fixed;z-index:9999;max-width:280px;background:#0f1a24;border:1px solid #2a4a5f;border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.45;color:#d7e6ef;box-shadow:0 8px 24px rgba(0,0,0,.45);display:none}\n.glossTooltip.show{display:block}\n.glossTooltip b{color:#7fd8ff;display:block;margin-bottom:3px;font-size:11px;letter-spacing:.02em}\n.sourceHealthStrip{display:flex;flex-wrap:wrap;gap:6px 10px;padding:6px 20px 0;font-size:10px;color:#7f97a8}\n.sourceHealthStrip .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;vertical-align:middle}\n.sourceHealthStrip .dot.ok{background:#2edb71}\n.sourceHealthStrip .dot.degraded{background:#f59e0b}\n.sourceHealthStrip .dot.unknown{background:#3a4a58}\n.sourceHealthStrip .src{cursor:default}', 'css')
rep('<div class="pageIntro"><h1>Market Rotation Screener</h1><div class="sub">Fast RRG (10/5) finds change; Trend RRG (25/12) confirms persistence.</div></div>', '<div class="pageIntro"><h1>Market Rotation Screener</h1><div class="sub">Fast <span class="glossTerm" data-gloss="RRG">RRG</span> (10/5) finds change; Trend <span class="glossTerm" data-gloss="RRG">RRG</span> (25/12) confirms persistence.</div></div>\n<div id="sourceHealthStrip" class="sourceHealthStrip"></div>', 'intro')
rep('''        <div id="dashboardBreadth" class="breadthList"></div>
      </div>
    </aside>''','''        <div id="dashboardBreadth" class="breadthList"></div>
      </div>
      <div class="panel">
        <div class="dashTopline"><span class="dashTitle">MACRO CALENDAR</span><span class="note">FOMC · CPI · Jobs</span></div>
        <div id="dashboardMacro" class="breadthList"></div>
      </div>
    </aside>''','macro card')
rep('''        <div class="vpLevelItem"><span class="vpSwatch vah"></span><span>VAH</span><strong id="vpVahTop">—</strong></div>
        <div class="vpLevelItem"><span class="vpSwatch poc"></span><span>POC</span><strong id="vpPocTop">—</strong></div>
        <div class="vpLevelItem"><span class="vpSwatch val"></span><span>VAL</span><strong id="vpValTop">—</strong></div>''','''        <div class="vpLevelItem"><span class="vpSwatch vah"></span><span class="glossTerm" data-gloss="VAH">VAH</span><strong id="vpVahTop">—</strong></div>
        <div class="vpLevelItem"><span class="vpSwatch poc"></span><span class="glossTerm" data-gloss="POC">POC</span><strong id="vpPocTop">—</strong></div>
        <div class="vpLevelItem"><span class="vpSwatch val"></span><span class="glossTerm" data-gloss="VAL">VAL</span><strong id="vpValTop">—</strong></div>''','vp')
rep('<div class="dashTitle">PRICE ACTION · STRAT</div>', '<div class="dashTitle">PRICE ACTION · <span class="glossTerm" data-gloss="STRAT">STRAT</span></div>', 'strat')
p.write_text(s)
