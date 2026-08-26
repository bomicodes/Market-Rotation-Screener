from pathlib import Path
import re
p=Path('app.py');s=p.read_text()

def one(old,new,label):
    global s
    n=s.count(old)
    if n!=1: raise SystemExit(f'{label}: expected 1, found {n}')
    s=s.replace(old,new,1)

# Keep the swing contract universe (7-35D) separate from the independently
# selected GEX window. In particular, 0-30 GEX must still include 0-6D gamma.
old='''    # GEX can use a different expiration universe than the trade-selection chain.\n    gex_rows=rows\n    bucket=str(gex_window or "0-30").lower()\n    if bucket not in ("0-7","8-30","31-90","all"): bucket="0-30"\n    if bucket!="0-30":\n        ranges={"0-7":(0,7),"8-30":(8,30),"31-90":(31,90),"all":(0,365)}\n        lo,hi=ranges[bucket]; gs=(today+pd.Timedelta(days=lo)).strftime("%Y-%m-%d"); ge=(today+pd.Timedelta(days=hi)).strftime("%Y-%m-%d")\n        gcontracts=alpaca_option_contracts(ticker,gs,ge); gmeta={x.get("symbol"):x for x in gcontracts if x.get("symbol")}\n        gsnaps=alpaca_option_chain(ticker,gs,ge,spot); gex_rows=[]\n        for sym,snap in gsnaps.items():\n            if sym not in gmeta: continue\n            rr=option_contract_row(sym,snap,gmeta[sym],spot)\n            if rr.get("expiration") and rr.get("moneyness_pct") is not None and abs(rr["moneyness_pct"])<=25:gex_rows.append(rr)\n'''
new='''    # GEX has its own expiration universe, independent of the 7-35D swing\n    # contract-selection chain. This prevents trade-horizon filtering from\n    # accidentally dropping near-expiry gamma that can dominate dealer positioning.\n    bucket=str(gex_window or "0-30").lower()\n    if bucket not in ("0-7","8-30","31-90","all","0-30"): bucket="0-30"\n    ranges={"0-7":(0,7),"0-30":(0,30),"8-30":(8,30),"31-90":(31,90),"all":(0,365)}\n    lo,hi=ranges[bucket]; gs=(today+pd.Timedelta(days=lo)).strftime("%Y-%m-%d"); ge=(today+pd.Timedelta(days=hi)).strftime("%Y-%m-%d")\n    gcontracts=alpaca_option_contracts(ticker,gs,ge); gmeta={x.get("symbol"):x for x in gcontracts if x.get("symbol")}\n    gsnaps=alpaca_option_chain(ticker,gs,ge,spot); gex_rows=[]\n    for sym,snap in gsnaps.items():\n        if sym not in gmeta: continue\n        rr=option_contract_row(sym,snap,gmeta[sym],spot)\n        if rr.get("expiration") and rr.get("moneyness_pct") is not None and abs(rr["moneyness_pct"])<=25:gex_rows.append(rr)\n'''
one(old,new,'independent gex window')

# Macro risk must affect ranking, not merely appear in the UI.
old='''if(c.catalyst?.days_to_earnings!=null&&c.catalyst.days_to_earnings<=3)score-=10;return {...b,score:Math.max(0,Math.min(100,Math.round(score))),context:c,factors:factorBreakdownFor(x,b,c)};'''
new='''if(c.catalyst?.days_to_earnings!=null&&c.catalyst.days_to_earnings<=3)score-=10;const mr=c.macro_risk?.risk||"CLEAR";if(mr==="HIGH")score-=12;else if(mr==="ELEVATED")score-=6;else if(mr==="WATCH")score-=2;return {...b,score:Math.max(0,Math.min(100,Math.round(score))),context:c,factors:factorBreakdownFor(x,b,c)};'''
one(old,new,'macro score penalty')

p.write_text(s)
print('v24.1 final correctness fixes applied')
