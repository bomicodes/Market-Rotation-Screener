from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()
orig=s

s=s.replace('APP_VERSION = "27.16"','APP_VERSION = "27.17"',1)

# Remove the redundant Historical RRG nav button.
nav_pat=r'\n\s*<button class="tab" data-view="history"><span class="navIcon">⌁</span><span>RRG Historical</span></button>'
s,n=re.subn(nav_pat,'',s,count=1)
assert n==1, f'history nav removal count={n}'

# The app template is assembled from concatenated raw strings, so remove the
# old top-level view by stable semantic markers rather than one large regex.
hist_anchor=s.find('Historical RRG · point-in-time replay')
assert hist_anchor>=0, 'historical view anchor not found'
hist_start=s.rfind('<div id=',0,hist_anchor)
assert hist_start>=0, 'historical view start not found'
gex_anchor=s.find('GEX LANDSCAPE',hist_anchor)
assert gex_anchor>=0, 'GEX anchor not found'
gex_start=s.rfind('<div id=',hist_anchor,gex_anchor)
assert gex_start>hist_start, f'bad view boundaries {hist_start=} {gex_start=}'
s=s[:hist_start]+s[gex_start:]

# Remove direct DOM bindings for controls that no longer exist.
first='document.getElementById("histQuadrantFilter").addEventListener'
last='document.getElementById("histDate").value=new Date().toISOString().slice(0,10);'
bind_start=s.find(first)
assert bind_start>=0, 'historical bindings start not found'
bind_end=s.find(last,bind_start)
assert bind_end>=0, 'historical bindings end not found'
bind_end += len(last)
while bind_end < len(s) and s[bind_end] in '\r\n':
    bind_end += 1
s=s[:bind_start]+s[bind_end:]

# Remove obsolete historical-canvas wiring only; sector/stock RRG stay intact.
s,n=re.subn(r'\ninstallRRGInteractions\("historyChart"\);','',s,count=1)
assert n==1, f'history interaction removal count={n}'
s,n=re.subn(r'\["sectorChart","stockChart","historyChart"\]\.forEach','["sectorChart","stockChart"].forEach',s,count=1)
assert n==1, f'history resize removal count={n}'

# Standalone UI is gone, while point-in-time backend and main timeline survive.
assert 'data-view="history"' not in s
assert 'Historical RRG · point-in-time replay' not in s
assert 'id="historyChart"' not in s
assert '@app.get("/api/historical-rrg")' in s
assert 'rrgTimelineSlider' in s
assert 'rrgTimelinePinnedDate' in s
assert 'APP_VERSION = "27.17"' in s

p.write_text(s)
print(f'patched app.py: {len(orig)} -> {len(s)} bytes')
