from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()

def one(old,new,label):
    global s
    n=s.count(old)
    if n!=1: raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

def sub(pattern,repl,label,flags=0):
    global s
    s2,n=re.subn(pattern,repl,s,count=1,flags=flags)
    if n!=1: raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s2

one('APP_VERSION = "24.0"','APP_VERSION = "24.1"','version')

# Expanded, official-source macro risk set. Keep this curated until a paid/reliable
# machine-readable economic-calendar source is configured.
sub(r'MACRO_CALENDAR=\[.*?\]\ndef upcoming_macro_events\(within_days=60\):\n    today=.*?return sorted\(out,key=lambda x:x\["date"\]\)\n', '''MACRO_CALENDAR=[
 {"date":"2026-08-26","time":"08:30 ET","type":"PCE","importance":"HIGH","label":"Personal Income & Outlays / July PCE","source":"BEA"},
 {"date":"2026-08-26","time":"08:30 ET","type":"GDP","importance":"HIGH","label":"GDP 2nd Estimate + Corporate Profits (Q2)","source":"BEA"},
 {"date":"2026-09-01","time":"10:00 ET","type":"JOLTS","importance":"MEDIUM","label":"JOLTS (July)","source":"BLS"},
 {"date":"2026-09-04","time":"08:30 ET","type":"NFP","importance":"HIGH","label":"Employment Situation (August)","source":"BLS"},
 {"date":"2026-09-10","time":"08:30 ET","type":"PPI","importance":"HIGH","label":"PPI (August)","source":"BLS"},
 {"date":"2026-09-11","time":"08:30 ET","type":"CPI","importance":"HIGH","label":"CPI (August)","source":"BLS"},
 {"date":"2026-09-16","time":"08:30 ET","type":"RETAIL","importance":"MEDIUM","label":"Retail Sales (August)","source":"Census"},
 {"date":"2026-09-16","time":"14:00 ET","type":"FOMC","importance":"HIGH","label":"FOMC Rate Decision","source":"Federal Reserve"},
 {"date":"2026-09-29","time":"10:00 ET","type":"JOLTS","importance":"MEDIUM","label":"JOLTS (August)","source":"BLS"},
 {"date":"2026-09-30","time":"08:30 ET","type":"PCE","importance":"HIGH","label":"Personal Income & Outlays / August PCE","source":"BEA"},
 {"date":"2026-09-30","time":"08:30 ET","type":"GDP","importance":"MEDIUM","label":"GDP 3rd Estimate + Corporate Profits (Q2)","source":"BEA"},
 {"date":"2026-10-27","time":"","type":"FOMC","importance":"MEDIUM","label":"FOMC meeting begins","source":"Federal Reserve"},
 {"date":"2026-10-28","time":"14:00 ET","type":"FOMC","importance":"HIGH","label":"FOMC Rate Decision","source":"Federal Reserve"},
 {"date":"2026-12-08","time":"","type":"FOMC","importance":"MEDIUM","label":"FOMC meeting begins","source":"Federal Reserve"},
 {"date":"2026-12-09","time":"14:00 ET","type":"FOMC","importance":"HIGH","label":"FOMC Rate Decision","source":"Federal Reserve"}]
def upcoming_macro_events(within_days=60):
    today=pd.Timestamp.now().normalize(); cutoff=today+pd.Timedelta(days=max(0,within_days)); out=[]
    for ev in MACRO_CALENDAR:
        d=pd.Timestamp(ev["date"])
        if today<=d<=cutoff: out.append({**ev,"days_away":int((d-today).days)})
    return sorted(out,key=lambda x:(x["date"],x.get("time") or ""))

def macro_risk_snapshot(within_days=7):
    events=upcoming_macro_events(within_days)
    high=[e for e in events if e.get("importance")=="HIGH"]
    nearest=min((e["days_away"] for e in events),default=None)
    nearest_high=min((e["days_away"] for e in high),default=None)
    risk="HIGH" if nearest_high is not None and nearest_high<=1 else ("ELEVATED" if nearest_high is not None and nearest_high<=3 else ("WATCH" if events else "CLEAR"))
    return {"risk":risk,"nearest_days":nearest,"nearest_high_days":nearest_high,"events":events[:8]}
''','macro calendar',re.S)

