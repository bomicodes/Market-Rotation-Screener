from pathlib import Path
import runpy

p=Path('scripts/patch_v25_1_speed_stability.py')
s=p.read_text()
old="pat=r'async function loadInstitutionalContext\\(ticker,parent=null,quiet=false\\)\\{.*?\\n\\}\\nfunction renderInstitutionalContext'"
new="pat=r'async function loadInstitutionalContext\\(ticker,parent=null,quiet=false\\)\\{.*?\\}function renderInstitutionalContext'"
if old not in s:
    raise SystemExit('old institutional matcher not found')
p.write_text(s.replace(old,new,1))
runpy.run_path(str(p),run_name='__main__')
