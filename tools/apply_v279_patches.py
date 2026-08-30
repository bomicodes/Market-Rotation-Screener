from pathlib import Path

p=Path('app.py')
s=p.read_text()

def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.8"','APP_VERSION = "27.9"','version')

old='''def compute_rrg(bench, asset, n1=10, n2=5):
    b = np.asarray(bench, dtype=float)
    a = np.asarray(asset, dtype=float)
    rs = a / b
    rs_sma = sma(rs, n1)
    ratio = 100.0 * rs / rs_sma
    mom_sma = sma(ratio, n2)
    momentum = 100.0 * ratio / mom_sma
    return ratio, momentum
'''
new='''def compute_rrg(bench, asset, n1=10, n2=5):
    """JdK-style RS-Ratio / RS-Momentum using the publicly documented z-score
    formulation of the Relative Rotation Graph method (Julius de Kempenaer).

    This is an original implementation of a formula published and
    independently re-implemented across multiple open-source RRG tools
    (e.g. RRG-Lite, RRGPy) and described in public technical-analysis
    references (StockCharts ChartSchool). It is not a reproduction of any
    vendor's proprietary source code -- the underlying RS-Ratio/RS-Momentum
    concept and z-score construction are publicly documented technique, and
    the code below is written from scratch against that public description,
    not copied from any single implementation.

    Previous version used a plain ratio-of-SMA (100 * RS/SMA(RS)) with no
    volatility scaling, which let every sector swing the same amount for the
    same % move regardless of that sector's own volatility -- this produced
    visibly noisier, more jagged tails than standard JdK RRG tools. The
    z-score version below scales each sector's deviation by its own recent
    volatility, which is what makes standard RRG tails read as smooth arcs
    rather than jagged zigzags.

    RS-Ratio     = 100 + (RS - SMA(RS, n1)) / STDEV(RS, n1)
    RS-Momentum  = 100 + (ROC - SMA(ROC, n2)) / STDEV(ROC, n2)
                   where ROC is the period-over-period % change of RS-Ratio.
    """
    b = np.asarray(bench, dtype=float)
    a = np.asarray(asset, dtype=float)
    rs = pd.Series((a / b) * 100.0, dtype=float)
    rs_mean = rs.rolling(n1).mean()
    rs_std = rs.rolling(n1).std(ddof=1).replace(0, np.nan)
    ratio = 100.0 + (rs - rs_mean) / rs_std

    roc = ratio.pct_change() * 100.0
    roc_mean = roc.rolling(n2).mean()
    roc_std = roc.rolling(n2).std(ddof=1).replace(0, np.nan)
    momentum = 100.0 + (roc - roc_mean) / roc_std

    return ratio.to_numpy(), momentum.to_numpy()
'''
once(old,new,'compute_rrg')

once('''    # Premium Support deliberately builds its own 7-90 DTE chain rather than
    # inheriting options_quality_payload(), whose UI-oriented contract list is
    # truncated. This ensures farther-dated contracts are genuinely searched.
    today=pd.Timestamp.now().normalize(); start=(today+pd.Timedelta(days=7)).strftime("%Y-%m-%d"); end=(today+pd.Timedelta(days=90)).strftime("%Y-%m-%d")
''','''    # Premium Support deliberately builds its own 7-730 DTE chain rather than
    # inheriting options_quality_payload(), whose UI-oriented contract list is
    # truncated. This ensures farther-dated contracts are genuinely searched.
    # Was capped at 90 DTE, which structurally excluded the ~100-730 DTE LEAPS
    # this trader actually holds (confirmed by the MSFT $500C 18 Sep 26 case:
    # ~100-110 DTE at base formation, premium based at $2.65 for ~8 weeks then
    # ran to $23.30). Extended to 730 (~2yr) with longer buckets so far-dated
    # bases get genuinely searched instead of silently falling outside the window.
    today=pd.Timestamp.now().normalize(); start=(today+pd.Timedelta(days=7)).strftime("%Y-%m-%d"); end=(today+pd.Timedelta(days=730)).strftime("%Y-%m-%d")
''','premium universe header')

once('if dte is None or dte<7 or dte>90:continue','if dte is None or dte<7 or dte>730:continue','premium dte filter')

once('''    # Preserve representation across expiration horizons instead of letting the
    # front month monopolize the finalists. Take up to four from each bucket,
    # then fill any remaining slots with the best unused candidates overall.
    selected=[];seen=set()
    for lo,hi in ((7,35),(36,60),(61,90)):
        bucket=[x[1] for x in candidates if x[1].get("dte") is not None and lo<=x[1]["dte"]<=hi]
        for r in bucket[:4]:
            if r.get("symbol") and r["symbol"] not in seen:
                selected.append(r);seen.add(r["symbol"])
    for _,r in candidates:
        if len(selected)>=12:break
''','''    # Preserve representation across expiration horizons instead of letting the
    # front month monopolize the finalists. Take a few from each bucket (fewer
    # per bucket now that there are more buckets, capped so the total stays
    # aligned with alpaca_option_daily_bars' own 20-symbol history-fetch limit),
    # then fill any remaining slots with the best unused candidates overall.
    selected=[];seen=set()
    for lo,hi in ((7,35),(36,60),(61,90),(91,180),(181,365),(366,730)):
        bucket=[x[1] for x in candidates if x[1].get("dte") is not None and lo<=x[1]["dte"]<=hi]
        for r in bucket[:3]:
            if r.get("symbol") and r["symbol"] not in seen:
                selected.append(r);seen.add(r["symbol"])
    for _,r in candidates:
        if len(selected)>=20:break
''','premium buckets')

