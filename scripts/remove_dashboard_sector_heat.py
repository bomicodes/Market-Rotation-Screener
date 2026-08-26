from pathlib import Path

p = Path('app.py')
s = p.read_text()

s = s.replace('APP_VERSION = "24.2"', 'APP_VERSION = "24.3"', 1)

old = '''      <div class="panel">\n        <div class="dashTopline"><span class="dashTitle">SECTOR ROTATION HEAT MAP</span><span class="note">Composite</span></div>\n        <div class="heatModeTabs" style="margin-bottom:8px"><button id="dashHeatComposite" class="active">Composite</button><button id="dashHeatFast">Fast 10/5</button><button id="dashHeatTrend">Trend 25/12</button></div>\n        <div id="dashboardHeatGrid" class="dashHeatGrid"></div>\n        <div class="heatScale"></div><div class="heatScaleLabels"><span>Weak</span><span>Neutral</span><span>Strong</span></div>\n      </div>\n'''
if old not in s:
    raise SystemExit('dashboard sector heat-map panel not found')
s = s.replace(old, '', 1)

marker = '/* v24.3 dashboard simplification */'
if marker not in s:
    idx = s.find('</style>')
    if idx < 0:
        raise SystemExit('</style> not found')
    s = s[:idx] + '''\n/* v24.3 dashboard simplification */\n/* Sector rotation heat map removed from Dashboard because Sector Summary + RRG already convey the same rotation signal. Dedicated Heat Map view remains available for deeper stock/sector triage. */\n''' + s[idx:]

p.write_text(s)
print('removed redundant dashboard sector heat map; dedicated heatmap view retained')
