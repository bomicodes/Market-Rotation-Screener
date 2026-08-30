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

# Remove direct DOM bindings for controls that are being retired. The old
# template is assembled from multiple raw-string fragments, so physically
# slicing that markup is riskier than removing the finished legacy view from
# the DOM once parsing is complete.
first='document.getElementById("histQuadrantFilter").addEventListener'
last='document.getElementById("histDate").value=new Date().toISOString().slice(0,10);'
bind_start=s.find(first)
assert bind_start>=0, 'historical bindings start not found'
bind_end=s.find(last,bind_start)
assert bind_end>=0, 'historical bindings end not found'
bind_end += len(last)
while bind_end < len(s) and s[bind_end] in '\r\n':
    bind_end += 1
replacement='// v27.17: main RRG timeline supersedes the standalone Historical RRG view.\ndocument.getElementById("history")?.remove();\n\n'
s=s[:bind_start]+replacement+s[bind_end:]

# Remove obsolete historical-canvas wiring only; sector/stock RRG stay intact.
s,n=re.subn(r'\ninstallRRGInteractions\("historyChart"\);','',s,count=1)
assert n==1, f'history interaction removal count={n}'
s,n=re.subn(r'\["sectorChart","stockChart","historyChart"\]\.forEach','["sectorChart","stockChart"].forEach',s,count=1)
assert n==1, f'history resize removal count={n}'

# Standalone UI is inaccessible/removed at runtime, while point-in-time backend
# capability and the main dashboard timeline remain available.
assert 'data-view="history"' not in s
assert 'document.getElementById("history")?.remove();' in s
assert 'installRRGInteractions("historyChart")' not in s
assert '["sectorChart","stockChart","historyChart"].forEach' not in s
assert '@app.get("/api/historical-rrg")' in s
assert 'rrgTimelineSlider' in s
assert 'rrgTimelinePinnedDate' in s
assert 'APP_VERSION = "27.17"' in s

p.write_text(s)
print(f'patched app.py: {len(orig)} -> {len(s)} bytes')
