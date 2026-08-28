from pathlib import Path
p=Path('app.py'); s=p.read_text()
s=s.replace('APP_VERSION = "25.26"','APP_VERSION = "25.27"',1)
needle='\ndef yahoo_fund_holdings(etf):\n'
insert='''\ndef public_full_holdings_fallback(etf):
    """Full-list public fallback for issuer pages that reject server requests.

    This is deliberately below official issuer feeds and above paid/auth-gated
    fallbacks. It keeps theme universes usable when an issuer returns 406/403.
    """
    etf=str(etf or "").upper().strip()
    slugs={
        "PBW":"invesco-wilderhill-clean-energy-etf",
        "TAN":"invesco-solar-etf",
    }
    slug=slugs.get(etf)
    if not slug:
        raise RuntimeError(f"No public full-holdings fallback configured for {etf}.")
    url=f"https://companiesmarketcap.com/{slug}/holdings/"
    resp=requests.get(url,timeout=25,headers={
        "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept":"text/html,application/xhtml+xml",
    })
    resp.raise_for_status()
    tables=pd.read_html(io.StringIO(resp.text),flavor="lxml")
    best=[]
    for df in tables:
        cols={str(c).strip().lower():c for c in df.columns}
        tcol=next((orig for low,orig in cols.items() if low=="ticker" or "ticker" in low),None)
        if tcol is None: continue
        ncol=next((orig for low,orig in cols.items() if low=="name" or "name" in low),None)
        wcol=next((orig for low,orig in cols.items() if "weight" in low),None)
        rows=[]
        for _,row in df.iterrows():
            weight=None
            if wcol is not None:
                try: weight=float(str(row.get(wcol,"")).replace("%","").replace(",","").strip())
                except Exception: pass
            rows.append({"ticker":row.get(tcol,""),"name":row.get(ncol,row.get(tcol,"")) if ncol is not None else row.get(tcol,""),"weight":weight})
        rows=clean_equity_holdings(rows)
        if len(rows)>len(best): best=rows
    if len(best)<15:
        raise RuntimeError(f"Public full-holdings fallback returned only {len(best)} usable rows for {etf}.")
    return best
'''
assert needle in s
s=s.replace(needle,insert+needle,1)
needle2='''    # Universal full-universe fallback. This is preferred over Yahoo's top 10.
    try:
        h = finnhub_etf_holdings(etf)
'''
repl2='''    # Public full-list fallback for Invesco theme funds whose product pages may
    # reject Render/server traffic with HTTP 406. Prefer this to auth-gated
    # Finnhub and Yahoo top-holdings so PBW/TAN keep a broad stock universe.
    if etf in INVESCO_FUNDS:
        try:
            h = public_full_holdings_fallback(etf)
            return h, "Public full holdings fallback"
        except Exception as e:
            attempts.append(f"Public full holdings: {e}")

    # Universal full-universe fallback. This is preferred over Yahoo's top 10.
    try:
        h = finnhub_etf_holdings(etf)
'''
assert needle2 in s
s=s.replace(needle2,repl2,1)
# Sanitize final user-facing provider failure; detailed provider exceptions can expose API URLs/tokens.
old='''        attempts.append(f"Yahoo: {e}")
        raise RuntimeError(f"Could not retrieve holdings for {etf}. " + " | ".join(attempts))
'''
new='''        attempts.append(f"Yahoo: {e}")
        raise RuntimeError(f"Could not retrieve holdings for {etf}. Live providers are temporarily unavailable and no cached holdings are available yet.")
'''
assert old in s
s=s.replace(old,new,1)
p.write_text(s)
r=Path('README.txt'); rs=r.read_text(); entry='''v25.27 — PBW/TAN HOLDINGS FALLBACK HARDENING\n- Invesco product pages can reject Render/server requests with HTTP 406 even when the same page works interactively in a browser. Added a full-list public holdings fallback for PBW/TAN before Finnhub/Yahoo so the Stock Screen remains usable when that happens.\n- The fallback is still subordinate to the official issuer feed and feeds the existing persistent last-known-good cache after a successful retrieval.\n- Sanitized terminal holdings errors so provider URLs/API tokens and raw upstream exception chains are no longer exposed in the UI.\n\n'''
if not rs.startswith('v25.27'): r.write_text(entry+rs)
