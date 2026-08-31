from pathlib import Path
p=Path('app.py')
s=p.read_text(); orig=s
old='''            f_in=((f.get("tail_trajectory")=="Rotating In") if f.get("tail_trajectory") else (f.get("rs_up") is True and f.get("mom_up") is True))\n            t_in=((tr.get("tail_trajectory")=="Rotating In") if tr.get("tail_trajectory") else (tr.get("rs_up") is True and tr.get("mom_up") is True))\n            pre=move*2.2+(12 if f_in else 0)+(8 if t_in else 0)+(5 if f.get("quadrant") in ("Leading","Improving") else 0)'''
new='''            f_in=((f.get("tail_trajectory")=="Rotating In") if f.get("tail_trajectory") else (f.get("rs_up") is True and f.get("mom_up") is True))\n            t_in=((tr.get("tail_trajectory")=="Rotating In") if tr.get("tail_trajectory") else (tr.get("rs_up") is True and tr.get("mom_up") is True))\n            f_out=((f.get("tail_trajectory")=="Rotating Out") if f.get("tail_trajectory") else (f.get("rs_up") is False and f.get("mom_up") is False))\n            t_out=((tr.get("tail_trajectory")=="Rotating Out") if tr.get("tail_trajectory") else (tr.get("rs_up") is False and tr.get("mom_up") is False))\n            # Archetype-neutral pre-rank: don't bias the 20-name history pass toward\n            # bullish/NE names before we know whether the stock is a continuation\n            # or reversion setup. Historical enrichment decides trade direction.\n            pre=move*1.5+(12 if (f_in or f_out) else 0)+(8 if (t_in or t_out) else 0)+(5 if f.get("quadrant") in ("Leading","Improving","Weakening","Lagging") else 0)'''
assert old in s, 'pre-rank block not found'
s=s.replace(old,new,1)
s=s.replace('# Historical work only for the strongest 10 stock candidates.','# Historical work for the strongest 20 stock candidates after the cheap market-wide pre-rank.',1)
old_js='''function peTradeScore(x){\n if(x?.options_loading||!x?.best_contract)return x?.trade_score??null;\n const base=Number(x?.opportunity_score||0),exec=peExecutionBonus(x);'''
new_js='''function peTradeScore(x){\n if(x?.options_loading)return x?.trade_score??null;\n const base=Number(x?.opportunity_score||0);\n // A strong stock setup without an executable contract is still useful research,\n // but it should not outrank a slightly weaker setup we can actually trade.\n if(!x?.best_contract)return Math.round(base*.72*10)/10;\n const exec=peExecutionBonus(x);'''
assert old_js in s, 'trade score function not found'
s=s.replace(old_js,new_js,1)
assert 'prelim[:20]' in s and 'base*.72' in s and 'f_out=' in s
p.write_text(s)
print(f'follow-up patched app.py {len(orig)} -> {len(s)} bytes')
