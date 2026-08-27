from pathlib import Path

app_path=Path('app.py')
s=app_path.read_text()
assert 'APP_VERSION = "25.21"' in s
s=s.replace('APP_VERSION = "25.21"','APP_VERSION = "25.22"',1)

# Make Invesco HTML parsing explicit and resilient now that html5lib/bs4 are installed.
old='tables = pd.read_html(io.StringIO(resp.text))'
new='''\n    try:\n        tables = pd.read_html(io.StringIO(resp.text), flavor="lxml")\n    except Exception:\n        tables = pd.read_html(io.StringIO(resp.text), flavor="bs4")'''
assert old in s
s=s.replace(old,new,1)

# Rename the live provider chain so a persistent last-known-good wrapper can sit around it.
assert 'def get_fund_holdings(etf):' in s
s=s.replace('def get_fund_holdings(etf):','def _get_fund_holdings_live(etf):',1)

# Accept an official Invesco top-holdings table as a partial-but-better-than-dead fallback.
old='''            if len(h) >= 15:\n                return h, "Invesco official product holdings"\n            attempts.append(f"Invesco: only {len(h)} usable rows")'''
new='''            if len(h) >= 15:\n                return h, "Invesco official product holdings"\n            if len(h) >= 8:\n                return h, "Invesco official top holdings fallback (PARTIAL)"\n            attempts.append(f"Invesco: only {len(h)} usable rows")'''
assert old in s
s=s.replace(old,new,1)

wrapper='''\n\ndef _ensure_holdings_cache_table(con):\n    con.execute("""CREATE TABLE IF NOT EXISTS holdings_cache(\n      etf TEXT PRIMARY KEY, updated_at TEXT NOT NULL, source TEXT, raw_json TEXT NOT NULL)""")\n\ndef _save_holdings_cache(etf, holdings, source):\n    if not holdings:\n        return\n    try:\n        backend=_setup_storage_backend(); now=datetime.utcnow().isoformat(timespec="seconds")+"Z"\n        raw=json.dumps(holdings,separators=(",",":"),default=str)\n        with _setup_db() as con:\n            _ensure_holdings_cache_table(con)\n            if backend=="postgresql":\n                con.execute("""INSERT INTO holdings_cache(etf,updated_at,source,raw_json) VALUES(%s,%s,%s,%s)\n                  ON CONFLICT(etf) DO UPDATE SET updated_at=EXCLUDED.updated_at, source=EXCLUDED.source, raw_json=EXCLUDED.raw_json""",\n                  (etf,now,source,raw))\n            else:\n                con.execute("INSERT OR REPLACE INTO holdings_cache(etf,updated_at,source,raw_json) VALUES(?,?,?,?)",\n                  (etf,now,source,raw))\n            con.commit()\n    except Exception:\n        pass\n\ndef _load_holdings_cache(etf):\n    try:\n        backend=_setup_storage_backend()\n        with _setup_db() as con:\n            _ensure_holdings_cache_table(con)\n            q="SELECT updated_at,source,raw_json FROM holdings_cache WHERE etf=%s" if backend=="postgresql" else "SELECT updated_at,source,raw_json FROM holdings_cache WHERE etf=?"\n            row=con.execute(q,(etf,)).fetchone()\n            if not row:\n                return None\n            row=dict(row); holdings=json.loads(row.get("raw_json") or "[]")\n            if not isinstance(holdings,list) or len(holdings)<5:\n                return None\n            return holdings,row.get("source"),row.get("updated_at")\n    except Exception:\n        return None\n\ndef get_fund_holdings(etf):\n    """Live issuer-first holdings with a persistent last-known-good safety net.\n\n    A temporary issuer/Finnhub/Yahoo outage must not blank the stock screen or\n    force a market-wide Post-Earnings scan to retry the same broken providers.\n    """\n    etf=str(etf or "").upper().strip()\n    try:\n        holdings,source=_get_fund_holdings_live(etf)\n        if holdings:\n            _save_holdings_cache(etf,holdings,source)\n        return holdings,source\n    except Exception as live_err:\n        cached_row=_load_holdings_cache(etf)\n        if cached_row:\n            holdings,source,updated_at=cached_row\n            return holdings,f"Cached holdings · {source or 'last known good'} · {updated_at}"\n        raise live_err\n\n'''
marker='\n\ndef finnhub_earnings_calendar(start_date, end_date):'
assert marker in s
s=s.replace(marker,wrapper+marker,1)
app_path.write_text(s)

req=Path('requirements.txt')
r=req.read_text()
for dep in ('html5lib>=1.1','beautifulsoup4>=4.12','lxml>=5.0'):
    if dep not in r:
        r += dep+'\n'
req.write_text(r)

readme=Path('README.txt')
rs=readme.read_text()
readme.write_text('''v25.22 — ETF HOLDINGS RESILIENCE + POST-EARNINGS PROVIDER HARDENING\n- Fixed TAN/PBW-style Invesco holdings parsing by installing the HTML parser stack used by pandas.read_html (lxml + BeautifulSoup + html5lib) and explicitly falling back between parsers.\n- Official Invesco top-holdings tables with >=8 usable rows are now accepted as a clearly-labeled PARTIAL fallback instead of throwing the entire Stock Screen into Finnhub/Yahoo.\n- Added a persistent last-known-good ETF holdings cache backed by the existing Neon/Postgres (SQLite locally). Successful issuer/Finnhub/Yahoo holdings are saved; if every live provider later fails, Stock Screen and Post-Earnings use the cached universe instead of going blank.\n- This builds on v25.21's Post-Earnings 502 fix: the market-wide scan is already cached/stale-safe, and now its holdings-discovery stage is also resilient to provider outages.\n\n'''+rs)
