from pathlib import Path

p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "25.34"' in s
s=s.replace('APP_VERSION = "25.34"','APP_VERSION = "25.35"',1)

old='''   toggleRRGFocus(id,ticker);\n   if(id==="stockChart"){\n     openSectorStockTicker(ticker,{scroll:true});\n   }\n   if(id==="sectorChart"){\n'''
new='''   toggleRRGFocus(id,ticker);\n   // Stock RRG clicks are inspection-only: focus the selected tail and dim the\n   // others in place. Opening the chart/volume-profile deep dive remains a\n   // separate action via the stock table/watchlist, so clicking the RRG no\n   // longer yanks the user away from the tail they are trying to inspect.\n   if(id==="sectorChart"){\n'''
assert old in s, 'stockChart click-open block not found'
s=s.replace(old,new,1)

# Keep the visible instruction aligned with the interaction contract.
old_tip='Tip: click a ticker row or chart label to focus it. All displayed tails stay visible; the others dim. Click the selected ticker again to clear.'
new_tip='Tip: click an RRG ticker label to focus its tail in place; other tails dim. Click again to clear. Use a stock table row to open the chart / volume-profile deep dive.'
if old_tip in s:
    s=s.replace(old_tip,new_tip,1)

p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
entry='''v25.35 — STOCK RRG CLICK = FOCUS ONLY\n- Fixed Stock RRG interaction so clicking a ticker label/endpoint now only highlights that stock's tail and dims the others in place.\n- Removed the automatic jump into the chart / Volume Profile from Stock RRG clicks. The stock table/watchlist remains the explicit path into the deep-dive panels.\n- Clicking the selected RRG ticker again still clears focus.\n\n'''
if not rs.startswith('v25.35'):
    r.write_text(entry+rs)
