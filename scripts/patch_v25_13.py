from pathlib import Path

p = Path('app.py')
s = p.read_text()

old_version = 'APP_VERSION = "25.12"'
assert old_version in s, 'expected v25.12 version string not found'
s = s.replace(old_version, 'APP_VERSION = "25.13"', 1)

old = '    clean=clean[-20:]'
assert old in s, 'premium metrics lookback anchor not found'
s = s.replace(old, '    clean=clean[-30:]', 1)

old = '    base=options_payload or options_quality_payload(ticker,"0-30",35,7);spot=_safe_float(base.get("spot"))'
new = '''    # Premium Support deliberately uses a wider expiration universe than the\n    # normal swing selector. Longer-dated contracts have time to form the\n    # multi-week premium bases this layer is designed to detect.\n    base=options_quality_payload(ticker,"0-30",90,7);spot=_safe_float(base.get("spot"))'''
assert old in s, 'premium payload universe anchor not found'
s = s.replace(old, new, 1)

old = '        if dte is None or dte<7 or dte>35:continue'
assert old in s, 'premium DTE filter anchor not found'
s = s.replace(old, '        if dte is None or dte<7 or dte>90:continue', 1)

old = '''    candidates.sort(key=lambda z:z[0],reverse=True);selected=[x[1] for x in candidates[:8]]\n    if not selected:return {"ticker":ticker,"direction":direction,"available":False,"reason":"No liquid OTM candidate passed the premium-history prefilter."}\n    histories=alpaca_option_daily_bars([r["symbol"] for r in selected],55);scored=[];rank_by_symbol={r["symbol"]:rank for rank,r in candidates}'''
new = '''    candidates.sort(key=lambda z:z[0],reverse=True)\n    # Preserve representation across expiration horizons instead of letting the\n    # front month monopolize the finalists. Take up to four from each bucket,\n    # then fill any remaining slots with the best unused candidates overall.\n    selected=[];seen=set()\n    for lo,hi in ((7,35),(36,60),(61,90)):\n        bucket=[x[1] for x in candidates if x[1].get("dte") is not None and lo<=x[1]["dte"]<=hi]\n        for r in bucket[:4]:\n            if r.get("symbol") and r["symbol"] not in seen:\n                selected.append(r);seen.add(r["symbol"])\n    for _,r in candidates:\n        if len(selected)>=12:break\n        if r.get("symbol") and r["symbol"] not in seen:\n            selected.append(r);seen.add(r["symbol"])\n    if not selected:return {"ticker":ticker,"direction":direction,"available":False,"reason":"No liquid OTM candidate passed the premium-history prefilter."}\n    histories=alpaca_option_daily_bars([r["symbol"] for r in selected],100);scored=[];rank_by_symbol={r["symbol"]:rank for rank,r in candidates}'''
assert old in s, 'premium finalist/history anchor not found'
s = s.replace(old, new, 1)

old = '    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:5],"contracts_screened":len(selected),"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}'
new = '    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:8],"contracts_screened":len(selected),"dte_universe":"7-90","expiration_buckets":["7-35","36-60","61-90"],"history_lookback_days":100,"premium_bars_window":30,"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}'
assert old in s, 'premium return anchor not found'
s = s.replace(old, new, 1)

p.write_text(s)

r = Path('README.txt')
rs = r.read_text()
note = '''v25.13 — PREMIUM SUPPORT LONGER-DATED CONTRACTS\n- Premium Support now searches its own 7–90 DTE option universe instead of inheriting the regular 7–35 DTE swing-selector chain. The regular options scanner remains unchanged.\n- Candidate history is balanced across 7–35, 36–60, and 61–90 DTE buckets so front-month contracts cannot crowd out further-dated premium bases. Up to 12 contracts are inspected per ticker.\n- Historical premium lookback expanded to 100 calendar days and the support/compression calculation now uses up to the latest 30 daily premium bars, improving detection of multi-week bases.\n\n'''
if not rs.startswith('v25.13'):
    r.write_text(note + rs)