once('''    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:8],"contracts_considered":len(premium_rows),"contracts_screened":len(selected),"dte_universe":"7-90","expiration_buckets":["7-35","36-60","61-90"],"history_lookback_days":100,"premium_bars_window":30,"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}
''','''    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:8],"contracts_considered":len(premium_rows),"contracts_screened":len(selected),"dte_universe":"7-730","expiration_buckets":["7-35","36-60","61-90","91-180","181-365","366-730"],"history_lookback_days":100,"premium_bars_window":30,"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}
''','premium payload metadata')

once('''    dte_min=max(0,min(30,int(dte_min or 0)));dte_max=max(dte_min+1,min(90,int(dte_max or 35)))
''','''    # Ceiling raised from 90 -> 760 (buffer past 730/2yr) so LEAPS-range
    # callers (premium support, the liquidity gate below) can actually reach
    # the contracts a long-dated swing trader holds. Callers that don't pass
    # dte_max still default to 35 and are unaffected (GEX, IV/RV state).
    dte_min=max(0,min(30,int(dte_min or 0)));dte_max=max(dte_min+1,min(760,int(dte_max or 35)))
''','options dte ceiling')

insert='''
_LIQUIDITY_RANK={"Liquid":0,"Tradable":1,"Thin":2}

def contract_liquidity_gate(ticker, gex_window="0-30"):
    """Decoupled liquidity gate: don't reject a name just because its front-month
    (7-35 DTE) chain is thin. A long-dated swing trader may hold a 400+ DTE LEAPS
    on a name whose weeklies are dead — that's a deep chain the old gate never
    looked at. Check the front month first (cheap, most names resolve here);
    only pay for the wider LEAPS fetch when the front month doesn't already
    clear the bar.
    """
    ticker=ticker.upper().strip()
    front=cached_refresh_safe(f"options-v24-1:{ticker}:{gex_window}:7:35",
                               lambda:options_quality_payload(ticker,gex_window,35,7),ttl=600)[0]
    if front.get("liquidity")=="Liquid":
        return {**front,"liquidity_source":"front-month","leaps_checked":False}
    leaps=None
    try:
        leaps=cached_refresh_safe(f"options-v24-1:{ticker}:{gex_window}:36:730",
                                   lambda:options_quality_payload(ticker,gex_window,730,36),ttl=600)[0]
    except Exception:
        leaps=None
    if not leaps or _LIQUIDITY_RANK.get(leaps.get("liquidity"),3)>=_LIQUIDITY_RANK.get(front.get("liquidity"),3):
        return {**front,"liquidity_source":"front-month","leaps_checked":leaps is not None,
                "leaps_liquidity":leaps.get("liquidity") if leaps else None}
    merged=dict(front)
    merged["liquidity"]=leaps["liquidity"]
    merged["liquidity_source"]="leaps"
    merged["leaps_checked"]=True
    merged["leaps_liquid_contracts"]=leaps.get("liquid_contracts")
    merged["leaps_tradable_contracts"]=leaps.get("tradable_contracts")
    merged["leaps_contracts_checked"]=leaps.get("contracts_checked")
    merged["front_month_liquidity"]=front.get("liquidity")
    return merged

'''
once('\n\n\ndef post_earnings_otm_contract(payload, direction="bullish", expected_move_pct=None, min_dte=None, ideal_dte=None):', '\n\n'+insert+'def post_earnings_otm_contract(payload, direction="bullish", expected_move_pct=None, min_dte=None, ideal_dte=None):','liquidity gate insert')

once('''        payload,stale,err=cached_refresh_safe(f"premium-support-v25-9:{ticker.upper()}:{direction}",lambda:premium_support_payload(ticker,direction,base),ttl=1800)
''','''        # v26-1: bumped from v25-9 because the DTE universe changed (7-90 -> 7-730);
        # a stale v25-9 cache entry would otherwise keep serving front-month-only
        # results under the old key's TTL after this deploy.
        payload,stale,err=cached_refresh_safe(f"premium-support-v26-1:{ticker.upper()}:{direction}",lambda:premium_support_payload(ticker,direction,base),ttl=1800)
''','premium cache key')

once('''        def one(sym):
            try:
                p,stale,err=cached_refresh_safe(f"options-v24-1:{sym}:0-30:7:35",lambda:options_quality_payload(sym,"0-30",35,7),ttl=600)
                return {"ok":True,**p,"stale":stale}
            except Exception as e:
''','''        def one(sym):
            try:
                # Was hardcoded to the 7-35 DTE front-month chain only, so a
                # name with a dead weekly chain but a deep LEAPS chain got
                # rejected as "not liquid/tradable" even though the contracts
                # this trader actually holds were sitting right there.
                p=contract_liquidity_gate(sym,"0-30")
                return {"ok":True,**p,"stale":False}
            except Exception as e:
''','options scan liquidity gate')

p.write_text(s)
print('patched app.py to v27.9')
