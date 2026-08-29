from pathlib import Path

p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "27.4"' in s
s=s.replace('APP_VERSION = "27.4"','APP_VERSION = "27.5"',1)

# Remove the old compact news CSS block from the orphaned stylesheet.
old_news='''.newsContextGrid{display:grid;grid-template-columns:1fr 1.25fr;gap:14px}.newsCol{min-width:0}.newsItem{padding:8px 0;border-bottom:1px solid #202936}.newsItem:last-child{border-bottom:none}.newsHeadline{font-size:12px;line-height:1.35;color:#e5e7eb}.newsMeta{font-size:10px;color:#64748b;margin-top:3px}.newsWhy{font-size:11px;color:#9fb0c2;margin-top:4px;line-height:1.35}.newsTicker{display:inline-block;font-size:10px;font-weight:800;color:#bfdbfe;border:1px solid #334155;border-radius:999px;padding:2px 6px;margin-right:5px}.newsCat{font-size:9px;color:#c4b5fd;text-transform:uppercase;letter-spacing:.4px}@media(max-width:800px){.newsContextGrid{grid-template-columns:1fr}}'''
assert s.count(old_news)==1, s.count(old_news)
s=s.replace(old_news,'',1)

# Insert complete news styling into the live app stylesheet immediately after
# the Institutional Decision Layer rules and before that stylesheet closes.
anchor='/* v24 Institutional Decision Layer */'
start=s.index(anchor)
style_end=s.index('</style>', start)
news_css='''/* News + Catalyst Context */
.newsContextGrid{display:grid;grid-template-columns:1fr 1.25fr;gap:14px}
.newsCol{min-width:0}
.newsItem{padding:8px 0;border-bottom:1px solid #202936}
.newsItem:last-child{border-bottom:none}
.newsHeadline{font-size:12px;line-height:1.35;color:#e5e7eb}
a.newsHeadline{color:#e5e7eb;text-decoration:none;border-bottom:1px solid #33415580}
a.newsHeadline:visited{color:#e5e7eb}
a.newsHeadline:hover{color:#7fd8ff;border-bottom-color:#7fd8ff}
.newsMeta{font-size:10px;color:#64748b;margin-top:3px}
.newsWhy{font-size:11px;color:#9fb0c2;margin-top:4px;line-height:1.35}
.newsTicker{display:inline-block;font-size:10px;font-weight:800;color:#bfdbfe;border:1px solid #334155;border-radius:999px;padding:2px 6px;margin-right:5px}
.newsCat{font-size:9px;color:#c4b5fd;text-transform:uppercase;letter-spacing:.4px}
@media(max-width:800px){.newsContextGrid{grid-template-columns:1fr}}
'''
s=s[:style_end]+news_css+s[style_end:]

old=''' if(!(liq==="Liquid"||liq==="Tradable"))gateFailures.push("Options not liquid/tradable");'''
new=''' if(!(liq==="Liquid"||liq==="Tradable")){
   const checked=Number(opt?.contracts_checked);
   const tradableCount=Number(opt?.tradable_contracts);
   if(!opt)gateFailures.push("No options data returned");
   else if(Number.isFinite(checked)&&checked===0)gateFailures.push("No option contracts in the 7–35 DTE window");
   else if(Number.isFinite(tradableCount))gateFailures.push(`Illiquid chain · only ${tradableCount} of ${Number.isFinite(checked)?checked:"?"} contracts tradable (needs 3+ with tight spread + real OI/volume)`);
   else gateFailures.push("Options chain too thin to trade");
 }'''
assert old in s
s=s.replace(old,new,1)

p.write_text(s)

r=Path('README.txt')
readme=r.read_text()
assert readme.startswith('v27.4')
entry='''v27.5 — NEWS STYLESHEET FIX + SPECIFIC LIQUIDITY FAILURE REASONS
- Root-caused the unreadable blue news headlines: the News + Catalyst Context CSS lived in an orphaned stylesheet outside the served app template, so the panel reached the browser without those rules. Moved the complete news styling into the live stylesheet and removed the orphaned copy.
- Added anchor-specific news headline rules: light gray text, subtle underline, matching visited color, and cyan hover so links remain readable on the dark UI while still looking clickable.
- Nearest Misses no longer reports only "Options not liquid/tradable". It now distinguishes no options response, no contracts in the 7–35 DTE window, and a thin returned chain using contracts_checked/tradable_contracts counts.
- The liquidity gate remains purely a tradability gate based on OI, volume, and spread thresholds; premium price does not determine whether a chain is Liquid/Tradable.

'''
r.write_text(entry+readme)

out=p.read_text()
assert 'APP_VERSION = "27.5"' in out
assert out.count(old_news)==0
assert 'a.newsHeadline{color:#e5e7eb' in out
assert 'a.newsHeadline:visited{color:#e5e7eb}' in out
assert 'a.newsHeadline:hover{color:#7fd8ff' in out
assert 'Options not liquid/tradable' not in out
assert 'Illiquid chain · only ${tradableCount}' in out
assert 'No option contracts in the 7–35 DTE window' in out
print('v27.5 patch applied')
