from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()
orig=s

# Version bump only; historical RRG math/backend is intentionally retained.
s=s.replace('APP_VERSION = "27.16"','APP_VERSION = "27.17"',1)

# Remove the redundant top-level Historical RRG navigation tab.
nav_pat=r'\n\s*<button class="tab" data-view="history"><span class="navIcon">⌁</span><span>RRG Historical</span></button>'
s,n=re.subn(nav_pat,'',s,count=1)
assert n==1, f'history nav removal count={n}'

# Remove only the standalone historical RRG view. The backend endpoint and
# main-dashboard timeline/history arrays remain untouched.
view_pat=r'\n<div id="history" class="view">.*?(?=\n<div id="[^"]+" class="view">)'
s,n=re.subn(view_pat,'\n',s,count=1,flags=re.S)
assert n==1, f'history view removal count={n}'

# Remove direct DOM bindings for controls that no longer exist. Historical
# helper functions/API code may remain dormant for future backtest reuse.
bind_pat=(r'document\.getElementById\("histQuadrantFilter"\)\.addEventListener.*?'
          r'document\.getElementById\("histDate"\)\.value=new Date\(\)\.toISOString\(\)\.slice\(0,10\);\n')
s,n=re.subn(bind_pat,'',s,count=1,flags=re.S)
assert n==1, f'history binding removal count={n}'

# Remove the obsolete canvas interaction registration; stock/sector remain.
s,n=re.subn(r'\ninstallRRGInteractions\("historyChart"\);','',s,count=1)
assert n==1, f'history interaction removal count={n}'

# Remove historical canvas from resize redraw list only.
s,n=re.subn(r'\["sectorChart","stockChart","historyChart"\]\.forEach','["sectorChart","stockChart"].forEach',s,count=1)
assert n==1, f'history resize removal count={n}'

# The standalone UI is gone, but point-in-time backend capability and main
# dashboard timeline must remain intact.
assert 'data-view="history"' not in s
assert '<div id="history" class="view">' not in s
assert 'id="historyChart"' not in s
assert '@app.get("/api/historical-rrg")' in s
assert 'rrgTimelineSlider' in s
assert 'rrgTimelinePinnedDate' in s
assert 'APP_VERSION = "27.17"' in s

p.write_text(s)
print(f'patched app.py: {len(orig)} -> {len(s)} bytes')
