from pathlib import Path

p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "25.13"' in s
s=s.replace('APP_VERSION = "25.13"','APP_VERSION = "25.14"',1)

old='''    # Premium Support deliberately uses a wider expiration universe than the
    # normal swing selector. Longer-dated contracts have time to form the
    # multi-week premium bases this layer is designed to detect.
    base=options_quality_payload(ticker,"0-30",90,7);spot=_safe_float(base.get("spot"))
    if not spot:return {"ticker":ticker,"direction":direction,"available":False,"reason":"Spot unavailable."}
    candidates=[]
    for r in base.get("contracts") or []:'''
new='''    # Premium Support deliberately builds its own 7-90 DTE chain rather than
    # inheriting options_quality_payload(), whose UI-oriented contract list is
    # truncated. This ensures farther-dated contracts are genuinely searched.
    today=pd.Timestamp.now().normalize(); start=(today+pd.Timedelta(days=7)).strftime("%Y-%m-%d"); end=(today+pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    _,spot=realized_vol_20d(ticker)
    if not spot:return {"ticker":ticker,"direction":direction,"available":False,"reason":"Spot unavailable."}
    contracts=alpaca_option_contracts(ticker,start,end); meta={x.get("symbol"):x for x in contracts if x.get("symbol")}
    snaps=alpaca_option_chain(ticker,start,end,spot); premium_rows=[]
    for sym,snap in snaps.items():
        if sym not in meta:continue
        rr=option_contract_row(sym,snap,meta[sym],spot)
        if rr.get("expiration") and rr.get("moneyness_pct") is not None and abs(rr["moneyness_pct"])<=20:premium_rows.append(rr)
    candidates=[]
    for r in premium_rows:'''
assert old in s, 'premium base block not found'
s=s.replace(old,new,1)

old='''    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:8],"contracts_screened":len(selected),"dte_universe":"7-90","expiration_buckets":["7-35","36-60","61-90"],"history_lookback_days":100,"premium_bars_window":30,"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}'''
new='''    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:8],"contracts_considered":len(premium_rows),"contracts_screened":len(selected),"dte_universe":"7-90","expiration_buckets":["7-35","36-60","61-90"],"history_lookback_days":100,"premium_bars_window":30,"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}'''
assert old in s, 'premium return block not found'
s=s.replace(old,new,1)
p.write_text(s)

r=Path('README.txt'); rs=r.read_text()
note='''v25.14 — PREMIUM SUPPORT FULL 7–90 DTE CHAIN\n- Premium Support now builds its own complete 7–90 DTE Alpaca option chain instead of consuming the generic options-quality payload, which intentionally truncates its UI list to 120 contracts.\n- This guarantees 36–60 and 61–90 DTE contracts can actually reach the premium-support bucket selector even when a ticker has a very large front-month chain.\n- Response diagnostics now include contracts_considered as well as contracts_screened.\n\n'''
if not rs.startswith('v25.14'):r.write_text(note+rs)
