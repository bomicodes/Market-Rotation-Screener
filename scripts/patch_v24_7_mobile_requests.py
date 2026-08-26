from pathlib import Path
import re

p = Path('app.py')
s = p.read_text()
orig = s

# Version: tolerate whichever of the immediately preceding staged builds lands first.
s = re.sub(r'APP_VERSION = "24\.(?:5|6)"', 'APP_VERSION = "24.7"', s, count=1)

# One request path for all ticker GETs. This mirrors the chart request shape that
# successfully dispatches on iOS Safari and avoids the older helper path that has
# been throwing WebKit's opaque DOMException before STRAT/options requests leave
# the browser.
needle = '''function safeTickerUrl(path,ticker,params={}){\n // Safari has intermittently thrown a DOMException ("The string did not match the expected pattern")\n // before dispatching chart requests when a prebuilt query string is passed through the generic helper.\n // Build the query from known scalar values and keep the final URL same-origin and relative.\n const base=safeTickerEndpoint(path,ticker);\n const q=[];\n Object.entries(params||{}).forEach(([k,v])=>{\n   if(v===undefined||v===null)return;\n   q.push(`${encodeURIComponent(String(k))}=${encodeURIComponent(String(v))}`);\n });\n return q.length?`${base}?${q.join("&")}`:base;\n}\n'''
if needle not in s:
    raise SystemExit('safeTickerUrl block not found')
insert = needle + '''async function safeTickerFetchJson(path,ticker,params={}){\n const url=safeTickerUrl(path,ticker,params);\n let r;\n try{\n   r=await window.fetch(url,{method:"GET",credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}});\n }catch(e){\n   console.error("Ticker request dispatch failed",{path,ticker,url,error:e});\n   throw new Error(`Request could not be dispatched: ${e?.message||e}`);\n }\n const raw=await r.text();\n let j;\n try{j=raw?JSON.parse(raw):{};}catch(e){throw new Error(`Service returned an unreadable response (${r.status})`);}\n if(!r.ok||!j?.ok)throw new Error(j?.error||`Request failed (${r.status})`);\n return j;\n}\n'''
s = s.replace(needle, insert, 1)

# STRAT: replace the old fetch helper entirely.
old = '''   const r=await fetch(safeTickerEndpoint("/api/strat",ticker),{headers:{"Accept":"application/json"}}),j=await r.json();\n   if(seq!==stratRequestSeq)return;\n   if(!r.ok||!j.ok)throw Error(j.error||"STRAT load failed");'''
new = '''   const j=await safeTickerFetchJson("/api/strat",ticker);\n   if(seq!==stratRequestSeq)return;'''
if old not in s:
    raise SystemExit('STRAT request block not found')
s = s.replace(old, new, 1)

# Options: normalize query construction and request dispatch. Match the existing
# compact statement regardless of spacing introduced by adjacent releases.
pat = re.compile(r'''const gw=document\.getElementById\("gexWindow"\)\?\.value\|\|"0-30";\s*const r=await fetch\(safeTickerEndpoint\("/api/options",ticker\)\+`\?gex_window=\$\{encodeURIComponent\(gw\)\}&dte_min=7&dte_max=35`,\{headers:\{"Accept":"application/json"\}\}\),j=await r\.json\(\);\s*if\(!r\.ok\|\|!j\.ok\)throw Error\(j\.error\|\|"Options request failed"\);''')
rep = '''const gw=document.getElementById("gexWindow")?.value||"0-30";\n   const j=await safeTickerFetchJson("/api/options",ticker,{gex_window:gw,dte_min:7,dte_max:35});'''
s, n = pat.subn(rep, s, count=1)
if n != 1:
    raise SystemExit(f'Options request block replacement count={n}')

# Flow uses the same ticker request path; do not let an old helper reintroduce the
# iOS Safari dispatch failure.
old_flow = '''   const r=await fetch(safeTickerEndpoint("/api/flow",ticker,force?"?refresh=1":""),{headers:{"Accept":"application/json"}}),j=await r.json();\n   if(!r.ok||!j.ok)throw Error(j.error||"Flow request failed");'''
new_flow = '''   const j=await safeTickerFetchJson("/api/flow",ticker,force?{refresh:1}:{});'''
if old_flow in s:
    s = s.replace(old_flow, new_flow, 1)

# Chart bars are useful even if a paid-profile subrequest fails. Previously either
# profile helper could throw and turn an otherwise valid Yahoo/Alpaca candle response
# into a 500/503. Degrade profile data independently instead.
old_profiles = '''        profiles=alpaca_session_volume_profiles(ticker)\n        visible_profiles=alpaca_visible_profiles(ticker,period,timeframe)'''
new_profiles = '''        try:\n            profiles=alpaca_session_volume_profiles(ticker)\n        except Exception as profile_err:\n            profiles={"session":None,"previous":None,"error":str(profile_err)}\n        try:\n            visible_profiles=alpaca_visible_profiles(ticker,period,timeframe)\n        except Exception as visible_err:\n            visible_profiles={"sessions":[],"weeks":[],"source":None,"error":str(visible_err)}'''
if old_profiles not in s:
    raise SystemExit('chart profile block not found')
s = s.replace(old_profiles, new_profiles, 1)

# Keep the mobile panel label aligned with the actual scanner defaults.
s = s.replace('Options · 0–30 DTE', 'Options · 7–35 DTE')

if s == orig:
    raise SystemExit('No changes made')
p.write_text(s)
print('patched app.py to v24.7')