# Preserve earnings timestamps and use them to distinguish pre-market from after-close.
sub(r'def get_earnings_dates\(ticker, limit=12\):.*?\n\ndef event_session_index\(df, earnings_date\):.*?return int\(pos\)\n', '''def get_earnings_dates(ticker, limit=12):
    try:
        ed=yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if ed is None or len(ed)==0:return []
        dates=[]
        for raw in pd.to_datetime(ed.index):
            try:
                d=pd.Timestamp(raw)
                if d.tzinfo is not None:
                    d=d.tz_convert("America/New_York").tz_localize(None)
                dates.append(d)
            except Exception:pass
        # Keep time-of-day where supplied by the provider; duplicate calendar
        # dates collapse to the first unique timestamp.
        return sorted(list(dict.fromkeys(dates)), reverse=True)
    except Exception:
        return []

def event_session_index(df, earnings_date):
    idx=pd.DatetimeIndex(df.index)
    if idx.tz is not None: idx=idx.tz_convert(None)
    ts=pd.Timestamp(earnings_date)
    if ts.tzinfo is not None:
        try: ts=ts.tz_convert("America/New_York").tz_localize(None)
        except Exception: ts=ts.tz_localize(None)
    d=ts.normalize()
    pos=int(idx.searchsorted(d))
    if pos>=len(idx):return None
    # An after-close report belongs to the NEXT regular session. Premarket or
    # date-only events use the first session on/after the calendar date.
    if ts.hour>=16:
        while pos<len(idx) and pd.Timestamp(idx[pos]).normalize()<=d: pos+=1
    if pos>=len(idx):return None
    return int(pos)
''','earnings event alignment',re.S)

# Neutral moving-average structure must not silently fabricate a bullish trade plan.
one('''    else:\n        trigger=hi20;confirmation=hi20+.15*atr;invalidation=lo10;hard_fail=lo20;target1=hi20+1.5*atr;target2=hi20+3*atr;risk=max(.01,trigger-invalidation);reward=max(0,target2-trigger)\n    r20=_ctx_return(c,20);r50=_ctx_return(c,50);trend_strength=int(spot>sma20)+int(sma20>sma50)+int(r20 is not None and r20>0)+int(r50 is not None and r50>0)\n    return {"available":True,"spot":round(spot,2),"atr14":round(atr,2),"direction":direction,"trend_strength":trend_strength,"sma20":round(sma20,2),"sma50":round(sma50,2),"trigger":round(trigger,2),"confirmation":round(confirmation,2),"invalidation":round(invalidation,2),"hard_fail":round(hard_fail,2),"target1":round(target1,2),"target2":round(target2,2),"rr_to_target2":round(reward/risk,2) if risk else None,"return_20d":round(r20,2) if r20 is not None else None,"return_50d":round(r50,2) if r50 is not None else None}\n''','''    else:\n        trigger=confirmation=invalidation=hard_fail=target1=target2=None;risk=reward=None\n    r20=_ctx_return(c,20);r50=_ctx_return(c,50);trend_strength=int(spot>sma20)+int(sma20>sma50)+int(r20 is not None and r20>0)+int(r50 is not None and r50>0)\n    return {"available":True,"spot":round(spot,2),"atr14":round(atr,2),"direction":direction,"trend_strength":trend_strength,"sma20":round(sma20,2),"sma50":round(sma50,2),"trigger":round(trigger,2) if trigger is not None else None,"confirmation":round(confirmation,2) if confirmation is not None else None,"invalidation":round(invalidation,2) if invalidation is not None else None,"hard_fail":round(hard_fail,2) if hard_fail is not None else None,"target1":round(target1,2) if target1 is not None else None,"target2":round(target2,2) if target2 is not None else None,"rr_to_target2":round(reward/risk,2) if risk else None,"return_20d":round(r20,2) if r20 is not None else None,"return_50d":round(r50,2) if r50 is not None else None}\n''','neutral structure')

