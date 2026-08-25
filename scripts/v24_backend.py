from pathlib import Path
p=Path('app.py'); s=p.read_text()
def rep(a,b,n):
    global s
    if a not in s: raise SystemExit('missing '+n)
    s=s.replace(a,b,1)
if 'APP_VERSION = "24.0"' in s: raise SystemExit(0)
rep('APP_VERSION = "23.6"','APP_VERSION = "24.0"','version')
rep('''        con.execute("""CREATE TABLE IF NOT EXISTS setup_snapshots(
          id BIGSERIAL PRIMARY KEY, captured_at TEXT NOT NULL, trade_date TEXT NOT NULL,
          ticker TEXT NOT NULL, spot DOUBLE PRECISION, bias TEXT, score DOUBLE PRECISION, signature TEXT,
          raw_json TEXT NOT NULL, UNIQUE(trade_date,ticker,signature))""")
        con.commit()''','''        con.execute("""CREATE TABLE IF NOT EXISTS setup_snapshots(
          id BIGSERIAL PRIMARY KEY, captured_at TEXT NOT NULL, trade_date TEXT NOT NULL,
          ticker TEXT NOT NULL, spot DOUBLE PRECISION, bias TEXT, score DOUBLE PRECISION, signature TEXT,
          raw_json TEXT NOT NULL, UNIQUE(trade_date,ticker,signature))""")
        con.execute("""CREATE TABLE IF NOT EXISTS watchlist_items(
          ticker TEXT PRIMARY KEY, added_at TEXT NOT NULL, added_price DOUBLE PRECISION)""")
        con.commit()''','pg table')
rep('''    con.execute("""CREATE TABLE IF NOT EXISTS setup_snapshots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, trade_date TEXT NOT NULL,
      ticker TEXT NOT NULL, spot REAL, bias TEXT, score REAL, signature TEXT,
      raw_json TEXT NOT NULL, UNIQUE(trade_date,ticker,signature))""")
    con.commit()''','''    con.execute("""CREATE TABLE IF NOT EXISTS setup_snapshots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, trade_date TEXT NOT NULL,
      ticker TEXT NOT NULL, spot REAL, bias TEXT, score REAL, signature TEXT,
      raw_json TEXT NOT NULL, UNIQUE(trade_date,ticker,signature))""")
    con.execute("""CREATE TABLE IF NOT EXISTS watchlist_items(
      ticker TEXT PRIMARY KEY, added_at TEXT NOT NULL, added_price REAL)""")
    con.commit()''','sqlite table')
rep('CACHE = {}\nCACHE_TTL = 60 * 15','''def list_watchlist_items():
    backend=_setup_storage_backend()
    with _setup_db() as con:
        cur=con.execute("SELECT ticker,added_at,added_price FROM watchlist_items ORDER BY added_at DESC")
        rows=[dict(x) for x in cur.fetchall()]
    return rows

def add_watchlist_item(ticker, added_price=None):
    ticker=str(ticker or "").upper().strip()
    if not ticker: raise ValueError("ticker required")
    now=datetime.utcnow().isoformat(timespec="seconds")+"Z"
    backend=_setup_storage_backend()
    with _setup_db() as con:
        if backend=="postgresql":
            con.execute("""INSERT INTO watchlist_items(ticker,added_at,added_price) VALUES(%s,%s,%s)
              ON CONFLICT(ticker) DO NOTHING""",(ticker,now,_safe_float(added_price)))
        else:
            con.execute("INSERT OR IGNORE INTO watchlist_items(ticker,added_at,added_price) VALUES(?,?,?)",(ticker,now,_safe_float(added_price)))
        con.commit()
    return {"ticker":ticker,"added_at":now}

def remove_watchlist_item(ticker):
    ticker=str(ticker or "").upper().strip(); backend=_setup_storage_backend()
    with _setup_db() as con:
        if backend=="postgresql": con.execute("DELETE FROM watchlist_items WHERE ticker=%s",(ticker,))
        else: con.execute("DELETE FROM watchlist_items WHERE ticker=?",(ticker,))
        con.commit()
    return {"ticker":ticker,"removed":True}

CACHE = {}
CACHE_TTL = 60 * 15''','watch funcs')
rep('def cached(key, fn, ttl=CACHE_TTL):','''_SOURCE_HEALTH = {}
_SOURCE_HEALTH_GUARD = threading.Lock()
_SOURCE_NAMES = ("yfinance","alpaca_stocks","alpaca_options","finnhub","unusual_whales","nasdaq_yahoo_calendar")
def _mark_source(name,ok,detail=None):
    now=datetime.utcnow().isoformat(timespec="seconds")+"Z"
    with _SOURCE_HEALTH_GUARD:
        row=_SOURCE_HEALTH.setdefault(name,{"last_success":None,"last_error":None,"last_error_detail":None,"last_was_success":None})
        if ok: row["last_success"]=now
        else:
            row["last_error"]=now; row["last_error_detail"]=str(detail)[:300] if detail else None
        row["last_was_success"]=ok

def source_health_snapshot():
    with _SOURCE_HEALTH_GUARD: rows={k:dict(v) for k,v in _SOURCE_HEALTH.items()}
    out=[]
    for name in _SOURCE_NAMES:
        row=rows.get(name,{"last_success":None,"last_error":None,"last_error_detail":None,"last_was_success":None})
        status="ok" if row["last_was_success"] is True else "degraded" if row["last_was_success"] is False else "unknown"
        out.append({"name":name,"status":status,**row})
    return out

MACRO_CALENDAR=[
 {"date":"2026-09-04","type":"NFP","label":"Employment Situation (August)"},
 {"date":"2026-09-11","type":"CPI","label":"CPI (August)"},
 {"date":"2026-09-16","type":"FOMC","label":"FOMC Rate Decision"},
 {"date":"2026-10-27","type":"FOMC","label":"FOMC meeting begins"},
 {"date":"2026-10-28","type":"FOMC","label":"FOMC Rate Decision"},
 {"date":"2026-12-08","type":"FOMC","label":"FOMC meeting begins"},
 {"date":"2026-12-09","type":"FOMC","label":"FOMC Rate Decision"}]
def upcoming_macro_events(within_days=60):
    today=pd.Timestamp.now().normalize(); cutoff=today+pd.Timedelta(days=within_days); out=[]
    for ev in MACRO_CALENDAR:
        d=pd.Timestamp(ev["date"])
        if today<=d<=cutoff: out.append({**ev,"days_away":int((d-today).days)})
    return sorted(out,key=lambda x:x["date"])

def cached(key, fn, ttl=CACHE_TTL):''','health core')
rep('def auth_required():','''@app.get("/api/watchlist")
def api_watchlist_list():
    try:return jsonify({"ok":True,"items":list_watchlist_items()})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500
@app.post("/api/watchlist")
def api_watchlist_add():
    try:
        body=request.get_json(force=True,silent=True) or {}; ticker=body.get("ticker")
        if not ticker:return jsonify({"ok":False,"error":"ticker required"}),400
        return jsonify({"ok":True,**add_watchlist_item(ticker,body.get("added_price"))})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500
@app.delete("/api/watchlist/<ticker>")
def api_watchlist_remove(ticker):
    try:return jsonify({"ok":True,**remove_watchlist_item(ticker)})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500
@app.get("/api/source-health")
def api_source_health():
    try:return jsonify({"ok":True,"sources":source_health_snapshot()})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500
@app.get("/api/macro-calendar")
def api_macro_calendar():
    try:return jsonify({"ok":True,"events":upcoming_macro_events(int(request.args.get("within_days",60)))})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500

def auth_required():''','endpoints')
p.write_text(s)
