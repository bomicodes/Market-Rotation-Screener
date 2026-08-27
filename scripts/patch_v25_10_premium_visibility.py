from pathlib import Path
p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "25.9"' in s
s=s.replace('APP_VERSION = "25.9"','APP_VERSION = "25.10"',1)

# Historical bars: OPRA can reject/return empty on some subscriptions. Fall back to indicative.
old='''        r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)\n        if r.status_code in (401,403):raise RuntimeError(f"Alpaca {ALPACA_OPTIONS_FEED} historical option-bar access was rejected.")\n        if r.status_code==429:\n            time.sleep(.75);r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)\n        r.raise_for_status();j=r.json() or {};bars=j.get("bars") or {}'''
new='''        r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)\n        if r.status_code in (401,403) and params.get("feed")!="indicative":\n            params["feed"]="indicative"\n            r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)\n        if r.status_code==429:\n            time.sleep(.75);r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)\n        r.raise_for_status();j=r.json() or {};bars=j.get("bars") or {}'''
assert old in s
s=s.replace(old,new,1)

# Do not reject otherwise useful OTM contracts just because today's volume is low.
s=s.replace('if spread is None or spread>22 or oi<75 or vol<10:continue','if spread is None or spread>25 or oi<50:continue',1)

# Preserve attempted payloads, including unavailable reason, so UI can explain failures.
old='''         if(r.ok&&j.ok)premiumSupportMap[x.ticker]=j;'''
new='''         if(r.ok&&j.ok)premiumSupportMap[x.ticker]=j;\n         else premiumSupportMap[x.ticker]={available:false,reason:j?.error||`HTTP ${r.status}`};'''
assert old in s
s=s.replace(old,new,1)

# Render explicit diagnostic instead of silently omitting the entire feature.
old=''' const premiumHTML=pc?`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>${pc.expiration} $${Number(pc.strike).toFixed(0)} ${String(pc.type||"").toLowerCase().startsWith("p")?"P":"C"} · $${Number(pc.mid||0).toFixed(2)} · ${pc.state}</b><div class="tiny">support $${Number(pc.support_low).toFixed(2)}–$${Number(pc.support_high).toFixed(2)} · ${pc.distance_from_support_pct==null?"—":Number(pc.distance_from_support_pct).toFixed(1)+"%"} above floor · ${pc.support_touches||0} tests · prior high $${Number(pc.prior_20d_high||0).toFixed(2)} (${pc.prior_expansion_multiple==null?"—":Number(pc.prior_expansion_multiple).toFixed(1)+"×"}) · score ${Number(pc.premium_support_score||0).toFixed(0)}/100</div></div>`:"";'''
new=''' const premiumHTML=pc?`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>${pc.expiration} $${Number(pc.strike).toFixed(0)} ${String(pc.type||"").toLowerCase().startsWith("p")?"P":"C"} · $${Number(pc.mid||0).toFixed(2)} · ${pc.state}</b><div class="tiny">support $${Number(pc.support_low).toFixed(2)}–$${Number(pc.support_high).toFixed(2)} · ${pc.distance_from_support_pct==null?"—":Number(pc.distance_from_support_pct).toFixed(1)+"%"} above floor · ${pc.support_touches||0} tests · prior high $${Number(pc.prior_20d_high||0).toFixed(2)} (${pc.prior_expansion_multiple==null?"—":Number(pc.prior_expansion_multiple).toFixed(1)+"×"}) · score ${Number(pc.premium_support_score||0).toFixed(0)}/100</div></div>`:(e.premiumSupport?`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>history unavailable</b><div class="tiny">${e.premiumSupport.reason||"No qualifying historical contract"}</div></div>`:`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>not evaluated</b></div>`);'''
assert old in s
s=s.replace(old,new,1)

p.write_text(s)

# changelog
r=Path('README.txt')
rs=r.read_text()
r.write_text('''v25.10 — PREMIUM SCANNER VISIBILITY / FALLBACK\n- Premium Support no longer fails silently on Top Setup cards: every finalist now shows the selected premium setup or an explicit unavailable/not-evaluated diagnostic.\n- Historical option bars retry with Alpaca indicative feed if OPRA historical access is rejected.\n- Relaxed the candidate prefilter from OI>=75 + volume>=10 + spread<=22% to OI>=50 + spread<=25%; current-day volume no longer blocks a contract that has useful historical premium structure.\n\n'''+rs)