# Exact-signature expectancy stays exact; broad ticker history is disclosed separately.
one('''    try:\n        hist=setup_history_stats(ticker,signature)\n        if not hist.get("count"):\n            hist=setup_history_stats(ticker);hist["fallback_all_signatures"]=True\n    except Exception as e:hist={"count":0,"returns":{},"error":str(e)}\n    return {"ticker":ticker,"parent":parent,"relative_strength":rs,"rotation_persistence":persistence,"triple_relative_strength":triple,"structure":structure,"horizon":horizon,"catalyst":catalyst,"signature":signature,"historical_expectancy":hist}\n''','''    try:\n        hist=setup_history_stats(ticker,signature)\n        baseline=setup_history_stats(ticker)\n    except Exception as e:\n        hist={"count":0,"returns":{},"error":str(e)};baseline={"count":0,"returns":{},"error":str(e)}\n    macro=macro_risk_snapshot(7)\n    return {"ticker":ticker,"parent":parent,"relative_strength":rs,"rotation_persistence":persistence,"triple_relative_strength":triple,"structure":structure,"horizon":horizon,"catalyst":catalyst,"macro_risk":macro,"signature":signature,"historical_expectancy":hist,"ticker_baseline_expectancy":baseline}\n''','exact expectancy')

# Swing-trade chain defaults: avoid near-expiry liquidity inflating execution quality.
sub(r'def options_quality_payload\(ticker, gex_window="0-30", dte_max=30\):\n    ticker=ticker.upper\(\).strip\(\)\n    dte_max=max\(7,min\(90,int\(dte_max or 30\)\)\)\n    today=pd.Timestamp.now\(\).normalize\(\)\n    start=today.strftime\("%Y-%m-%d"\)\n    end=\(today\+pd.Timedelta\(days=dte_max\)\).strftime\("%Y-%m-%d"\)', '''def options_quality_payload(ticker, gex_window="0-30", dte_max=35, dte_min=7):
    ticker=ticker.upper().strip()
    dte_min=max(0,min(30,int(dte_min or 0)));dte_max=max(dte_min+1,min(90,int(dte_max or 35)))
    today=pd.Timestamp.now().normalize()
    start=(today+pd.Timedelta(days=dte_min)).strftime("%Y-%m-%d")
    end=(today+pd.Timedelta(days=dte_max)).strftime("%Y-%m-%d")''','options horizon')
one('''        "ticker":ticker,"spot":round(spot,2),"dte_min":0,"dte_max":dte_max,"gex_window":bucket,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}",''','''        "ticker":ticker,"spot":round(spot,2),"dte_min":dte_min,"dte_max":dte_max,"gex_window":bucket,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}",''','options dte metadata')

# API can explicitly request a horizon. Defaults remain swing-oriented.
one('''        force=request.args.get("refresh")=="1"\n        bucket=(request.args.get("gex_window") or "0-30").lower(); payload,stale,err=cached_refresh_safe(f"options-v23:{ticker.upper()}:{bucket}",lambda:options_quality_payload(ticker,bucket),force=force,ttl=600)''','''        force=request.args.get("refresh")=="1"\n        bucket=(request.args.get("gex_window") or "0-30").lower();dmin=max(0,min(30,int(request.args.get("dte_min",7))));dmax=max(dmin+1,min(90,int(request.args.get("dte_max",35))));payload,stale,err=cached_refresh_safe(f"options-v24-1:{ticker.upper()}:{bucket}:{dmin}:{dmax}",lambda:options_quality_payload(ticker,bucket,dmax,dmin),force=force,ttl=600)''','options api horizon')
one('''                p,stale,err=cached_refresh_safe(f"options-v21-2:{sym}",lambda:options_quality_payload(sym),ttl=600)''','''                p,stale,err=cached_refresh_safe(f"options-v24-1:{sym}:0-30:7:35",lambda:options_quality_payload(sym,"0-30",35,7),ttl=600)''','options scan horizon')

p.write_text(s)
print('v24.1 backend migration applied')
