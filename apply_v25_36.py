from pathlib import Path

p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "25.35"' in s
s=s.replace('APP_VERSION = "25.35"','APP_VERSION = "25.36"',1)
anchor='''installRRGInteractions("sectorChart");\ninstallRRGInteractions("stockChart");\ninstallRRGInteractions("historyChart");\n'''
assert anchor in s
insert=anchor+'''\nfunction installStockSummaryFocusOnly(){\n document.addEventListener("click",evt=>{\n   const target=evt.target;\n   if(!target||!target.closest)return;\n   if(target.closest("button,a,input,select,textarea"))return;\n   const row=target.closest("[data-live-ticker]");\n   if(!row)return;\n   evt.preventDefault();\n   evt.stopImmediatePropagation();\n   const ticker=row.dataset.liveTicker;\n   if(ticker)toggleRRGFocus("stockChart",ticker);\n },true);\n}\ninstallStockSummaryFocusOnly();\n'''
s=s.replace(anchor,insert,1)
p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
entry='''v25.36 — STOCK RRG SUMMARY CLICK = FOCUS ONLY\n- Extended the v25.35 focus-only behavior to the Stock RRG summary rows themselves. Clicking a stock in the summary now only highlights that ticker on the Stock RRG and dims the others; it no longer bubbles into the deep-dive/Volume Profile navigation handler.\n- Interactive controls inside the row (buttons/links/inputs) are left alone. Clicking the selected stock again still clears the RRG focus.\n\n'''
if not rs.startswith('v25.36'):
    r.write_text(entry+rs)
