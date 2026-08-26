from pathlib import Path
import runpy
p=Path('scripts/patch_v25_1_speed_stability.py')
s=p.read_text()
# Compact one-line institutional loader matcher.
s=s.replace("pat=r'async function loadInstitutionalContext\\(ticker,parent=null,quiet=false\\)\\{.*?\\n\\}\\nfunction renderInstitutionalContext'","pat=r'async function loadInstitutionalContext\\(ticker,parent=null,quiet=false\\)\\{.*?\\}\\}\\nfunction renderInstitutionalContext'",1)
# The extracted STRAT body is already indented one function level after the de-indent loop.
old="'''+ '\\n'.join('    '+x if x else '' for x in core.splitlines()) + '''"
new="'''+ core + '''"
if old not in s: raise SystemExit('STRAT indentation builder marker not found')
p.write_text(s.replace(old,new,1))
runpy.run_path(str(p),run_name='__main__')
