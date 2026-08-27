from pathlib import Path
p=Path('app.py')
s=p.read_text()
assert 'APP_VERSION = "25.20"' in s
s=s.replace('APP_VERSION = "25.20"','APP_VERSION = "25.21"',1)
old='''    try:\n        recent_days=max(3,min(10,int(request.args.get("days","5"))))\n        all_holdings={}; parent_map={}; sources=set()'''
new='''    recent_days=max(3,min(10,int(request.args.get("days","5"))))\n    def _build():\n        all_holdings={}; parent_map={}; sources=set()'''
assert old in s
s=s.replace(old,new,1)
s=s.replace('''            return jsonify({"ok":True,"results":[],"universe":len(tickers),\n                            "recent_reporters":0,"diagnostics":diag})''','''            return {"results":[],"universe":len(tickers),\n                    "recent_reporters":0,"diagnostics":diag}''',1)
old2='''        return jsonify({"ok":True,"results":rows[:8],"universe":len(tickers),\n                        "recent_reporters":len(reporters),"recent_days":recent_days,\n                        "diagnostics":diag,"holdings_sources":sorted(sources),\n                        "options_deferred":True})\n    except Exception as e:\n        return jsonify({"ok":False,"error":str(e)}),500'''
new2='''        return {"results":rows[:8],"universe":len(tickers),\n                "recent_reporters":len(reporters),"recent_days":recent_days,\n                "diagnostics":diag,"holdings_sources":sorted(sources),\n                "options_deferred":True}\n\n    try:\n        key=f"postearnings-opportunities-v1:{recent_days}"\n        payload,stale,err=cached_refresh_safe(key,_build,ttl=300)\n        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})\n    except Exception as e:\n        return jsonify({"ok":False,"error":str(e)}),500'''
assert old2 in s
s=s.replace(old2,new2,1)
p.write_text(s)
r=Path('README.txt'); txt=r.read_text(); header='''v25.21 — POST-EARNINGS SCANNER 502 FIX\n- Wrapped /api/postearnings-opportunities in cached_refresh_safe with a 5-minute TTL so repeat requests do not rerun the full discovery pipeline.\n- A genuine refresh failure can now serve the last known-good result as stale, with the refresh error preserved, instead of returning a hard failure when cached data exists.\n\n'''; r.write_text(header+txt)
