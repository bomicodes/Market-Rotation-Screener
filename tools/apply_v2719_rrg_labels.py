from pathlib import Path
p=Path('app.py')
s=p.read_text(); orig=s
repls={
'APP_VERSION = "27.18"':'APP_VERSION = "27.19"',
'RRG LIVE · 1D · FAST ROTATION (10/5)':'RRG LIVE · 1D · EARLY ROTATION (10/5)',
'Benchmark: SPY · 1D/1W changes observation periodicity · Fast/Trend changes sensitivity':'Benchmark: SPY · Timeframe changes observation periodicity · Sensitivity changes how early the rotation responds',
'<div class="rrgControlStack"><div class="rrgToggle"><button id="rrgDailyBtn" class="active" type="button" onclick="setSectorRRGTimeframe(\'1d\')">1D</button><button id="rrgWeeklyBtn" type="button" onclick="setSectorRRGTimeframe(\'1w\')">1W</button></div><div class="rrgToggle"><button id="rrgFastBtn" class="active">FAST 10/5</button><button id="rrgTrendBtn">TREND 25/12</button></div></div>':'<div class="rrgControlStack"><div><div class="tiny" style="text-align:right;margin-bottom:3px">TIMEFRAME</div><div class="rrgToggle"><button id="rrgDailyBtn" class="active" type="button" onclick="setSectorRRGTimeframe(\'1d\')">1D</button><button id="rrgWeeklyBtn" type="button" onclick="setSectorRRGTimeframe(\'1w\')">1W</button></div></div><div><div class="tiny" style="text-align:right;margin-bottom:3px">SENSITIVITY</div><div class="rrgToggle"><button id="rrgFastBtn" class="active" title="10/5 · reacts sooner to relative-strength turns">EARLY</button><button id="rrgTrendBtn" title="25/12 · slower confirmation of persistent rotation">CONFIRMED</button></div></div></div>',
'<div class="sscLabel">Fast 10/5</div>':'<div class="sscLabel">Early · 10/5</div>',
'<div class="sscLabel">Trend 25/12</div>':'<div class="sscLabel">Confirmed · 25/12</div>',
'Fast finds the turn; Trend checks whether it is persisting.':'Early finds the turn; Confirmed checks whether it is persisting.',
'<span class="note">Fast + Trend</span>':'<span class="note">Early + Confirmed</span>',
'<th>Fast</th><th>Trend</th>':'<th>Early</th><th>Confirmed</th>',
'<th>Score</th><th>Fast</th><th>Trend</th>':'<th>Score</th><th>Early</th><th>Confirmed</th>',
'<select id="rrgMetricsMode"><option value="fast">Fast</option><option value="trend">Trend</option></select>':'<select id="rrgMetricsMode"><option value="fast">Early · 10/5</option><option value="trend">Confirmed · 25/12</option></select>',
}
for old,new in repls.items():
    assert old in s, f'missing: {old[:90]}'
    s=s.replace(old,new)
# Dynamic title uses the internal mode keys; keep those keys unchanged but make display terminology intuitive.
s=s.replace('sectorRRGMode==="trend"?"TREND ROTATION (25/12)":"FAST ROTATION (10/5)"','sectorRRGMode==="trend"?"CONFIRMED ROTATION (25/12)":"EARLY ROTATION (10/5)"')
s=s.replace('sectorRRGMode==="trend"?"Trend 25/12":"Fast 10/5"','sectorRRGMode==="trend"?"Confirmed · 25/12":"Early · 10/5"')
assert s!=orig
p.write_text(s)
print('patched',len(orig),'->',len(s))
