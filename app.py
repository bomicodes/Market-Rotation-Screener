
from flask import Flask, jsonify, request, Response, session, redirect
from concurrent.futures import ThreadPoolExecutor, as_completed
import io, math, time, traceback, os, sqlite3, json, hmac, threading
from urllib.parse import quote
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import requests
import yfinance as yf

app = Flask(__name__)
APP_VERSION = "26.3"
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
PORT = int(os.environ.get("PORT", "8765"))
SCREENER_PASSWORD = os.environ.get("SCREENER_PASSWORD", "").strip()
UW_API_TOKEN = os.environ.get("UW_API_TOKEN", "").strip()
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
ALPACA_API_KEY = os.environ.get("APCA_API_KEY_ID", os.environ.get("ALPACA_API_KEY", "")).strip()
ALPACA_API_SECRET = os.environ.get("APCA_API_SECRET_KEY", os.environ.get("ALPACA_API_SECRET", "")).strip()
ALPACA_TRADING_BASE_URL = os.environ.get("ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
ALPACA_OPTIONS_FEED = os.environ.get("ALPACA_OPTIONS_FEED", "opra").strip().lower() or "opra"
ALPACA_STOCK_FEED = os.environ.get("ALPACA_STOCK_FEED", "sip").strip().lower() or "sip"
FLOW_MIN_PREMIUM = float(os.environ.get("FLOW_MIN_PREMIUM", "25000"))
FLOW_MAX_CANDIDATES = int(os.environ.get("FLOW_MAX_CANDIDATES", "1200"))
FLOW_ACTIVITY_COVERAGE_TARGET = float(os.environ.get("FLOW_ACTIVITY_COVERAGE_TARGET", "99.5"))
FLOW_TRADE_WORKERS = max(1, min(6, int(os.environ.get("FLOW_TRADE_WORKERS", "4"))))


SECTORS = {
    "XLK":"Technology",
    "XLC":"Communication Services",
    "XLY":"Consumer Discretionary",
    "XLF":"Financials",
    "XLI":"Industrials",
    "XLB":"Materials",
    "XLE":"Energy",
    "XLV":"Health Care",
    "XLP":"Consumer Staples",
    "XLU":"Utilities",
    "XLRE":"Real Estate",
}

# Broader Layer-1 RRG universe. Core sectors can be drilled into using State Street
# holdings; industry/theme ETFs are used to identify more specific leadership.
INDUSTRIES = {
    "SMH":"Semiconductors",
    "IGV":"Software",
    "XBI":"Biotech",
    "IBB":"Biotechnology",
    "ITB":"Homebuilders",
    "XRT":"Retail",
    "KRE":"Regional Banks",
    "XME":"Metals & Mining",
    "XOP":"Oil & Gas Exploration",
    "OIH":"Oil Services",
    "IYT":"Transportation",
    "ITA":"Aerospace & Defense",
    "TAN":"Solar",
    "PBW":"Clean Energy",
}
RRG_UNIVERSE = {**SECTORS, **INDUSTRIES}

MACRO_BASKETS = {
    "rate": ["XLK","XLC","XLU","XLRE","XLP"],
    "cyclical": ["XLY","XLI","XLF","XLB"],
    "defensive": ["XLV","XLP","XLU"],
    "inflation": ["XLE","XLB"],
}

SECTOR_HOLDING_SUPPLEMENTS = {
    "XLB": [{"ticker": "B", "name": "Barrick Mining Corporation", "weight": None}],
}

def apply_sector_supplements(etf, holdings):
    out=[dict(h) for h in holdings]
    seen={str(h.get("ticker") or h.get("symbol") or "").upper() for h in out}
    for s in SECTOR_HOLDING_SUPPLEMENTS.get(etf, []):
        sym=str(s.get("ticker") or "").upper()
        if sym and sym not in seen:
            out.append(dict(s))
            seen.add(sym)
    return out

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SETUP_DB_PATH = os.environ.get("SETUP_DB_PATH", "/tmp/market_rotation_setups.sqlite3")

def _setup_storage_backend():
    return "postgresql" if DATABASE_URL else "sqlite"

def _setup_db():
    """Open the historical-setup store lazily.

    Production: set DATABASE_URL to a managed PostgreSQL connection string.
    Local/dev fallback: SQLite at SETUP_DB_PATH. No database connection is made
    during app startup, preserving the app's zero-network startup behavior.
    """
    if DATABASE_URL:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed. Run pip install -r requirements.txt.") from e
        con=psycopg.connect(DATABASE_URL, connect_timeout=10, row_factory=dict_row)
        con.execute("""CREATE TABLE IF NOT EXISTS setup_snapshots(
          id BIGSERIAL PRIMARY KEY, captured_at TEXT NOT NULL, trade_date TEXT NOT NULL,
          ticker TEXT NOT NULL, spot DOUBLE PRECISION, bias TEXT, score DOUBLE PRECISION, signature TEXT,
          raw_json TEXT NOT NULL, UNIQUE(trade_date,ticker,signature))""")
        con.execute("""CREATE TABLE IF NOT EXISTS watchlist_items(
          ticker TEXT PRIMARY KEY, added_at TEXT NOT NULL, added_price DOUBLE PRECISION)""")
        con.commit()
        return con

    con=sqlite3.connect(SETUP_DB_PATH, timeout=10)
    con.row_factory=sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS setup_snapshots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, trade_date TEXT NOT NULL,
      ticker TEXT NOT NULL, spot REAL, bias TEXT, score REAL, signature TEXT,
      raw_json TEXT NOT NULL, UNIQUE(trade_date,ticker,signature))""")
    con.execute("""CREATE TABLE IF NOT EXISTS watchlist_items(
      ticker TEXT PRIMARY KEY, added_at TEXT NOT NULL, added_price REAL)""")
    con.commit()
    return con

def save_setup_snapshot(payload):
    ticker=str(payload.get("ticker") or "").upper().strip()
    if not ticker: raise ValueError("ticker required")
    raw=payload.get("raw") or {}
    signature=str(payload.get("signature") or "unclassified")[:240]
    now=datetime.utcnow().isoformat(timespec="seconds")+"Z"
    trade_date=str(payload.get("trade_date") or pd.Timestamp.now().date())
    values=(now,trade_date,ticker,_safe_float(payload.get("spot")),str(payload.get("bias") or "neutral"),_safe_float(payload.get("score")),signature,json.dumps(raw,separators=(",",":"),default=str))
    backend=_setup_storage_backend()
    with _setup_db() as con:
        if backend=="postgresql":
            con.execute("""INSERT INTO setup_snapshots(captured_at,trade_date,ticker,spot,bias,score,signature,raw_json)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(trade_date,ticker,signature) DO UPDATE SET
                captured_at=EXCLUDED.captured_at, spot=EXCLUDED.spot, bias=EXCLUDED.bias,
                score=EXCLUDED.score, raw_json=EXCLUDED.raw_json""", values)
        else:
            con.execute("INSERT OR REPLACE INTO setup_snapshots(captured_at,trade_date,ticker,spot,bias,score,signature,raw_json) VALUES(?,?,?,?,?,?,?,?)", values)
        con.commit()
    return {"ticker":ticker,"trade_date":trade_date,"signature":signature,"storage":backend}

def setup_history_stats(ticker, signature=None):
    ticker=ticker.upper().strip(); backend=_setup_storage_backend()
    if backend=="postgresql":
        q="SELECT * FROM setup_snapshots WHERE ticker=%s"; args=[ticker]
        if signature: q+=" AND signature=%s"; args.append(signature)
    else:
        q="SELECT * FROM setup_snapshots WHERE ticker=?"; args=[ticker]
        if signature: q+=" AND signature=?"; args.append(signature)
    q+=" ORDER BY trade_date DESC LIMIT 250"
    with _setup_db() as con:
        cur=con.execute(q,args)
        rows=[dict(x) for x in cur.fetchall()]
    if not rows:return {"count":0,"completed":0,"returns":{},"storage":backend}
    px=dl_ohlc(ticker,"3y")
    stats={h:[] for h in (1,3,5,10)}; mfe=[]; mae=[]
    if px is not None and len(px):
      px=px.dropna(subset=["Close"]).copy(); idx=pd.DatetimeIndex(px.index).tz_localize(None) if getattr(px.index,'tz',None) is not None else pd.DatetimeIndex(px.index)
      for r in rows:
        d=pd.Timestamp(r["trade_date"]); pos=idx.searchsorted(d,side="right")-1
        if pos<0 or pos>=len(px):continue
        base=_safe_float(r.get("spot")) or float(px["Close"].iloc[pos])
        bias=str(r.get("bias") or "neutral").lower()
        direction_sign=-1.0 if bias.startswith("bear") else 1.0
        for h in stats:
          if pos+h<len(px):
            raw_ret=(float(px["Close"].iloc[pos+h])/base-1)*100
            stats[h].append(raw_ret*direction_sign)
        end=min(len(px),pos+11)
        if end>pos+1:
          seg=px.iloc[pos+1:end]
          if direction_sign>0:
            favorable=(float(seg["High"].max())/base-1)*100
            adverse=(float(seg["Low"].min())/base-1)*100
          else:
            favorable=-(float(seg["Low"].min())/base-1)*100
            adverse=-(float(seg["High"].max())/base-1)*100
          mfe.append(favorable); mae.append(adverse)
    out={}
    for h,v in stats.items(): out[str(h)]={"n":len(v),"win_rate":round(100*sum(x>0 for x in v)/len(v),1) if v else None,"median":round(float(np.median(v)),2) if v else None}
    storage=("PostgreSQL (persistent)" if backend=="postgresql" else f"SQLite fallback: {SETUP_DB_PATH}")
    return {"count":len(rows),"completed":max([len(v) for v in stats.values()] or [0]),"returns":out,"median_mfe_10d":round(float(np.median(mfe)),2) if mfe else None,"median_mae_10d":round(float(np.median(mae)),2) if mae else None,"storage":storage}

def list_watchlist_items():
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
CACHE_TTL = 60 * 15
# Keep the in-process cache bounded. Options/flow payloads can be large, and an
# unbounded dict lets normal ticker exploration slowly push a small Render
# instance toward its memory limit. Oldest entries are evicted first.
CACHE_MAX_ENTRIES = max(20, int(os.environ.get("CACHE_MAX_ENTRIES", "80")))
_CACHE_LOCKS = {}
_CACHE_LOCKS_GUARD = threading.Lock()

def _trim_cache(force=False):
    max_entries=CACHE_MAX_ENTRIES
    if not force and len(CACHE) <= max_entries:
        return 0
    target=max(1, int(max_entries * 0.80))
    remove_n=max(0, len(CACHE)-target)
    if remove_n <= 0:
        return 0
    def _stamp(item):
        try:return float(item[1][0])
        except Exception:return 0.0
    victims=sorted(CACHE.items(), key=_stamp)[:remove_n]
    for k,_ in victims:
        CACHE.pop(k,None)
    return len(victims)

def _cache_lock(key):
    # One lock per cache key so unrelated keys never block each other. Prune
    # before allocating another lock so both cached payloads and lock metadata
    # stay bounded over long-running sessions.
    _trim_cache()
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CACHE_LOCKS[key] = lock
        if len(_CACHE_LOCKS) > CACHE_MAX_ENTRIES * 2:
            for old_key in list(_CACHE_LOCKS):
                if old_key == key or old_key in CACHE:
                    continue
                old_lock=_CACHE_LOCKS.get(old_key)
                if old_lock is not None and not old_lock.locked():
                    _CACHE_LOCKS.pop(old_key,None)
                if len(_CACHE_LOCKS) <= CACHE_MAX_ENTRIES * 2:
                    break
        return lock

_SOURCE_HEALTH = {}
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

def cached(key, fn, ttl=CACHE_TTL):
    now = time.time()
    hit = CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    with _cache_lock(key):
        now = time.time()
        hit = CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        val = fn()
        CACHE[key] = (now, val)
        return val


def cached_refresh_safe(key, fn, force=False, ttl=CACHE_TTL):
    """Refresh without destroying the last known-good payload."""
    now = time.time()
    hit = CACHE.get(key)
    if hit and not force and now - hit[0] < ttl:
        return hit[1], False, None
    with _cache_lock(key):
        now = time.time()
        hit = CACHE.get(key)
        if hit and not force and now - hit[0] < ttl:
            return hit[1], False, None
        try:
            val = fn()
            CACHE[key] = (now, val)
            return val, False, None
        except Exception as e:
            if hit:
                return hit[1], True, str(e)
            raise

def dl_prices(tickers, period="3y"):
    """Download daily closes with a per-symbol repair pass.

    yfinance can occasionally return a populated multi-ticker frame with one or
    more requested symbols entirely empty.  Older code treated the presence of
    the column as success, which later produced .iloc[-1] out-of-bounds errors.
    """
    tickers = list(dict.fromkeys([t for t in tickers if t]))
    if not tickers:
        return pd.DataFrame()

    last_err = None
    df = None
    for attempt in range(2):
        try:
            df = yf.download(
                tickers=tickers,
                period=period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="column",
                threads=True,
                timeout=30,
            )
            if df is not None and len(df) > 0:
                break
        except Exception as e:
            last_err = e
        time.sleep(1.5 * (attempt + 1))

    close = pd.DataFrame()
    if df is not None and len(df) > 0:
        if isinstance(df.columns, pd.MultiIndex):
            if "Close" in df.columns.get_level_values(0):
                close = df["Close"].copy()
            elif "Adj Close" in df.columns.get_level_values(0):
                close = df["Adj Close"].copy()
        else:
            col = "Close" if "Close" in df.columns else ("Adj Close" if "Adj Close" in df.columns else None)
            if col:
                close = df[[col]].copy()
                close.columns = [tickers[0]]

    if len(close):
        close.index = pd.to_datetime(close.index).tz_localize(None)
        close = close.sort_index()

    # Repair symbols that were omitted or returned as all-NaN in the bulk call.
    missing = []
    for ticker in tickers:
        if ticker not in close.columns or close[ticker].dropna().empty:
            missing.append(ticker)

    repaired = []
    for ticker in missing:
        try:
            one = yf.download(
                ticker,
                period=period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
                timeout=20,
            )
            if one is None or len(one) == 0:
                continue
            if isinstance(one.columns, pd.MultiIndex):
                if "Close" in one.columns.get_level_values(0):
                    s = one["Close"]
                elif "Adj Close" in one.columns.get_level_values(0):
                    s = one["Adj Close"]
                else:
                    continue
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[:, 0]
            else:
                col = "Close" if "Close" in one.columns else ("Adj Close" if "Adj Close" in one.columns else None)
                if not col:
                    continue
                s = one[col]
            s = pd.Series(s, name=ticker).dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            if len(s):
                repaired.append(s)
        except Exception:
            continue

    if repaired:
        repair_df = pd.concat(repaired, axis=1)
        close = repair_df if close.empty else close.combine_first(repair_df)
        # combine_first keeps existing bulk values but adds repaired symbols/dates.
        for c in repair_df.columns:
            if c not in close.columns:
                close[c] = repair_df[c]

    if close.empty or close.dropna(how="all").empty:
        _mark_source("yfinance", False, last_err)
        raise RuntimeError("Price provider returned no usable data" + (f": {last_err}" if last_err else "."))

    _mark_source("yfinance", True)
    return close.sort_index().dropna(how="all")

def _yf_download_retry(ticker, period, interval="1d", timeout=12, attempts=2, prepost=False):
    """Coalesced Yahoo fetch with stale-on-error protection.

    Chart, STRAT and institutional modules often ask for the same symbol within
    milliseconds. One upstream request now owns each ticker/period/interval key;
    concurrent callers wait for it and reuse the result. Successful frames remain
    fresh for five minutes and stale frames remain eligible for 24 hours if Yahoo
    is rate-limited or temporarily unavailable.
    """
    ticker=str(ticker or "").upper().strip()
    key=f"yf-frame-v25:{ticker}:{period}:{interval}:{1 if prepost else 0}"
    now=time.time(); hit=CACHE.get(key)
    fresh_ttl=300
    stale_ttl=86400
    if hit and now-hit[0] < fresh_ttl:
        return hit[1].copy()

    with _cache_lock(key):
        now=time.time(); hit=CACHE.get(key)
        if hit and now-hit[0] < fresh_ttl:
            return hit[1].copy()
        last=pd.DataFrame(); last_err=None
        for attempt in range(max(1, attempts)):
            try:
                df=yf.download(
                    ticker, period=period, interval=interval, auto_adjust=True,
                    progress=False, threads=False, prepost=prepost, timeout=timeout
                )
                if df is not None and len(df):
                    if isinstance(df.columns,pd.MultiIndex):
                        df.columns=[c[0] for c in df.columns]
                    df.index=pd.to_datetime(df.index).tz_localize(None)
                    df=df.sort_index()
                    CACHE[key]=(time.time(),df.copy())
                    _mark_source("yfinance",True)
                    return df
                last=df if df is not None else pd.DataFrame()
            except Exception as e:
                last_err=e
                # A retry immediately after a provider 429 usually worsens the
                # rate-limit window. Prefer stale data when we have it.
                msg=str(e).lower()
                if "429" in msg or "rate" in msg or "too many requests" in msg:
                    break
            if attempt+1 < attempts:
                time.sleep(0.8*(attempt+1))

        _mark_source("yfinance",False,last_err or "empty response")
        hit=CACHE.get(key)
        if hit and now-hit[0] < stale_ttl:
            return hit[1].copy()
        return last if last is not None else pd.DataFrame()

def _period_days(period):
    p=str(period or "").lower()
    table={"5d":8,"1mo":35,"1m":35,"3mo":105,"3m":105,"6mo":205,"6m":205,
           "1y":370,"18mo":560,"2y":740,"3y":1110,"4y":1480,"5y":1850}
    return table.get(p,1110)

def _alpaca_daily_ohlc(ticker, period="3y"):
    ticker=str(ticker or "").upper().strip()
    key=f"alpaca-day-v25-1:{ALPACA_STOCK_FEED}:{ticker}:{period}"
    now=time.time(); hit=CACHE.get(key); fresh_ttl=300; stale_ttl=86400
    if hit and now-hit[0] < fresh_ttl:return hit[1].copy()
    if not (ALPACA_API_KEY and ALPACA_API_SECRET):return pd.DataFrame()
    with _cache_lock(key):
        now=time.time(); hit=CACHE.get(key)
        if hit and now-hit[0] < fresh_ttl:return hit[1].copy()
        try:
            from zoneinfo import ZoneInfo
            end=datetime.now(ZoneInfo("America/New_York")); start=end-timedelta(days=_period_days(period))
            url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
            params={"timeframe":"1Day","start":start.isoformat(),"end":end.isoformat(),
                    "adjustment":"raw","feed":ALPACA_STOCK_FEED,"sort":"asc","limit":10000}
            raw=[]; token=None
            for _ in range(4):
                if token:params["page_token"]=token
                r=requests.get(url,params=params,headers=alpaca_headers(),timeout=20)
                r.raise_for_status(); j=r.json() or {}; raw.extend(j.get("bars") or [])
                token=j.get("next_page_token") or j.get("page_token")
                if not token:break
            if raw:
                rows=[]; idx=[]
                for b in raw:
                    try:
                        idx.append(pd.Timestamp(b.get("t")).tz_convert("America/New_York").tz_localize(None))
                        rows.append({"Open":float(b.get("o")),"High":float(b.get("h")),"Low":float(b.get("l")),
                                     "Close":float(b.get("c")),"Volume":float(b.get("v") or 0)})
                    except Exception:pass
                if rows:
                    df=pd.DataFrame(rows,index=pd.DatetimeIndex(idx)).sort_index()
                    CACHE[key]=(time.time(),df.copy()); _mark_source("alpaca_stocks",True); return df
        except Exception as e:
            _mark_source("alpaca_stocks",False,e)
        hit=CACHE.get(key)
        if hit and now-hit[0] < stale_ttl:return hit[1].copy()
        return pd.DataFrame()

def dl_ohlc(ticker, period="3y"):
    # Paid consolidated SIP is canonical for deep-dive OHLC. Yahoo is a fallback,
    # not a parallel dependency, which removes most rate-limit failures.
    df=_alpaca_daily_ohlc(ticker,period)
    if df is not None and len(df):return df
    return _yf_download_retry(ticker,period,"1d",timeout=10,attempts=1)



def _nice_profile_step(raw_step):
    raw_step=max(0.01,float(raw_step or 0.01))
    exp=math.floor(math.log10(raw_step))
    base=10**exp
    frac=raw_step/base
    nice=1 if frac<=1 else 2 if frac<=2 else 2.5 if frac<=2.5 else 5 if frac<=5 else 10
    return max(0.01,nice*base)

def _profile_from_intraday_bars(bars, session_date, rows_count=64, value_area_pct=68):
    if not bars:
        return None
    lows=[float(b["l"]) for b in bars if b.get("l") is not None]
    highs=[float(b["h"]) for b in bars if b.get("h") is not None]
    if not lows or not highs:
        return None
    lo,hi=min(lows),max(highs)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi<=lo:
        return None

    rows_count=max(24,min(96,int(rows_count or 64)))
    edges=np.linspace(lo,hi,rows_count+1,dtype=float)
    vols=np.zeros(rows_count,dtype=float)

    for b in bars:
        bh=float(b.get("h") or 0); bl=float(b.get("l") or 0)
        bc=float(b.get("c") or (bh+bl)/2); vol=float(b.get("v") or 0)
        if vol<=0 or not np.isfinite(vol):
            continue
        if bh<=bl:
            k=int(np.searchsorted(edges,bc,side="right")-1)
            k=max(0,min(rows_count-1,k)); vols[k]+=vol
            continue
        i0=max(0,int(np.searchsorted(edges,bl,side="right")-1))
        i1=min(rows_count-1,int(np.searchsorted(edges,bh,side="left")))
        touched=[]; total_overlap=0.0
        for i in range(i0,i1+1):
            ov=max(0.0,min(bh,edges[i+1])-max(bl,edges[i]))
            if ov>0:
                touched.append((i,ov)); total_overlap+=ov
        if total_overlap<=0:
            k=int(np.searchsorted(edges,bc,side="right")-1)
            k=max(0,min(rows_count-1,k)); vols[k]+=vol
        else:
            for i,ov in touched:
                vols[i]+=vol*(ov/total_overlap)

    total=float(vols.sum())
    if total<=0:
        return None

    centers=(edges[:-1]+edges[1:])/2.0
    poc_idx=int(np.argmax(vols))
    target=total*(float(value_area_pct)/100.0)
    chosen={poc_idx}; accum=float(vols[poc_idx])
    left,right=poc_idx-1,poc_idx+1
    while accum<target and (left>=0 or right<rows_count):
        lv=float(vols[left]) if left>=0 else -1.0
        rv=float(vols[right]) if right<rows_count else -1.0
        if rv>lv:
            chosen.add(right); accum+=max(0.0,rv); right+=1
        else:
            chosen.add(left); accum+=max(0.0,lv); left-=1

    low_idx,high_idx=min(chosen),max(chosen)
    return {
        "session":str(session_date),
        "poc":round(float(centers[poc_idx]),4),
        "vah":round(float(edges[high_idx+1]),4),
        "val":round(float(edges[low_idx]),4),
        "row_count":rows_count,
        "row_size":round(float((hi-lo)/rows_count),6),
        "value_area_pct":int(value_area_pct),
        "total_volume":int(total),
        "source":f"Alpaca {ALPACA_STOCK_FEED.upper()} 1Min bars",
        "method":f"Lower-timeframe OHLCV volume distributed across {rows_count} price rows",
        "bins":[{"price":round(float(centers[i]),4),"volume":int(round(vols[i]))} for i in range(rows_count)]
    }


def alpaca_session_volume_profiles(ticker):
    """Latest/prior sessions plus current/prior week composite profiles."""
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        return {"session":None,"previous":None,"current_week":None,"previous_week":None,"error":"Alpaca is not configured."}
    try:
        from zoneinfo import ZoneInfo
        et=ZoneInfo("America/New_York")
        now=datetime.now(et)
        url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
        params={
            "timeframe":"1Min",
            "start":(now-timedelta(days=20)).isoformat(),
            "end":now.isoformat(),
            "adjustment":"raw",
            "feed":ALPACA_STOCK_FEED,
            "sort":"asc",
            "limit":10000
        }
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=25)
        if r.status_code in (401,403):
            try: detail=(r.json() or {}).get("message") or r.text
            except Exception: detail=r.text
            return {"session":None,"previous":None,"current_week":None,"previous_week":None,
                    "error":f"Alpaca stock-bar access rejected: {detail or r.status_code}"}
        r.raise_for_status()
        raw=(r.json() or {}).get("bars") or []

        sessions={}
        week_bars={}
        for b in raw:
            ts=b.get("t")
            if not ts: continue
            try:
                dt=pd.Timestamp(ts)
                if dt.tzinfo is None: dt=dt.tz_localize("UTC")
                dt=dt.tz_convert("America/New_York")
            except Exception:
                continue
            mins=dt.hour*60+dt.minute
            if mins<570 or mins>=960:
                continue
            d=dt.date()
            sessions.setdefault(d,[]).append(b)
            iso=dt.isocalendar()
            wk=(int(iso.year),int(iso.week))
            week_bars.setdefault(wk,[]).append(b)

        dates=sorted(d for d,v in sessions.items() if v)
        if not dates:
            return {"session":None,"previous":None,"current_week":None,"previous_week":None,
                    "error":"No regular-session Alpaca bars returned."}
        latest=dates[-1]
        previous=dates[-2] if len(dates)>1 else None

        weeks=sorted(k for k,v in week_bars.items() if v)
        current_wk=weeks[-1] if weeks else None
        previous_wk=weeks[-2] if len(weeks)>1 else None

        current_week_profile=_profile_from_intraday_bars(week_bars[current_wk],f"{current_wk[0]}-W{current_wk[1]:02d}") if current_wk else None
        previous_week_profile=_profile_from_intraday_bars(week_bars[previous_wk],f"{previous_wk[0]}-W{previous_wk[1]:02d}") if previous_wk else None
        if current_week_profile:
            current_week_profile["source"]=f"Alpaca {ALPACA_STOCK_FEED.upper()} 1Min weekly composite"
        if previous_week_profile:
            previous_week_profile["source"]=f"Alpaca {ALPACA_STOCK_FEED.upper()} 1Min weekly composite"

        return {
            "session":_profile_from_intraday_bars(sessions[latest],latest),
            "previous":_profile_from_intraday_bars(sessions[previous],previous) if previous else None,
            "current_week":current_week_profile,
            "previous_week":previous_week_profile,
            "error":None
        }
    except Exception as e:
        return {"session":None,"previous":None,"current_week":None,"previous_week":None,"error":str(e)}

def alpaca_visible_profiles(ticker, period, chart_timeframe):
    """Build one profile per visible RTH session (or per week for 1W).

    Resolution is chosen to keep the request practical:
      1M range -> 1Min bars
      3M/6M range -> 5Min bars
    This lets a daily chart show a separate profile for essentially every
    displayed candle rather than one large profile for the entire window.
    """
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        return {"sessions":[],"weeks":[],"source":None,"error":"Alpaca is not configured."}

    try:
        from zoneinfo import ZoneInfo
        et=ZoneInfo("America/New_York")
        now=datetime.now(et)

        days={"1m":35,"3m":105,"6m":205}.get(period,35)
        source_tf="1Min" if period=="1m" else "5Min"

        url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
        params={
            "timeframe":source_tf,
            "start":(now-timedelta(days=days)).isoformat(),
            "end":now.isoformat(),
            "adjustment":"raw",
            "feed":ALPACA_STOCK_FEED,
            "sort":"asc",
            "limit":10000
        }
        raw=[]; token=None
        for _ in range(6):
            if token: params["page_token"]=token
            r=requests.get(url,params=params,headers=alpaca_headers(),timeout=30)
            if r.status_code in (401,403):
                try: detail=(r.json() or {}).get("message") or r.text
                except Exception: detail=r.text
                _mark_source("alpaca_stocks", False, detail or r.status_code)
                return {"sessions":[],"weeks":[],"source":source_tf,
                        "error":f"Alpaca stock-bar access rejected: {detail or r.status_code}"}
            r.raise_for_status()
            j=r.json() or {}
            raw.extend(j.get("bars") or [])
            token=j.get("next_page_token") or j.get("page_token")
            if not token: break

        sessions={}
        weeks={}
        for b in raw:
            ts=b.get("t")
            if not ts:
                continue
            try:
                dt=pd.Timestamp(ts)
                if dt.tzinfo is None:
                    dt=dt.tz_localize("UTC")
                dt=dt.tz_convert("America/New_York")
            except Exception:
                continue
            mins=dt.hour*60+dt.minute
            if mins<570 or mins>=960:   # 09:30–16:00 ET
                continue
            d=dt.date()
            sessions.setdefault(d,[]).append(b)
            iso=dt.isocalendar()
            wk=(int(iso.year),int(iso.week))
            weeks.setdefault(wk,[]).append(b)

        # Fewer rows per session makes the profiles visibly useful beside
        # individual candles; the underlying lower-timeframe bars remain intact.
        if chart_timeframe in ("1h","4h"):
            session_rows=32
        else:
            session_rows=40

        session_items=[]
        for d in sorted(sessions):
            p=_profile_from_intraday_bars(sessions[d],d,rows_count=session_rows,value_area_pct=68)
            if p:
                p["source"]=f"Alpaca {ALPACA_STOCK_FEED.upper()} {source_tf} RTH"
                session_items.append({"date":str(d),"profile":p})

        week_items=[]
        for wk in sorted(weeks):
            label=f"{wk[0]}-W{wk[1]:02d}"
            p=_profile_from_intraday_bars(weeks[wk],label,rows_count=52,value_area_pct=68)
            if p:
                p["source"]=f"Alpaca {ALPACA_STOCK_FEED.upper()} {source_tf} weekly composite"
                week_items.append({"week":label,"profile":p})

        _mark_source("alpaca_stocks", True)
        return {"sessions":session_items,"weeks":week_items,"source":source_tf,"error":None}
    except Exception as e:
        _mark_source("alpaca_stocks", False, e)
        return {"sessions":[],"weeks":[],"source":None,"error":str(e)}

def _period_start_et(period):
    from zoneinfo import ZoneInfo
    now=datetime.now(ZoneInfo("America/New_York"))
    days={"1m":35,"3m":100,"6m":200}.get(period,35)
    return now-timedelta(days=days),now

def _canonical_hourly_bars(ticker,period):
    ticker=str(ticker or "").upper().strip(); key=f"alpaca-hour-v25-1:{ALPACA_STOCK_FEED}:{ticker}:{period}"
    now=time.time(); hit=CACHE.get(key); fresh_ttl=120; stale_ttl=21600
    if hit and now-hit[0] < fresh_ttl:return [dict(x) for x in hit[1]]
    with _cache_lock(key):
        now=time.time(); hit=CACHE.get(key)
        if hit and now-hit[0] < fresh_ttl:return [dict(x) for x in hit[1]]
        parsed=[]
        if ALPACA_API_KEY and ALPACA_API_SECRET:
            try:
                start_dt,end_dt=_period_start_et(period); url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
                params={"timeframe":"1Hour","start":start_dt.isoformat(),"end":end_dt.isoformat(),
                        "adjustment":"raw","feed":ALPACA_STOCK_FEED,"sort":"asc","limit":10000}
                raw=[]; token=None
                for _ in range(3):
                    if token:params["page_token"]=token
                    r=requests.get(url,params=params,headers=alpaca_headers(),timeout=20); r.raise_for_status()
                    j=r.json() or {}; raw.extend(j.get("bars") or []); token=j.get("next_page_token") or j.get("page_token")
                    if not token:break
                for b in raw:
                    try:
                        dt=pd.Timestamp(b.get("t"));
                        if dt.tzinfo is None:dt=dt.tz_localize("UTC")
                        dt=dt.tz_convert("America/New_York"); mins=dt.hour*60+dt.minute
                        if mins<570 or mins>=960:continue
                        parsed.append({"dt":dt,"open":float(b.get("o")),"high":float(b.get("h")),
                                       "low":float(b.get("l")),"close":float(b.get("c")),"volume":int(b.get("v") or 0)})
                    except Exception:pass
                if parsed:_mark_source("alpaca_stocks",True)
            except Exception as e:_mark_source("alpaca_stocks",False,e); parsed=[]
        if not parsed:
            hit=CACHE.get(key)
            if hit and now-hit[0] < stale_ttl:return [dict(x) for x in hit[1]]
            try:
                days=_period_days(period); yperiod="1mo" if days<=35 else "3mo" if days<=105 else "6mo"
                df=_yf_download_retry(ticker,yperiod,"60m",timeout=10,attempts=1,prepost=False)
                if df is not None and len(df):
                    for idx,row in df.dropna(subset=["Close"]).iterrows():
                        dt=pd.Timestamp(idx); parsed.append({"dt":dt,"open":float(row.get("Open",row["Close"])),
                           "high":float(row.get("High",row["Close"])),"low":float(row.get("Low",row["Close"])),
                           "close":float(row["Close"]),"volume":int(row.get("Volume") or 0)})
            except Exception:parsed=[]
        if parsed:CACHE[key]=(time.time(),[dict(x) for x in parsed])
        return parsed

def alpaca_chart_bars(ticker,timeframe,period):
    if timeframe not in ("1h","4h"):return []
    parsed=_canonical_hourly_bars(ticker,period)
    if timeframe=="1h":return parsed
    groups={}
    for b in parsed:
        mins=b["dt"].hour*60+b["dt"].minute; slot=0 if mins<810 else 1
        groups.setdefault((b["dt"].date(),slot),[]).append(b)
    out=[]
    for key in sorted(groups):
        g=groups[key]; out.append({"dt":g[0]["dt"],"open":g[0]["open"],"high":max(x["high"] for x in g),
             "low":min(x["low"] for x in g),"close":g[-1]["close"],"volume":sum(x["volume"] for x in g)})
    return out

def sma(arr, n):
    return pd.Series(arr, dtype=float).rolling(n).mean().to_numpy()

def compute_rrg(bench, asset, n1=10, n2=5):
    b = np.asarray(bench, dtype=float)
    a = np.asarray(asset, dtype=float)
    rs = a / b
    rs_sma = sma(rs, n1)
    ratio = 100.0 * rs / rs_sma
    mom_sma = sma(ratio, n2)
    momentum = 100.0 * ratio / mom_sma
    return ratio, momentum

def quadrant(x,y):
    if x >= 100 and y >= 100: return "Leading"
    if x < 100 and y >= 100: return "Improving"
    if x < 100 and y < 100: return "Lagging"
    return "Weakening"

def rrg_rows(prices, bench_ticker, members, n1=10, n2=5, tail=8):
    out = []
    if bench_ticker not in prices.columns:
        raise RuntimeError(f"{bench_ticker} price history is missing.")
    for ticker in members:
        if ticker == bench_ticker or ticker not in prices.columns:
            continue
        pair = prices[[bench_ticker,ticker]].dropna()
        if len(pair) < max(40, n1+n2+tail+5):
            continue
        ratio, mom = compute_rrg(pair[bench_ticker].values, pair[ticker].values, n1, n2)
        valid = np.isfinite(ratio) & np.isfinite(mom)
        idx = np.where(valid)[0]
        if len(idx) < max(5, tail):
            continue
        li, pi = idx[-1], idx[-2]
        x, y = float(ratio[li]), float(mom[li])
        px, py = float(ratio[pi]), float(mom[pi])
        q = quadrant(x,y)
        recent_q = [quadrant(float(ratio[i]), float(mom[i])) for i in idx[-tail:]]
        l_to_i = q == "Improving" and "Lagging" in recent_q[:-1]
        recent_m = [float(mom[i]) for i in idx[-4:]]
        early_turn = q == "Lagging" and len(recent_m) >= 4 and all(recent_m[i] > recent_m[i-1] for i in range(1, len(recent_m)))
        tail_pts = [{"x":float(ratio[i]),"y":float(mom[i])} for i in idx[-tail:]]

        # Recent tail trajectory: compare the latest point with a point 2 bars back
        # (or earliest available recent point). This is intentionally simple:
        # NE = Rotating In, SW = Rotating Out, otherwise Neutral.
        recent_idx = idx[-3:] if len(idx) >= 3 else idx[-2:]
        if len(recent_idx) >= 2:
            ti0, ti1 = recent_idx[0], recent_idx[-1]
            dx_tail = float(ratio[ti1] - ratio[ti0])
            dy_tail = float(mom[ti1] - mom[ti0])
        else:
            dx_tail = dy_tail = 0.0

        eps = 1e-9
        if dx_tail > eps and dy_tail > eps:
            tail_trajectory = "Rotating In"
        elif dx_tail < -eps and dy_tail < -eps:
            tail_trajectory = "Rotating Out"
        else:
            tail_trajectory = "Neutral"
        score = 0.0
        score += {"Leading":3.0,"Improving":2.6,"Weakening":1.0,"Lagging":0.5}[q]
        if x > px: score += 1.4
        if y > py: score += 1.8
        if l_to_i: score += 1.4
        if early_turn: score += 1.0
        if q == "Leading" and x > px and y > py: score += 1.0
        out.append({
            "ticker":ticker,"quadrant":q,"x":round(x,4),"y":round(y,4),
            "rs_up":x>px,"mom_up":y>py,"l_to_i":l_to_i,"early_turn":early_turn,
            "score":round(min(10,score),1),"tail":tail_pts,
            "tail_trajectory":tail_trajectory,
            "tail_dx":round(dx_tail,4),"tail_dy":round(dy_tail,4),
            "date":pair.index[li].strftime("%Y-%m-%d")
        })
    return sorted(out, key=lambda r:(-r["score"],-r["y"],-r["x"]))


def dual_rrg_rows(prices, bench_ticker, members, tail_fast=8, tail_trend=8):
    fast = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 10, 5, tail_fast)}
    trend = {r["ticker"]: r for r in rrg_rows(prices, bench_ticker, members, 25, 12, tail_trend)}
    out = []
    for ticker in members:
        f = fast.get(ticker)
        t = trend.get(ticker)
        if not f:
            continue
        row = dict(f)
        row["fast"] = f
        row["trend"] = t
        out.append(row)
    return out

def alignment_label(stock_fast, stock_trend, group_fast=None, group_trend=None):
    sf = stock_fast or {}
    st = stock_trend or {}
    gf = group_fast or {}
    gt = group_trend or {}

    sfq = sf.get("quadrant")
    stq = st.get("quadrant")
    gfq = gf.get("quadrant")
    gtq = gt.get("quadrant")

    sf_up = sf.get("rs_up") and sf.get("mom_up")
    st_up = st.get("rs_up") and st.get("mom_up")
    group_strong = gfq in ("Leading","Improving") and gf.get("mom_up", False)
    group_weak = gfq in ("Weakening","Lagging")

    if sfq in ("Leading","Improving") and sf_up and stq in ("Leading","Improving") and st_up and group_strong:
        return "FULL ALIGNMENT"
    if sfq == "Leading" and sf_up and group_weak:
        return "STOCK-SPECIFIC LEADER"
    if sfq == "Improving" and sf_up and stq in ("Lagging","Improving"):
        return "EARLY ROTATION"
    if sfq == "Leading" and (not sf.get("mom_up", False)):
        return "LOSING LEADERSHIP"
    if sfq in ("Leading","Improving") and sf_up and (stq in ("Lagging","Weakening") or not st):
        return "SHORT-TERM SURGE"
    if sfq == "Lagging" and sf.get("mom_up", False):
        return "EARLY TURN"
    return "MIXED"

def pct_change(s, n):
    s = pd.Series(s).dropna()
    if len(s) <= n: return None
    return float((s.iloc[-1]/s.iloc[-1-n]-1)*100)

def state_street_holdings(etf):
    etf = etf.upper()
    url = f"https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{etf.lower()}.xlsx"
    resp = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
    resp.raise_for_status()
    raw = pd.read_excel(io.BytesIO(resp.content), header=None, engine="openpyxl")
    header_idx = None
    for i in range(min(50, len(raw))):
        vals = [str(x).strip().lower() for x in raw.iloc[i].tolist()]
        joined = " | ".join(vals)
        if ("ticker" in joined or "symbol" in joined) and "weight" in joined:
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("Could not locate holdings header.")
    df = pd.read_excel(io.BytesIO(resp.content), header=header_idx, engine="openpyxl")
    cols = {str(c).strip().lower():c for c in df.columns}
    tcol = next((orig for low,orig in cols.items() if low in ("ticker","symbol") or "ticker" in low),None)
    wcol = next((orig for low,orig in cols.items() if "weight" in low),None)
    ncol = next((orig for low,orig in cols.items() if low=="name" or "security name" in low),None)
    if not tcol:
        raise RuntimeError("Holdings file did not expose a ticker column.")
    out=[]
    for _,row in df.iterrows():
        t=str(row.get(tcol,"")).strip().upper()
        if not t or t in ("NAN","-","CASH_USD") or len(t)>10: continue
        t=t.replace(".","-")
        weight=None
        if wcol is not None:
            try:
                weight=float(row.get(wcol))
                if weight<=1.0: weight*=100
            except: pass
        name=str(row.get(ncol,"")).strip() if ncol is not None else t
        out.append({"ticker":t,"name":name,"weight":weight})
    seen=set(); cleaned=[]
    for r in sorted(out,key=lambda x:-(x["weight"] if x["weight"] is not None else -1)):
        if r["ticker"] not in seen:
            seen.add(r["ticker"]); cleaned.append(r)
    if len(cleaned)<5:
        raise RuntimeError("Too few usable holdings.")
    return cleaned


# State Street also publishes daily files for several industry/theme SPDRs.
STATE_STREET_FUNDS = set(SECTORS) | {"XBI","XRT","KRE","XME","XOP"}


VANECK_FUNDS = {"SMH","OIH"}


ISHARES_FUNDS = {
    "IGV": ("239771", "ishares-expanded-tech-software-sector-etf"),
    "IBB": ("239699", "ishares-biotechnology-etf"),
    "ITB": ("239512", "ishares-us-home-construction-etf"),
    "IYT": ("239501", "ishares-transportation-average-etf"),
    "ITA": ("239502", "ishares-us-aerospace-defense-etf"),
}

INVESCO_FUNDS = {
    "TAN": "https://www.invesco.com/us/en/financial-products/etfs/invesco-solar-etf.html",
    "PBW": "https://www.invesco.com/us/en/financial-products/etfs/invesco-wilderhill-clean-energy-etf.html",
}

def clean_equity_holdings(rows):
    seen = set()
    out = []
    for r in rows:
        t = str(r.get("ticker","")).strip().upper().replace(".","-")
        if not t or t in ("NAN","-","--","CASH","CASH_USD","USD") or len(t) > 15:
            continue
        # Reject obvious non-ticker labels and many foreign/local exchange codes.
        if " " in t and not t.endswith("-US"):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append({
            "ticker": t,
            "name": str(r.get("name", t)).strip(),
            "weight": r.get("weight")
        })
    return out

def ishares_holdings(etf):
    """Official iShares latest-holdings CSV."""
    etf = etf.upper()
    product = ISHARES_FUNDS.get(etf)
    if not product:
        raise RuntimeError(f"No iShares mapping configured for {etf}.")
    pid, slug = product
    url = f"https://www.ishares.com/us/products/{pid}/{slug}/latest-holdings.csv"
    resp = requests.get(url, timeout=25, headers={"User-Agent":"Mozilla/5.0"})
    resp.raise_for_status()

    # iShares CSV begins with fund metadata; locate the real CSV header.
    raw = resp.text
    lines = raw.splitlines()
    header_idx = None
    for i, line in enumerate(lines[:30]):
        low = line.lower()
        if low.startswith("ticker,") and "weight" in low:
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("Could not locate the iShares holdings CSV header.")
    df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
    cols = {str(c).strip().lower(): c for c in df.columns}
    tcol = next((orig for low,orig in cols.items() if low == "ticker"), None)
    ncol = next((orig for low,orig in cols.items() if low == "name"), None)
    wcol = next((orig for low,orig in cols.items() if "weight" in low), None)
    asset_col = next((orig for low,orig in cols.items() if low == "asset class"), None)
    if tcol is None:
        raise RuntimeError("iShares holdings CSV did not contain a ticker column.")

    rows = []
    for _, row in df.iterrows():
        if asset_col is not None:
            asset = str(row.get(asset_col,"")).lower()
            if "equity" not in asset:
                continue
        weight = None
        if wcol is not None:
            try:
                weight = float(str(row.get(wcol,"")).replace("%","").replace(",",""))
            except Exception:
                pass
        rows.append({
            "ticker": row.get(tcol,""),
            "name": row.get(ncol,row.get(tcol,"")) if ncol is not None else row.get(tcol,""),
            "weight": weight
        })
    rows = clean_equity_holdings(rows)
    if len(rows) < 5:
        raise RuntimeError("iShares returned too few usable holdings.")
    return rows

def invesco_holdings(etf):
    """Attempt official Invesco product-page holdings before falling back."""
    etf = etf.upper()
    url = INVESCO_FUNDS.get(etf)
    if not url:
        raise RuntimeError(f"No Invesco mapping configured for {etf}.")
    resp = requests.get(url, timeout=25, headers={"User-Agent":"Mozilla/5.0"})
    resp.raise_for_status()

    # Invesco may render holdings as HTML tables. Try all tables and locate one
    # with a ticker/symbol column. If their page changes, caller falls back.
    
    try:
        tables = pd.read_html(io.StringIO(resp.text), flavor="lxml")
    except Exception:
        tables = pd.read_html(io.StringIO(resp.text), flavor="bs4")
    candidates = []
    for df in tables:
        cols = {str(c).strip().lower(): c for c in df.columns}
        tcol = next((orig for low,orig in cols.items() if "ticker" in low or low == "symbol"), None)
        if tcol is None:
            continue
        ncol = next((orig for low,orig in cols.items() if low == "name" or "holding" in low), None)
        wcol = next((orig for low,orig in cols.items() if "weight" in low or "% of" in low), None)
        rows = []
        for _, row in df.iterrows():
            weight = None
            if wcol is not None:
                try:
                    weight = float(str(row.get(wcol,"")).replace("%","").replace(",",""))
                except Exception:
                    pass
            rows.append({
                "ticker": row.get(tcol,""),
                "name": row.get(ncol,row.get(tcol,"")) if ncol is not None else row.get(tcol,""),
                "weight": weight,
            })
        rows = clean_equity_holdings(rows)
        if len(rows) > len(candidates):
            candidates = rows
    if len(candidates) < 5:
        raise RuntimeError("Invesco page did not expose a complete ticker holdings table.")
    return candidates

def vaneck_holdings(etf):
    """
    Official VanEck holdings workbook.
    VanEck exposes an XLSX download at each fund's /downloads/holdings/ URL.
    """
    etf = etf.upper()
    fund_urls = {
        "SMH":"https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/downloads/holdings/",
        "OIH":"https://www.vaneck.com/us/en/investments/oil-services-etf-oih/downloads/holdings/",
    }
    url = fund_urls.get(etf)
    if not url:
        raise RuntimeError(f"No VanEck holdings workbook configured for {etf}.")

    headers = {
        "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,*/*",
        "Referer":url.rsplit("/downloads/holdings/",1)[0] + "/",
    }
    resp = requests.get(url, timeout=30, headers=headers)
    resp.raise_for_status()
    if len(resp.content) < 1000:
        raise RuntimeError("VanEck holdings download returned an unexpectedly small response.")

    # VanEck workbooks may have title rows before the actual holdings header.
    raw = pd.read_excel(io.BytesIO(resp.content), header=None)
    header_idx = None
    for i in range(min(30, len(raw))):
        vals = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        joined = " | ".join(vals)
        if "ticker" in joined and ("holding name" in joined or "security name" in joined or "% of net assets" in joined):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("Could not locate the VanEck holdings header.")

    df = pd.read_excel(io.BytesIO(resp.content), header=header_idx)
    cols = {str(c).strip().lower(): c for c in df.columns}

    tcol = next((orig for low,orig in cols.items() if low == "ticker" or "ticker" in low), None)
    ncol = next((orig for low,orig in cols.items() if "holding name" in low or "security name" in low or low == "name"), None)
    wcol = next((orig for low,orig in cols.items() if "% of net assets" in low or "net assets" in low or "weight" in low), None)

    if tcol is None:
        raise RuntimeError("VanEck workbook did not contain a ticker column.")

    rows = []
    for _, row in df.iterrows():
        ticker = str(row.get(tcol,"")).strip()
        if not ticker:
            continue
        weight = None
        if wcol is not None:
            try:
                weight = float(str(row.get(wcol,"")).replace("%","").replace(",","").strip())
                # Excel sometimes stores percentage as decimal fraction.
                if 0 < weight <= 1:
                    weight *= 100
            except Exception:
                pass
        rows.append({
            "ticker": ticker,
            "name": row.get(ncol,ticker) if ncol is not None else ticker,
            "weight": weight
        })

    rows = clean_equity_holdings(rows)
    if len(rows) < 15:
        raise RuntimeError(f"VanEck returned only {len(rows)} usable holdings for {etf}.")
    return rows


def public_full_holdings_fallback(etf):
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

def yahoo_fund_holdings(etf):
    """Generic fallback using yfinance/Yahoo fund top-holdings data."""
    try:
        fd = yf.Ticker(etf).funds_data
        df = fd.top_holdings
        if df is None or len(df) == 0:
            raise RuntimeError("Yahoo did not return fund holdings.")
        work = df.copy()
        if "Symbol" in work.columns:
            symbols = work["Symbol"].astype(str)
        else:
            symbols = pd.Series(work.index.astype(str), index=work.index)
        # Yahoo's common columns are Name and Holding Percent.
        name_col = next((c for c in work.columns if str(c).strip().lower() in ("name","holding name")), None)
        weight_col = next((c for c in work.columns if "holding" in str(c).lower() and "percent" in str(c).lower()), None)
        if weight_col is None:
            weight_col = next((c for c in work.columns if "weight" in str(c).lower() or "percent" in str(c).lower()), None)
        out = []
        for idx, sym in symbols.items():
            t = str(sym).strip().upper().replace(".","-")
            if not t or t in ("NAN","-","CASH") or len(t) > 12:
                continue
            name = str(work.loc[idx, name_col]).strip() if name_col is not None else t
            weight = None
            if weight_col is not None:
                try:
                    weight = float(work.loc[idx, weight_col])
                    if weight <= 1.0:
                        weight *= 100
                except Exception:
                    pass
            out.append({"ticker":t,"name":name,"weight":weight})
        if not out:
            raise RuntimeError("Yahoo returned no usable equity holdings.")
        return out
    except Exception as e:
        raise RuntimeError(f"Could not retrieve holdings for {etf}: {e}")


def finnhub_etf_holdings(etf):
    """
    Full ETF holdings/constituents from Finnhub.
    Used as a universal full-universe fallback when an issuer feed is unavailable.
    Requires the FINNHUB_API_KEY already used by the earnings calendar.
    """
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY is not configured.")

    etf = etf.upper()
    url = "https://finnhub.io/api/v1/etf/holdings"
    all_rows = []
    seen_assets = set()

    # Finnhub returns up to 100 holdings per call with skip-based pagination.
    # Continue until the provider signals the true end of the fund, bounded by a
    # generous safety ceiling so malformed pagination cannot loop forever.
    skip = 0
    for _ in range(30):  # safety ceiling: up to 3,000 holdings
        params = {"symbol":etf, "skip":skip, "token":FINNHUB_API_KEY}
        resp = requests.get(url, params=params, timeout=25, headers={"User-Agent":"MarketRotationScreener/1.0"})
        resp.raise_for_status()
        payload = resp.json() or {}

        rows = payload.get("holdings") if isinstance(payload, dict) else None
        if rows is None and isinstance(payload, list):
            rows = payload
        rows = rows or []
        if not rows:
            break

        new_count = 0
        for r in rows:
            ticker = (
                r.get("symbol")
                or r.get("asset")
                or r.get("ticker")
                or r.get("code")
                or ""
            )
            ticker = str(ticker).strip().upper().replace(".","-")
            if not ticker or ticker in seen_assets:
                continue
            seen_assets.add(ticker)
            new_count += 1

            name = r.get("name") or r.get("description") or ticker
            weight = (
                r.get("percent")
                if r.get("percent") is not None
                else r.get("weight")
            )
            try:
                weight = float(weight) if weight is not None else None
                if weight is not None and 0 < weight <= 1:
                    weight *= 100
            except Exception:
                weight = None

            all_rows.append({
                "ticker":ticker,
                "name":name,
                "weight":weight
            })

        if new_count == 0 or len(rows) < 100:
            break
        skip += 100

    all_rows = clean_equity_holdings(all_rows)
    if len(all_rows) < 10:
        raise RuntimeError(f"Finnhub returned only {len(all_rows)} usable holdings for {etf}.")
    return all_rows


def _get_fund_holdings_live(etf):
    """
    Holdings source priority:
      1) Official issuer feed
      2) Finnhub FULL ETF holdings
      3) Yahoo TOP holdings only as a last-resort partial fallback
    """
    etf = etf.upper()
    attempts = []

    if etf in STATE_STREET_FUNDS:
        try:
            h = state_street_holdings(etf)
            return h, "State Street official daily holdings"
        except Exception as e:
            attempts.append(f"State Street: {e}")

    if etf in ISHARES_FUNDS:
        try:
            h = ishares_holdings(etf)
            return h, "iShares official latest-holdings CSV"
        except Exception as e:
            attempts.append(f"iShares: {e}")

    if etf in VANECK_FUNDS:
        try:
            h = vaneck_holdings(etf)
            return h, "VanEck official holdings XLS"
        except Exception as e:
            attempts.append(f"VanEck: {e}")

    if etf in INVESCO_FUNDS:
        try:
            h = invesco_holdings(etf)
            # Invesco pages can be role/cookie-gated. Only accept a meaningful list.
            if len(h) >= 15:
                return h, "Invesco official product holdings"
            if len(h) >= 8:
                return h, "Invesco official top holdings fallback (PARTIAL)"
            attempts.append(f"Invesco: only {len(h)} usable rows")
        except Exception as e:
            attempts.append(f"Invesco: {e}")

    # Public full-list fallback for Invesco theme funds whose product pages may
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
        return h, "Finnhub FULL ETF holdings fallback"
    except Exception as e:
        attempts.append(f"Finnhub: {e}")

    # Last resort only.
    try:
        holdings = yahoo_fund_holdings(etf)
        return holdings, "Yahoo Finance TOP holdings fallback (PARTIAL)"
    except Exception as e:
        attempts.append(f"Yahoo: {e}")
        raise RuntimeError(f"Could not retrieve holdings for {etf}. Live providers are temporarily unavailable and no cached holdings are available yet.")



def _ensure_holdings_cache_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS holdings_cache(
      etf TEXT PRIMARY KEY, updated_at TEXT NOT NULL, source TEXT, raw_json TEXT NOT NULL)""")

def _save_holdings_cache(etf, holdings, source):
    if not holdings:
        return
    try:
        backend=_setup_storage_backend(); now=datetime.utcnow().isoformat(timespec="seconds")+"Z"
        raw=json.dumps(holdings,separators=(",",":"),default=str)
        with _setup_db() as con:
            _ensure_holdings_cache_table(con)
            if backend=="postgresql":
                con.execute("""INSERT INTO holdings_cache(etf,updated_at,source,raw_json) VALUES(%s,%s,%s,%s)
                  ON CONFLICT(etf) DO UPDATE SET updated_at=EXCLUDED.updated_at, source=EXCLUDED.source, raw_json=EXCLUDED.raw_json""",
                  (etf,now,source,raw))
            else:
                con.execute("INSERT OR REPLACE INTO holdings_cache(etf,updated_at,source,raw_json) VALUES(?,?,?,?)",
                  (etf,now,source,raw))
            con.commit()
    except Exception:
        pass

def _load_holdings_cache(etf):
    try:
        backend=_setup_storage_backend()
        with _setup_db() as con:
            _ensure_holdings_cache_table(con)
            q="SELECT updated_at,source,raw_json FROM holdings_cache WHERE etf=%s" if backend=="postgresql" else "SELECT updated_at,source,raw_json FROM holdings_cache WHERE etf=?"
            row=con.execute(q,(etf,)).fetchone()
            if not row:
                return None
            row=dict(row); holdings=json.loads(row.get("raw_json") or "[]")
            if not isinstance(holdings,list) or len(holdings)<5:
                return None
            return holdings,row.get("source"),row.get("updated_at")
    except Exception:
        return None

def get_fund_holdings(etf):
    """Live issuer-first holdings with a persistent last-known-good safety net.

    A temporary issuer/Finnhub/Yahoo outage must not blank the stock screen or
    force a market-wide Post-Earnings scan to retry the same broken providers.
    """
    etf=str(etf or "").upper().strip()
    try:
        holdings,source=_get_fund_holdings_live(etf)
        if holdings:
            _save_holdings_cache(etf,holdings,source)
        return holdings,source
    except Exception as live_err:
        cached_row=_load_holdings_cache(etf)
        if cached_row:
            holdings,source,updated_at=cached_row
            return holdings,f"Cached holdings · {source or 'last known good'} · {updated_at}"
        raise live_err



def finnhub_earnings_calendar(start_date, end_date):
    """
    Finnhub free-tier earnings calendar. Returns a ticker -> metadata mapping.
    Endpoint: /api/v1/calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    if not FINNHUB_API_KEY:
        return {}
    url = "https://finnhub.io/api/v1/calendar/earnings"
    params = {
        "from": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
        "to": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
        "token": FINNHUB_API_KEY
    }
    try:
        resp = requests.get(url, params=params, timeout=25, headers={"User-Agent":"MarketRotationScreener/1.0"})
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("earningsCalendar", []) if isinstance(payload, dict) else []
        out = {}
        for r in rows:
            t = str(r.get("symbol","")).strip().upper().replace(".","-")
            d = r.get("date")
            if not t or not d:
                continue
            # Finnhub may not always provide BMO/AMC; hour is optional.
            hour = r.get("hour")
            report_time = None
            if hour:
                h = str(hour).lower()
                if h in ("bmo","before market open","premarket"):
                    report_time = "premarket"
                elif h in ("amc","after market close","afterhours"):
                    report_time = "afterhours"
                else:
                    report_time = str(hour)
            out[t] = {
                "date": pd.Timestamp(d).normalize(),
                "time": report_time,
                "source": "Finnhub earnings calendar",
                "eps_estimate": r.get("epsEstimate"),
                "eps_actual": r.get("epsActual"),
                "revenue_estimate": r.get("revenueEstimate"),
                "revenue_actual": r.get("revenueActual"),
            }
        _mark_source("finnhub", True)
        return out
    except Exception as e:
        _mark_source("finnhub", False, e)
        return {}

def uw_api_get(path, params=None):
    if not UW_API_TOKEN:
        return None
    url = "https://api.unusualwhales.com" + path
    headers = {
        "Authorization": f"Bearer {UW_API_TOKEN}",
        "Accept": "application/json",
        "User-Agent": "MarketRotationScreener/1.0"
    }
    try:
        resp = requests.get(url, params=params or {}, headers=headers, timeout=25)
        resp.raise_for_status()
        payload = resp.json()
        _mark_source("unusual_whales", True)
        return payload.get("data", payload)
    except Exception as e:
        _mark_source("unusual_whales", False, e)
        raise

def unusual_whales_day(date):
    """Return recent earnings for one date from official UW premarket/afterhours APIs."""
    if not UW_API_TOKEN:
        return {}
    ds = pd.Timestamp(date).strftime("%Y-%m-%d")
    out = {}
    for endpoint, fallback_time in [
        ("/api/earnings/premarket", "premarket"),
        ("/api/earnings/afterhours", "afterhours"),
    ]:
        try:
            rows = uw_api_get(endpoint, {"date": ds, "limit": 100, "page": 0}) or []
            # paginate once more only if full page returned
            page = 1
            while rows and len(rows) >= 100 and page < 5:
                more = uw_api_get(endpoint, {"date": ds, "limit": 100, "page": page}) or []
                if not more:
                    break
                rows.extend(more)
                if len(more) < 100:
                    break
                page += 1
            for r in rows:
                t = str(r.get("symbol","")).strip().upper().replace(".","-")
                if not t:
                    continue
                report_date = r.get("report_date") or ds
                report_time = r.get("report_time") or fallback_time
                out[t] = {
                    "date": pd.Timestamp(report_date).normalize(),
                    "time": report_time,
                    "source": "Unusual Whales API",
                    "reaction": r.get("reaction"),
                    "expected_move_perc": r.get("expected_move_perc"),
                }
        except Exception:
            continue
    return out

def unusual_whales_history(ticker):
    """Official UW historical ticker earnings endpoint."""
    if not UW_API_TOKEN:
        return []
    try:
        rows = uw_api_get(f"/api/earnings/{ticker}") or []
        out = []
        for r in rows:
            d = r.get("report_date")
            if not d:
                continue
            out.append({
                "date": pd.Timestamp(d).normalize(),
                "time": r.get("report_time"),
                "source": "Unusual Whales API",
                "post_1d": r.get("post_earnings_move_1d"),
                "post_3d": r.get("post_earnings_move_3d"),
                "post_1w": r.get("post_earnings_move_1w"),
                "post_2w": r.get("post_earnings_move_2w"),
                "expected_move_perc": r.get("expected_move_perc"),
            })
        return sorted(out, key=lambda x:x["date"], reverse=True)
    except Exception:
        return []


def nasdaq_calendar_for_day(day):
    """
    Public Nasdaq earnings calendar fallback for one date.
    Uses Nasdaq's market-activity calendar response and returns ticker metadata.
    """
    ds = pd.Timestamp(day).strftime("%Y-%m-%d")
    url = "https://api.nasdaq.com/api/calendar/earnings"
    params = {"date": ds}
    headers = {
        "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept":"application/json, text/plain, */*",
        "Accept-Language":"en-US,en;q=0.9",
        "Origin":"https://www.nasdaq.com",
        "Referer":"https://www.nasdaq.com/",
    }
    try:
        resp = requests.get(url, params=params, timeout=20, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        rows = (((payload or {}).get("data") or {}).get("rows") or [])
        out = {}
        for r in rows:
            t = str(r.get("symbol","")).strip().upper().replace(".","-")
            if not t:
                continue
            raw_time = str(r.get("time","") or "").lower()
            report_time = None
            if "pre" in raw_time or "before" in raw_time:
                report_time = "premarket"
            elif "after" in raw_time:
                report_time = "afterhours"
            out[t] = {
                "date": pd.Timestamp(ds).normalize(),
                "time": report_time,
                "source": "Nasdaq earnings calendar",
            }
        _mark_source("nasdaq_yahoo_calendar", True)
        return out
    except Exception as e:
        _mark_source("nasdaq_yahoo_calendar", False, e)
        return {}

def yahoo_calendar_for_day(day):
    """
    Public Yahoo Finance earnings-calendar fallback.
    Returns {TICKER: date}. This is used only for recent-event discovery;
    historical profiling still uses per-ticker earnings history.
    """
    ds = pd.Timestamp(day).strftime("%Y-%m-%d")
    url = f"https://finance.yahoo.com/calendar/earnings?day={ds}&offset=0&size=100"
    headers = {
        "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
    }
    try:
        resp = requests.get(url, timeout=20, headers=headers)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        out = {}
        for df in tables:
            # Yahoo table generally contains Symbol / Company / Earnings Call Time.
            symbol_col = next((c for c in df.columns if str(c).strip().lower() == "symbol"), None)
            if symbol_col is None:
                continue
            for sym in df[symbol_col].astype(str):
                t = sym.strip().upper().replace(".","-")
                if t and t not in ("NAN","SYMBOL"):
                    out[t] = pd.Timestamp(ds)
        _mark_source("nasdaq_yahoo_calendar", True)
        return out
    except Exception as e:
        _mark_source("nasdaq_yahoo_calendar", False, e)
        return {}

def discover_recent_earnings(tickers, recent_trading_days=10):
    """
    Lightweight recent-earnings discovery for the main screen.

    Main scan intentionally avoids per-ticker historical earnings requests.
    It intersects ALL ETF holdings with calendar-level sources only:
      1) Finnhub date-range calendar
      2) Optional Unusual Whales API
      3) Nasdaq daily calendar
      4) Yahoo daily calendar

    Historical earnings profiles are loaded separately, on demand.
    """
    now = pd.Timestamp.now().normalize()
    calendar_days = int(recent_trading_days * 1.8) + 5
    start = now - pd.Timedelta(days=calendar_days)
    wanted = set(tickers)
    found = {}
    diag = {
        "universe": len(tickers),
        "finnhub": 0,
        "nasdaq": 0,
        "uw": 0,
        "yahoo": 0,
        "calendar_days_checked": 0,
    }

    # 1) Finnhub: one structured range request.
    if FINNHUB_API_KEY:
        fh_key = f"finnhub-calendar:{start.date()}:{now.date()}"
        fh_map = cached(fh_key, lambda: finnhub_earnings_calendar(start, now), ttl=1800)
        for t, meta in fh_map.items():
            if t in wanted:
                found[t] = meta
                diag["finnhub"] += 1

    # Build weekday list once.
    days = [d for d in pd.date_range(start, now, freq="D") if d.weekday() < 5]
    diag["calendar_days_checked"] = len(days)

    # 2) Optional Unusual Whales paid API.
    if UW_API_TOKEN:
        def uw_day_cached(d):
            k = f"uw-calendar:{pd.Timestamp(d).date()}"
            return cached(k, lambda: unusual_whales_day(d), ttl=1800)

        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(uw_day_cached, d): d for d in days}
            for fut in as_completed(futures):
                try:
                    day_map = fut.result()
                except Exception:
                    day_map = {}
                for t, meta in day_map.items():
                    if t in wanted and t not in found:
                        found[t] = meta
                        diag["uw"] += 1

    # 3 + 4) Nasdaq and Yahoo public calendars, parallelized and cached.
    def public_day(d):
        ds = pd.Timestamp(d).strftime("%Y-%m-%d")
        nkey = f"nasdaq-calendar:{ds}"
        ykey = f"yahoo-calendar:{ds}"
        nmap = cached(nkey, lambda: nasdaq_calendar_for_day(d), ttl=1800)
        ymap = cached(ykey, lambda: yahoo_calendar_for_day(d), ttl=1800)
        return nmap, ymap

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(public_day, d): d for d in days}
        for fut in as_completed(futures):
            try:
                nmap, ymap = fut.result()
            except Exception:
                nmap, ymap = {}, {}

            for t, meta in nmap.items():
                if t in wanted and t not in found:
                    found[t] = meta
                    diag["nasdaq"] += 1

            for t, ed in ymap.items():
                if t in wanted and t not in found:
                    found[t] = {
                        "date": ed,
                        "time": None,
                        "source": "Yahoo earnings calendar"
                    }
                    diag["yahoo"] += 1

    diag["found"] = len(found)
    return found, diag


def nasdaq_earnings_history_dates(ticker):
    """
    One-request fallback for historical reported earnings dates from Nasdaq.
    Returns a list of normalized timestamps. If Nasdaq changes the endpoint,
    this safely returns an empty list and yfinance remains available.
    """
    ticker = ticker.upper().strip()
    url = f"https://api.nasdaq.com/api/company/{ticker}/earnings-surprise"
    headers = {
        "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept":"application/json, text/plain, */*",
        "Accept-Language":"en-US,en;q=0.9",
        "Origin":"https://www.nasdaq.com",
        "Referer":f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}/earnings",
    }
    try:
        resp = requests.get(url, timeout=20, headers=headers)
        resp.raise_for_status()
        payload = resp.json() or {}
        data = payload.get("data") or {}
        rows = data.get("earningsSurpriseTable") or data.get("rows") or []
        if isinstance(rows, dict):
            rows = rows.get("rows") or []
        dates = []
        for r in rows:
            raw = (
                r.get("dateReported")
                or r.get("date")
                or r.get("reportedDate")
                or r.get("reportDate")
            )
            if not raw:
                continue
            try:
                d = pd.to_datetime(raw, errors="coerce")
                if pd.notna(d):
                    dates.append(pd.Timestamp(d).normalize())
            except Exception:
                pass
        return sorted(list(dict.fromkeys(dates)), reverse=True)
    except Exception:
        return []

def merged_historical_earnings_dates(ticker, current_event_date=None):
    """
    Merge multiple date sources for one ticker. No approximate/fabricated
    earnings dates are created.
    """
    dates = []
    # yfinance first
    try:
        dates.extend(get_earnings_dates(ticker, 24))
    except Exception:
        pass

    # Nasdaq one-request history fallback
    try:
        dates.extend(nasdaq_earnings_history_dates(ticker))
    except Exception:
        pass

    # Optional UW history if user ever configures a token
    if UW_API_TOKEN:
        try:
            dates.extend([x["date"] for x in unusual_whales_history(ticker)])
        except Exception:
            pass

    if current_event_date:
        try:
            dates.append(pd.Timestamp(current_event_date).normalize())
        except Exception:
            pass

    cleaned = []
    for d in dates:
        try:
            td = pd.Timestamp(d)
            if getattr(td, "tzinfo", None) is not None:
                try:
                    td = td.tz_convert(None)
                except Exception:
                    td = td.tz_localize(None)
            cleaned.append(td.normalize())
        except Exception:
            pass

    return sorted(list(dict.fromkeys(cleaned)), reverse=True)

def get_earnings_dates(ticker, limit=12):
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

def abs_excursion(df, start_pos, days):
    # Require the full post-event trading-session window.
    if start_pos is None or start_pos <= 0 or start_pos >= len(df):
        return None
    if start_pos + days > len(df):
        return None
    base = float(df["Close"].iloc[start_pos-1])
    seg = df.iloc[start_pos:start_pos+days]
    if len(seg) < days:
        return None
    hi = float(seg["High"].max())
    lo = float(seg["Low"].min())
    up = (hi/base-1)*100
    down = (lo/base-1)*100
    return float(max(abs(up),abs(down)))

def close_return(df, start_pos, days):
    if start_pos is None or start_pos <= 0 or start_pos+days-1 >= len(df):
        return None
    base=float(df["Close"].iloc[start_pos-1])
    last=float(df["Close"].iloc[start_pos+days-1])
    return float((last/base-1)*100)

def earnings_profile(ticker, dates):
    df = dl_ohlc(ticker, "4y")
    if len(df)<80 or not dates:
        return None
    events=[]
    for d in sorted(dates):
        pos=event_session_index(df,d)
        if pos is None or pos<=0: continue
        row={"date":pd.Timestamp(d).strftime("%Y-%m-%d")}
        for n in (1,3,5,10,14):
            row[f"close{n}"]=close_return(df,pos,n)
            row[f"exc{n}"]=abs_excursion(df,pos,n)
        if row["exc1"] is not None:
            events.append(row)
    # Stats use only fully completed 14D events. The detail table can still show
    # recent incomplete events with unfinished horizons as —.
    completed=[e for e in events if e["exc14"] is not None]
    hist=completed[-8:]
    display_events=events[-8:]
    if len(hist)<3:
        return None
    def med(key):
        vals=[abs(e[key]) if key.startswith("close") and e[key] is not None else e[key] for e in hist if e[key] is not None]
        return float(np.median(vals)) if vals else None
    ex10=[e["exc10"] for e in hist if e["exc10"] is not None]
    ex14=[e["exc14"] for e in hist if e["exc14"] is not None]
    pct5 = 100*sum(v>=5 for v in ex10)/len(ex10) if ex10 else None
    pct10 = 100*sum(v>=10 for v in ex14)/len(ex14) if ex14 else None

    # continuation tendency: compare 10d excursion to 1d excursion
    # (magnitude only — doesn't distinguish real drift from round-trip volatility)
    ratios=[]
    for e in hist:
        if e["exc1"] and e["exc10"] is not None and e["exc1"]>0:
            ratios.append(e["exc10"]/e["exc1"])
    cont_ratio=float(np.median(ratios)) if ratios else 1.0

    # Directional persistence: did the day-1 close-return direction still hold
    # (and retain real magnitude) by day 10/14, or did the stock round-trip?
    # This is the actual PEAD proxy — abs_excursion() alone can't tell a stock
    # that kept grinding in one direction apart from one that gapped and faded.
    persisted=0; reverted=0; persist_n=0
    for e in hist:
        c1=e.get("close1")
        c_end=e.get("close10") if e.get("close10") is not None else e.get("close14")
        if c1 is None or c_end is None or c1==0: continue
        persist_n+=1
        same_dir=(c_end*c1)>0
        retained=abs(c_end)/abs(c1)
        if same_dir and retained>=0.5:
            persisted+=1
        elif (not same_dir) or retained<=0.2:
            reverted+=1
    pct_persist=persisted/persist_n if persist_n else None
    pct_revert=reverted/persist_n if persist_n else None

    # Simple mover score driven by typical excursion + frequency.
    m1=med("exc1") or 0
    m10=med("exc10") or 0
    score=min(10.0, 0.45*min(10,m1) + 0.35*min(10,m10/1.5) + 0.20*((pct5 or 0)/10))
    if score>=7.0: label="HIGH"
    elif score>=4.5: label="MODERATE"
    else: label="LOW"
    if pct_revert is not None and pct_revert>=0.45:
        behavior="REVERSION"
    elif pct_persist is not None and pct_persist>=0.60:
        behavior="CONTINUATION"
    elif cont_ratio<1.15:
        behavior="FAST REACTION"
    else:
        behavior="MIXED"
    return {
        "label":label,"score":round(score,1),"n":len(hist),
        "median_exc1":round(m1,2),"median_exc3":round(med("exc3") or 0,2),
        "median_exc5":round(med("exc5") or 0,2),"median_exc10":round(m10,2),
        "median_exc14":round(med("exc14") or 0,2),"has_exc14_data":bool(ex14),
        "pct_gt5_10d":round(pct5 or 0,1),"pct_gt10_14d":round(pct10 or 0,1),
        "pct_directional_persist":round((pct_persist or 0)*100,1),
        "pct_directional_revert":round((pct_revert or 0)*100,1),
        "behavior":behavior,"events":list(reversed(display_events))
    }

def alpaca_headers():
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        raise RuntimeError("Alpaca is not configured. Add APCA_API_KEY_ID and APCA_API_SECRET_KEY to Render.")
    return {"APCA-API-KEY-ID":ALPACA_API_KEY,"APCA-API-SECRET-KEY":ALPACA_API_SECRET,"Accept":"application/json"}

def _safe_float(v):
    try:
        if v is None or v=="": return None
        return float(v)
    except Exception:
        return None

def realized_vol_20d(ticker):
    df=dl_ohlc(ticker,"3mo")
    c=df["Close"].dropna()
    if not len(c): return None,None
    spot=float(c.iloc[-1])
    if len(c)<22: return None,spot
    ret=np.log(c/c.shift(1)).dropna().tail(20)
    return float(ret.std(ddof=1)*np.sqrt(252)),spot

def alpaca_option_contracts(ticker,start_date,end_date):
    url=f"{ALPACA_TRADING_BASE_URL}/v2/options/contracts"
    params={"underlying_symbols":ticker,"expiration_date_gte":start_date,"expiration_date_lte":end_date,"status":"active","limit":1000}
    rows=[]; token=None
    for _ in range(4):
        if token: params["page_token"]=token
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=25)
        if r.status_code in (401,403):
            raise RuntimeError("Alpaca contract access was rejected. Check API credentials/account permissions.")
        r.raise_for_status()
        j=r.json() or {}
        rows.extend(j.get("option_contracts") or [])
        token=j.get("next_page_token") or j.get("page_token")
        if not token: break
    return rows

def alpaca_option_chain(ticker,start_date,end_date,spot):
    url=f"{ALPACA_DATA_BASE_URL}/v1beta1/options/snapshots/{ticker}"
    params={
        "feed":ALPACA_OPTIONS_FEED,"expiration_date_gte":start_date,"expiration_date_lte":end_date,
        "strike_price_gte":round(max(.01,spot*.75),2),"strike_price_lte":round(spot*1.25,2),"limit":1000
    }
    out={}; token=None
    try:
        for _ in range(4):
            if token: params["page_token"]=token
            r=requests.get(url,params=params,headers=alpaca_headers(),timeout=25)
            if r.status_code in (401,403):
                _mark_source("alpaca_options", False, f"{r.status_code} rejected")
                raise RuntimeError(f"Alpaca {ALPACA_OPTIONS_FEED} option-chain access was rejected. Check API credentials/feed permissions.")
            if r.status_code==429:
                _mark_source("alpaca_options", False, "rate limited")
                raise RuntimeError("Alpaca rate limit reached. Try again shortly.")
            r.raise_for_status(); j=r.json() or {}; part=j.get("snapshots") or {}
            if isinstance(part,dict): out.update(part)
            token=j.get("next_page_token")
            if not token: break
    except requests.RequestException as e:
        _mark_source("alpaca_options", False, e)
        raise
    _mark_source("alpaca_options", True)
    return out

def alpaca_option_chain_broad(ticker,start_date,end_date,spot):
    """Broader snapshot universe for institutional-flow discovery.

    Deliberately separate from the 0-30 DTE trade-selection chain: flow may live
    in LEAPS or farther-from-spot strikes. Pagination is bounded for Render/API safety.
    """
    url=f"{ALPACA_DATA_BASE_URL}/v1beta1/options/snapshots/{ticker}"
    params={
        "feed":ALPACA_OPTIONS_FEED,"expiration_date_gte":start_date,"expiration_date_lte":end_date,
        "strike_price_gte":round(max(.01,spot*.45),2),"strike_price_lte":round(spot*1.65,2),"limit":1000
    }
    out={}; token=None
    for _ in range(10):
        if token: params["page_token"]=token
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=30)
        if r.status_code in (401,403): raise RuntimeError(f"Alpaca {ALPACA_OPTIONS_FEED} broad-chain access was rejected.")
        if r.status_code==429: raise RuntimeError("Alpaca rate limit reached while building broad flow universe.")
        r.raise_for_status(); j=r.json() or {}; part=j.get("snapshots") or {}
        if isinstance(part,dict): out.update(part)
        token=j.get("next_page_token")
        if not token: break
    return out

def broad_flow_universe(ticker,spot):
    today=pd.Timestamp.now().normalize(); start=today.strftime("%Y-%m-%d")
    end=(today+pd.Timedelta(days=900)).strftime("%Y-%m-%d")
    contracts=alpaca_option_contracts(ticker,start,end); meta={x.get("symbol"):x for x in contracts if x.get("symbol")}
    snaps=alpaca_option_chain_broad(ticker,start,end,spot)
    rows=[]
    for sym,snap in snaps.items():
        if sym not in meta: continue
        r=option_contract_row(sym,snap,meta[sym],spot)
        if r.get("expiration") and r.get("moneyness_pct") is not None: rows.append(r)
    return rows

def option_contract_row(symbol,snap,meta,spot):
    q=(snap or {}).get("latestQuote") or (snap or {}).get("latest_quote") or {}
    t=(snap or {}).get("latestTrade") or (snap or {}).get("latest_trade") or {}
    dbar=(snap or {}).get("dailyBar") or (snap or {}).get("daily_bar") or {}
    g=(snap or {}).get("greeks") or {}
    bid=_safe_float(q.get("bp",q.get("bid_price"))); ask=_safe_float(q.get("ap",q.get("ask_price")))
    last=_safe_float(t.get("p",t.get("price"))); last_size=_safe_float(t.get("s",t.get("size"))) or 0; vol=_safe_float(dbar.get("v",dbar.get("volume"))) or 0
    iv=_safe_float((snap or {}).get("impliedVolatility",(snap or {}).get("implied_volatility")))
    oi=_safe_float((meta or {}).get("open_interest")) or 0
    strike=_safe_float((meta or {}).get("strike_price"))
    mid=spread=None
    if bid is not None and ask is not None and bid>=0 and ask>=bid and bid+ask>0:
        mid=(bid+ask)/2
        if mid>0: spread=(ask-bid)/mid*100
    moneyness=(strike/spot-1)*100 if strike and spot else None
    dte=None
    exp_str=(meta or {}).get("expiration_date")
    if exp_str:
        try:
            dte=(datetime.strptime(str(exp_str)[:10],"%Y-%m-%d").date()-datetime.now().date()).days
        except Exception:
            dte=None
    if oi>=500 and vol>=100 and spread is not None and spread<=10:
        liq="Liquid"; execution_label="Liquid"
    elif oi>=100 and vol>=25 and spread is not None and spread<=15:
        liq="Tradable"; execution_label="Tradable"
    else:
        liq="Thin"
        if spread is None:
            execution_label="No Quote"
        elif oi>=500 or vol>=100:
            execution_label="Active · Wide Spread"
        elif oi>=100 or vol>=25:
            execution_label="Low Activity · Wide Spread" if spread>15 else "Low Activity"
        else:
            execution_label="Low Activity"
    return {
        "symbol":symbol,"type":(meta or {}).get("type"),"expiration":(meta or {}).get("expiration_date"),"dte":dte,
        "strike":strike,"bid":bid,"ask":ask,"mid":mid,"last":last,"last_size":int(last_size),"trade_ts":t.get("t",t.get("timestamp")),"quote_ts":q.get("t",q.get("timestamp")),"volume":int(vol),"open_interest":int(oi),
        "iv":(iv*100 if iv is not None and iv<=5 else iv),"delta":_safe_float(g.get("delta")),
        "gamma":_safe_float(g.get("gamma")),"theta":_safe_float(g.get("theta")),"vega":_safe_float(g.get("vega")),
        "spread_pct":spread,"moneyness_pct":moneyness,"liquidity":liq,"execution_label":execution_label
    }


def alpaca_option_daily_bars(symbols, lookback_days=55):
    """Historical OPRA/indicative daily option bars for a small candidate set."""
    symbols=[str(x).strip() for x in (symbols or []) if str(x).strip()][:20]
    if not symbols:return {}
    # Use an actual UTC timestamp as the end bound. The previous code
    # normalized server time and then added a day; after UTC midnight this
    # could send Alpaca an end date nearly two calendar days in the future,
    # which the historical options endpoint rejects with HTTP 400.
    end=pd.Timestamp.now(tz="UTC")-pd.Timedelta(minutes=1)
    start=end-pd.Timedelta(days=max(25,int(lookback_days or 55)))
    params={"symbols":",".join(symbols),"timeframe":"1Day","start":start.isoformat().replace("+00:00","Z"),"end":end.isoformat().replace("+00:00","Z"),"limit":10000,"sort":"asc"}
    out={sym:[] for sym in symbols}; token=None
    for _ in range(4):
        if token:params["page_token"]=token
        elif "page_token" in params:params.pop("page_token",None)
        r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)
        if r.status_code==429:
            time.sleep(.75);r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/bars",params=params,headers=alpaca_headers(),timeout=30)
        if not r.ok:
            # Preserve Alpaca's response body so a bad parameter/entitlement
            # is diagnosable from the Top Setups card instead of only showing
            # the generic requests 400/403 message.
            detail=(r.text or "").strip()[:500]
            raise RuntimeError(f"Alpaca option history HTTP {r.status_code}: {detail or r.reason}")
        j=r.json() or {};bars=j.get("bars") or {}
        if isinstance(bars,dict):
            for sym,arr in bars.items():out.setdefault(sym,[]).extend(arr or [])
        token=j.get("next_page_token")
        if not token:break
    return out

def _premium_support_metrics(bars,current_mid=None):
    """Score premium support as a decaying option-specific zone, not stock support."""
    clean=[]
    for b in bars or []:
        o=_safe_float(b.get("o",b.get("open")));h=_safe_float(b.get("h",b.get("high")));l=_safe_float(b.get("l",b.get("low")));c=_safe_float(b.get("c",b.get("close")))
        if None in (h,l,c) or h<=0 or l<=0 or c<=0:continue
        clean.append({"o":o if o and o>0 else c,"h":h,"l":l,"c":c,"v":_safe_float(b.get("v",b.get("volume"))) or 0,"t":b.get("t",b.get("timestamp"))})
    clean=clean[-30:]
    if len(clean)<5:return {"available":False,"reason":"Need at least 5 daily premium bars."}
    lows=np.array([x["l"] for x in clean],dtype=float);highs=np.array([x["h"] for x in clean],dtype=float);closes=np.array([x["c"] for x in clean],dtype=float)
    floor=float(np.min(lows));q25=float(np.percentile(lows,25));support_hi=min(max(floor*1.12,q25),floor*1.35)
    px=_safe_float(current_mid) or float(closes[-1]);distance=(px/floor-1)*100 if floor>0 else None
    touches=int(np.sum(lows<=support_hi));prior_high=float(np.max(highs));expansion=(prior_high/px) if px>0 else None
    ranges=np.array([(x["h"]-x["l"])/max(x["c"],.01) for x in clean],dtype=float)
    recent=float(np.mean(ranges[-5:])) if len(ranges)>=5 else None;prior=float(np.mean(ranges[-15:-5])) if len(ranges)>=10 else None;compression=(recent/prior) if prior and prior>0 else None
    reversal=bool(len(closes)>=3 and closes[-1]>closes[-2] and closes[-2]>=closes[-3]*.92 and px>floor*1.05)
    score=0.0
    if distance is not None:score+=35 if distance<=10 else 30 if distance<=20 else 18 if distance<=35 else 8 if distance<=50 else 0
    score+=min(20.0,touches*4.0)
    if expansion is not None:score+=20 if expansion>=3 else 15 if expansion>=2 else 8 if expansion>=1.5 else 0
    if compression is not None:score+=15 if compression<=.75 else 10 if compression<=1.0 else 3 if compression<=1.2 else 0
    if reversal:score+=10
    score=max(0.0,min(100.0,score))
    if distance is not None and distance<=20 and touches>=2:state="REVERSAL CONFIRMED" if reversal else "AT SUPPORT"
    elif distance is not None and distance<=35 and touches>=2:state="NEAR SUPPORT"
    elif distance is not None and distance<=20:state="CHEAP / UNPROVEN"
    else:state="AWAY FROM SUPPORT"
    floor_reliable=touches>=2
    return {"available":True,"score":round(score,1),"state":state,"current_premium":round(px,4),"support_low":round(floor,4),"support_high":round(support_hi,4),"distance_from_support_pct":round(distance,1) if distance is not None else None,"support_touches":touches,"floor_reliable":floor_reliable,"prior_20d_high":round(prior_high,4),"prior_expansion_multiple":round(expansion,2) if expansion is not None else None,"range_compression_ratio":round(compression,2) if compression is not None else None,"reversal_confirmed":reversal,"bars_used":len(clean),"last_bar_date":str(clean[-1].get("t") or "")[:10] or None}


def premium_support_payload(ticker,direction="bullish",options_payload=None):
    ticker=ticker.upper().strip();direction=str(direction or "bullish").lower();want_put=direction.startswith("bear")
    # Premium Support deliberately builds its own 7-90 DTE chain rather than
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
    for r in premium_rows:
        typ=str(r.get("type") or "").lower();is_put=typ.startswith("p")
        if is_put!=want_put:continue
        strike=_safe_float(r.get("strike"));mid=_safe_float(r.get("mid"));bid=_safe_float(r.get("bid"));ask=_safe_float(r.get("ask"));spread=_safe_float(r.get("spread_pct"));delta=abs(_safe_float(r.get("delta")) or 0);oi=int(_safe_float(r.get("open_interest")) or 0);vol=int(_safe_float(r.get("volume")) or 0);dte=r.get("dte")
        if not strike or not mid or mid<=0 or not bid or bid<=0 or not ask or ask<=bid:continue
        if dte is None or dte<7 or dte>90:continue
        otm=((spot-strike)/spot*100) if want_put else ((strike-spot)/spot*100)
        if otm<=0 or otm>10:continue
        if spread is None or spread>25 or oi<50:continue
        if delta and not (.15<=delta<=.55):continue
        exec_score=(20 if spread<=8 else 16 if spread<=12 else 11)+(8 if oi>=500 else 5 if oi>=200 else 2)+(6 if vol>=100 else 3 if vol>=25 else 1)
        shape_score=(10 if 2<=otm<=7 else 6)+(8 if .22<=delta<=.45 else 4)+(6 if mid<=3 else 4 if mid<=5 else 1)
        candidates.append((exec_score+shape_score,dict(r,otm_pct=round(otm,2))))
    candidates.sort(key=lambda z:z[0],reverse=True)
    # Preserve representation across expiration horizons instead of letting the
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
        if r.get("symbol") and r["symbol"] not in seen:
            selected.append(r);seen.add(r["symbol"])
    if not selected:return {"ticker":ticker,"direction":direction,"available":False,"reason":"No liquid OTM candidate passed the premium-history prefilter."}
    histories=alpaca_option_daily_bars([r["symbol"] for r in selected],100);scored=[];rank_by_symbol={r["symbol"]:rank for rank,r in candidates}
    for r in selected:
        m=_premium_support_metrics(histories.get(r["symbol"]) or [],r.get("mid"))
        if not m.get("available"):continue
        execution_component=min(100.0,rank_by_symbol.get(r["symbol"],0)*2.0);combined=.78*float(m.get("score") or 0)+.22*execution_component
        rr=dict(r);rr.update(m);rr["premium_support_score"]=round(combined,1);scored.append(rr)
    if not scored:return {"ticker":ticker,"direction":direction,"available":False,"reason":"Historical premium bars were unavailable for the candidate contracts."}
    scored.sort(key=lambda r:(-r["premium_support_score"],r.get("distance_from_support_pct") if r.get("distance_from_support_pct") is not None else 999,r.get("mid") or 999))
    return {"ticker":ticker,"direction":direction,"available":True,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}","best_contract":scored[0],"candidates":scored[:8],"contracts_considered":len(premium_rows),"contracts_screened":len(selected),"dte_universe":"7-90","expiration_buckets":["7-35","36-60","61-90"],"history_lookback_days":100,"premium_bars_window":30,"note":"Premium support is contract-specific and decays with time/IV; it is a confirmation layer, not a static stock-like floor."}


def modeled_dealer_positioning(rows, spot):
    """Heuristic dealer-positioning map from current chain gamma + OI.

    Calls are assigned +gamma and puts -gamma. This is a transparent screening
    convention, not knowledge of dealers' actual inventory. Exposures are
    reported as dollar-gamma per ~1% underlying move.
    """
    by_strike={}
    usable=0
    for r in rows:
        gamma=_safe_float(r.get("gamma")); oi=_safe_float(r.get("open_interest")); strike=_safe_float(r.get("strike"))
        if gamma is None or oi is None or oi<=0 or strike is None: continue
        typ=str(r.get("type") or "").lower()
        sign=1.0 if typ.startswith("c") else (-1.0 if typ.startswith("p") else 0.0)
        if not sign: continue
        # gamma * contracts * multiplier * S^2 * 1% move
        gex=sign*gamma*oi*100.0*(spot**2)*0.01
        b=by_strike.setdefault(float(strike),{"call_gex":0.0,"put_gex":0.0,"net_gex":0.0,"call_oi":0,"put_oi":0})
        if sign>0:
            b["call_gex"]+=abs(gex); b["call_oi"]+=int(oi)
        else:
            b["put_gex"]-=abs(gex); b["put_oi"]+=int(oi)
        b["net_gex"]+=gex; usable+=1
    if not by_strike:
        return {"available":False,"reason":"No gamma/open-interest rows available."}
    levels=[]
    for k in sorted(by_strike):
        b=by_strike[k]
        levels.append({"strike":k,"call_gex":b["call_gex"],"put_gex":b["put_gex"],"net_gex":b["net_gex"],"call_oi":b["call_oi"],"put_oi":b["put_oi"]})
    total_call=sum(x["call_gex"] for x in levels)
    total_put=sum(x["put_gex"] for x in levels)
    total=sum(x["net_gex"] for x in levels)
    call_wall=max(levels,key=lambda x:x["call_gex"])["strike"]
    put_wall=min(levels,key=lambda x:x["put_gex"])["strike"]
    # Model a zero-gamma level by re-pricing Black-Scholes gamma across
    # hypothetical spot prices, using each contract's current IV and time to expiry.
    # Calls are + and puts - under the same transparent inventory convention.
    def bs_gamma(S,K,sigma,T,r=.04):
        if not S or not K or not sigma or sigma<=0 or T<=0: return 0.0
        try:
            d1=(math.log(S/K)+(r+.5*sigma*sigma)*T)/(sigma*math.sqrt(T))
            phi=math.exp(-.5*d1*d1)/math.sqrt(2*math.pi)
            return phi/(S*sigma*math.sqrt(T))
        except Exception:
            return 0.0
    today=datetime.now().date(); model_rows=[]
    for r in rows:
        oi=_safe_float(r.get("open_interest")); K=_safe_float(r.get("strike")); iv=_safe_float(r.get("iv")); exp=r.get("expiration")
        typ=str(r.get("type") or "").lower(); sign=1.0 if typ.startswith("c") else (-1.0 if typ.startswith("p") else 0.0)
        if not oi or oi<=0 or not K or not iv or not exp or not sign: continue
        sigma=iv/100.0 if iv>5 else iv
        try: d=datetime.strptime(str(exp)[:10],"%Y-%m-%d").date(); T=max(1/365.0,(d-today).days/365.0)
        except Exception: continue
        model_rows.append((K,sigma,T,oi,sign))
    flip=None
    if model_rows:
        grid=np.linspace(max(.01,spot*.70),spot*1.30,121)
        vals=[]
        for S in grid:
            net=0.0
            for K,sigma,T,oi,sign in model_rows:
                gam=bs_gamma(float(S),K,sigma,T)
                net += sign*gam*oi*100.0*(float(S)**2)*0.01
            vals.append(net)
        for i in range(1,len(grid)):
            a,b=vals[i-1],vals[i]
            if a==0:
                flip=float(grid[i-1]); break
            if (a<0<b) or (a>0>b):
                x0,x1=float(grid[i-1]),float(grid[i])
                flip=x0 + (0-a)*(x1-x0)/(b-a) if b!=a else x0
                break
    # focus display on strikes reasonably close to spot
    near_all=[x for x in levels if abs(x["strike"]/spot-1)<=0.18]
    near=sorted(near_all,key=lambda x:abs(x["net_gex"]),reverse=True)[:12]
    landscape_levels=sorted(near_all,key=lambda x:x["strike"])
    return {
        "available":True,"method":"call + / put - gamma × OI heuristic","contracts_used":usable,
        "total_call_gex":round(total_call,2),"total_put_gex":round(total_put,2),
        "net_gex":round(total,2),"net_gex_millions":round(total/1e6,2),
        "gamma_regime":"Positive / dampening" if total>=0 else "Negative / amplifying",
        "call_wall":call_wall,"put_wall":put_wall,"modeled_flip":round(flip,2) if flip is not None else None,
        "levels":near,"landscape_levels":landscape_levels,
        "warning":"Modeled from chain gamma/OI using a call-positive / put-negative inventory convention. The flip re-prices Black-Scholes gamma across hypothetical spot levels; it is not actual dealer inventory or a vendor-equivalent level."
    }

def _option_trade_chunks(symbols, start_iso, end_iso, feed):
    """Fetch today's option trades across a high-coverage contract universe.

    V21.2 prioritizes coverage because flow is being used as a decision-support layer.
    Contract chunks are fetched concurrently, while each chunk is paginated deeply
    enough that very-active names are less likely to crowd out other contracts.
    """
    chunks=[symbols[i:i+20] for i in range(0,len(symbols),20)]

    def fetch_chunk(chunk):
        local=[]
        params={"symbols":",".join(chunk),"start":start_iso,"end":end_iso,"limit":10000,"sort":"asc"}
        token=None
        for _ in range(5):
            if token: params["page_token"]=token
            elif "page_token" in params: params.pop("page_token",None)
            r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/trades",params=params,headers=alpaca_headers(),timeout=35)
            if r.status_code in (401,403):
                try:
                    detail=(r.json() or {}).get("message") or r.text
                except Exception:
                    detail=r.text
                if r.status_code==401:
                    raise RuntimeError(f"Alpaca historical option-trade authentication failed: {detail or 'invalid API credentials'}")
                raise RuntimeError(
                    "Alpaca historical option-trade endpoint returned 403 Forbidden. "
                    "Your option-chain/GEX access may still work, but this account/key is not currently entitled to the historical options trades endpoint. "
                    f"Alpaca response: {detail or 'forbidden'}"
                )
            if r.status_code==429:
                time.sleep(1.0)
                r=requests.get(f"{ALPACA_DATA_BASE_URL}/v1beta1/options/trades",params=params,headers=alpaca_headers(),timeout=35)
            if r.status_code==429: raise RuntimeError("Alpaca rate limit reached while loading high-coverage flow.")
            if r.status_code>=400:
                try:
                    detail=(r.json() or {}).get("message") or r.text
                except Exception:
                    detail=r.text
                raise RuntimeError(f"Alpaca historical options trades returned {r.status_code}: {detail}")
            j=r.json() or {}
            trades=j.get("trades") or {}
            if isinstance(trades,dict):
                for sym,arr in trades.items():
                    for t in (arr or []): local.append((sym,t))
            token=j.get("next_page_token")
            if not token: break
        return local

    out=[]
    with ThreadPoolExecutor(max_workers=min(FLOW_TRADE_WORKERS,max(1,len(chunks)))) as ex:
        futs=[ex.submit(fetch_chunk,c) for c in chunks]
        for f in as_completed(futs): out.extend(f.result())
    return out

def _parse_trade_ts(v):
    if not v: return None
    try:
        return pd.Timestamp(v).to_pydatetime()
    except Exception:
        return None


def _institutional_candidate_score(r):
    """Rank contracts for flow inspection without reusing the user's trade-selector rules."""
    vol=float(r.get("volume") or 0); oi=float(r.get("open_interest") or 0)
    px=float(r.get("last") or r.get("mid") or 0)
    last_size=float(r.get("last_size") or 0)
    gross_activity=max(0.0, vol*px*100.0)
    last_notional=max(0.0, last_size*px*100.0)
    ratio=(vol/oi) if oi>0 else (6.0 if vol>0 else 0.0)
    ratio=min(ratio,10.0)
    # Reward large dollar activity first, then abnormal turnover and block-like last prints.
    score=(math.log10(gross_activity+1)*2.2)+(min(vol,5000)/5000.0)*1.5+ratio*0.9+(math.log10(last_notional+1))*0.7
    return score




def _fetch_stock_bars_with_feed(ticker, feed, days=3):
    """Fetch Alpaca stock bars with an explicit feed override for comparison only."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    url = f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Min", "start": (now - timedelta(days=days)).isoformat(), "end": now.isoformat(),
        "adjustment": "raw", "feed": feed, "sort": "asc", "limit": 10000,
    }
    raw=[]; token=None
    for _ in range(3):
        if token: params["page_token"]=token
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=20)
        if r.status_code in (401,403):
            raise PermissionError(f"Account is not entitled to the '{feed}' stock feed yet.")
        r.raise_for_status()
        j=r.json() or {}
        raw.extend(j.get("bars") or [])
        token=j.get("next_page_token") or j.get("page_token")
        if not token: break
    return raw

def _fetch_option_snapshots_with_feed(ticker,start_date,end_date,spot,feed):
    """Fetch Alpaca option snapshots with an explicit feed override for comparison only."""
    url=f"{ALPACA_DATA_BASE_URL}/v1beta1/options/snapshots/{ticker}"
    params={
        "feed":feed,"expiration_date_gte":start_date,"expiration_date_lte":end_date,
        "strike_price_gte":round(max(.01,spot*.85),2),"strike_price_lte":round(spot*1.15,2),"limit":1000,
    }
    out={}; token=None
    for _ in range(3):
        if token: params["page_token"]=token
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=20)
        if r.status_code in (401,403):
            raise PermissionError(f"Account is not entitled to the '{feed}' options feed yet.")
        r.raise_for_status()
        j=r.json() or {}
        part=j.get("snapshots") or {}
        if isinstance(part,dict): out.update(part)
        token=j.get("next_page_token")
        if not token: break
    return out

def feed_comparison_payload(ticker):
    ticker=ticker.upper().strip()
    out={"ticker":ticker,"stocks":{},"options":{}}
    for feed in ("iex","sip"):
        try:
            bars=_fetch_stock_bars_with_feed(ticker,feed,days=3)
            if not bars:
                out["stocks"][feed]={"available":False,"reason":"No bars returned for this window."}; continue
            last_date=str(bars[-1].get("t",""))[:10]
            session_bars=[b for b in bars if str(b.get("t",""))[:10]==last_date]
            total_vol=sum(float(b.get("v") or 0) for b in session_bars)
            profile=_profile_from_intraday_bars(session_bars,last_date,rows_count=48,value_area_pct=68)
            out["stocks"][feed]={
                "available":True,"session_date":last_date,"total_volume":int(total_vol),
                "vah":round(profile["vah"],2) if profile else None,
                "poc":round(profile["poc"],2) if profile else None,
                "val":round(profile["val"],2) if profile else None,
            }
        except Exception as e:
            out["stocks"][feed]={"available":False,"reason":str(e)}
    spot=None
    try:
        px=dl_ohlc(ticker,"5d")
        if px is not None and len(px): spot=float(px["Close"].dropna().iloc[-1])
    except Exception: pass
    if spot:
        today=pd.Timestamp.now().normalize(); start=today.strftime("%Y-%m-%d"); end=(today+pd.Timedelta(days=45)).strftime("%Y-%m-%d")
        try:
            contracts=alpaca_option_contracts(ticker,start,end); meta={x.get("symbol"):x for x in contracts if x.get("symbol")}
        except Exception: meta={}
        for feed in ("indicative","opra"):
            try:
                snaps=_fetch_option_snapshots_with_feed(ticker,start,end,spot,feed)
                if not snaps:
                    out["options"][feed]={"available":False,"reason":"No contracts returned."}; continue
                rows=[]
                for sym,snap in snaps.items():
                    if sym not in meta: continue
                    r=option_contract_row(sym,snap,meta[sym],spot)
                    if r["expiration"] and r["moneyness_pct"] is not None and abs(r["moneyness_pct"])<=15: rows.append(r)
                positioning=modeled_dealer_positioning(rows,spot) if rows else {"available":False}
                spreads=[r["spread_pct"] for r in rows if r.get("spread_pct") is not None]
                out["options"][feed]={
                    "available":True,"contracts":len(rows),
                    "median_spread_pct":round(float(np.median(spreads)),2) if spreads else None,
                    "call_wall":positioning.get("call_wall"),"put_wall":positioning.get("put_wall"),
                    "gamma_regime":positioning.get("gamma_regime"),
                }
            except Exception as e:
                out["options"][feed]={"available":False,"reason":str(e)}
    else:
        out["options"]["indicative"]={"available":False,"reason":"Could not determine spot price."}
        out["options"]["opra"]={"available":False,"reason":"Could not determine spot price."}
    return out

def _cluster_institutional_events(raw, meta):
    """Cluster fragmented prints into contract-level institutional flow events.

    This is intentionally *not* directional classification. It groups nearby prints
    in the same contract into one event when the WHOLE group fits within a 90-second
    window of its first print and within a 7.5% price band of its own range — bounded
    against the cluster's origin, not a shifting consecutive-gap/running-vwap
    reference. The latter can let a chain of individually-small steps stretch a
    single reported "event" across many minutes or a wide price range while never
    tripping either check on any one step.
    """
    seed=[]
    for sym,t in raw:
        p=_safe_float(t.get("p",t.get("price"))); sz=_safe_float(t.get("s",t.get("size")))
        if p is None or sz is None or p<=0 or sz<=0: continue
        ts=_parse_trade_ts(t.get("t",t.get("timestamp")))
        prem=p*sz*100.0
        seed.append((sym,ts,p,int(sz),prem,t))
    seed.sort(key=lambda z:((z[0] or ""), z[1] or datetime.min))
    clusters=[]; cur=None
    for sym,ts,p,sz,prem,t in seed:
        if cur is not None and cur["symbol"]==sym and ts is not None and cur["start_dt"] is not None:
            elapsed=(ts-cur["start_dt"]).total_seconds()
            lo=min(cur["min_price"],p); hi=max(cur["max_price"],p)
            ref=max(lo,.01)
            spread_ok=(hi-lo)/ref<=0.075
            if elapsed<=90 and spread_ok:
                oldprem=cur["premium"]
                cur["premium"]+=prem; cur["size"]+=sz; cur["prints"]+=1
                cur["vwap"]=(cur["vwap"]*oldprem+p*prem)/max(cur["premium"],1)
                cur["end_dt"]=ts; cur["end_timestamp"]=t.get("t",t.get("timestamp"))
                cur["max_print"]=max(cur["max_print"],prem)
                cur["min_price"]=lo; cur["max_price"]=hi
                continue
        if cur is not None: clusters.append(cur)
        r=meta.get(sym,{})
        cur={"symbol":sym,"type":r.get("type"),"expiration":r.get("expiration"),"strike":r.get("strike"),
             "start_dt":ts,"end_dt":ts,"start_timestamp":t.get("t",t.get("timestamp")),"end_timestamp":t.get("t",t.get("timestamp")),
             "vwap":p,"size":sz,"premium":prem,"prints":1,"max_print":prem,
             "min_price":p,"max_price":p,
             "volume":int(r.get("volume") or 0),"open_interest":int(r.get("open_interest") or 0)}
    if cur is not None: clusters.append(cur)

    events=[]
    for e in clusters:
        oi=float(e.get("open_interest") or 0); vol=float(e.get("volume") or 0)
        ratio=(vol/oi) if oi>0 else (None if vol<=0 else 999.0)
        # An event can qualify through aggregate premium, a block-sized child print,
        # or repeated prints whose combined premium crosses the institutional threshold.
        if e["premium"] < FLOW_MIN_PREMIUM and e["max_print"] < FLOW_MIN_PREMIUM: continue
        premium=e["premium"]
        # 0-100 relevance score. This estimates "institutional-looking", not bullish/bearish.
        # Recalibrated in V21.2: a genuine ~$500K block should not be buried as
        # "Watch" simply because volume/OI is quiet. Premium and block size lead;
        # turnover and repeated execution add confidence.
        premium_pts=min(60.0, max(0.0, math.log10((premium/FLOW_MIN_PREMIUM)+1.0)*35.0))
        ratio_pts=0.0 if ratio in (None,0) else min(18.0, (ratio if ratio<999 else 4.0)*4.5)
        repeat_pts=min(12.0,max(0,e["prints"]-1)*2.0)
        block_pts=min(15.0,e["max_print"]/500000.0*15.0)
        score=min(100.0,premium_pts+ratio_pts+repeat_pts+block_pts)
        if score>=60: relevance="High"
        elif score>=40: relevance="Medium"
        else: relevance="Watch"
        events.append({
            "symbol":e["symbol"],"type":e.get("type"),"expiration":e.get("expiration"),"strike":e.get("strike"),
            "price":round(e["vwap"],4),"size":int(e["size"]),"premium":round(premium,2),"prints":int(e["prints"]),
            "max_print":round(e["max_print"],2),"timestamp":e.get("start_timestamp"),"end_timestamp":e.get("end_timestamp"),
            "volume":int(vol),"open_interest":int(oi),"vol_oi":(round(ratio,2) if ratio is not None and ratio<999 else None),
            "institutional_score":round(score,1),"relevance":relevance
        })
    events.sort(key=lambda z:(z["institutional_score"],z["premium"]), reverse=True)
    return events


def flow_payload(ticker, options_payload=None):
    """V21.2 high-coverage institutional-flow engine from Alpaca option trades.

    Broad chain discovery -> institutional candidate prefilter -> near-complete candidate
    coverage -> raw trade history -> fragmented-print clustering -> unusualness scoring.
    Direction is intentionally left unclassified until contemporaneous NBBO alignment is available.
    """
    ticker=ticker.upper().strip()
    x=options_payload or options_quality_payload(ticker)
    spot=_safe_float(x.get("spot"))
    if not spot: raise RuntimeError(f"Could not determine current price for {ticker} flow scan.")
    rows=broad_flow_universe(ticker,spot)

    # Institutional relevance prefilter. This avoids simply taking the highest-volume
    # 160 contracts, which biased V21 toward retail-heavy activity.
    eligible=[]
    for r in rows:
        vol=float(r.get("volume") or 0); oi=float(r.get("open_interest") or 0)
        px=float(r.get("last") or r.get("mid") or 0); last_size=float(r.get("last_size") or 0)
        gross_activity=vol*px*100.0; ratio=(vol/oi) if oi>0 else (999.0 if vol>0 else 0.0)
        last_notional=last_size*px*100.0
        if ((vol>=50 and gross_activity>=100000) or ratio>=1.5 or last_notional>=FLOW_MIN_PREMIUM or vol>=500):
            r=dict(r); r["institutional_candidate_score"]=_institutional_candidate_score(r); eligible.append(r)
    eligible=sorted(eligible,key=lambda r:r.get("institutional_candidate_score",0),reverse=True)
    eligible_total=len(eligible)
    # V21.3 accuracy-first coverage: scan the full candidate set when it fits under
    # the safety ceiling. For very large chains, keep adding candidates until the
    # selected set represents the configured share of estimated institutional
    # activity (default 99.5%) or the hard ceiling is reached. This prioritizes
    # economically meaningful coverage rather than an arbitrary contract count.
    def activity_weight(r):
        vol=float(r.get("volume") or 0); px=float(r.get("last") or r.get("mid") or 0); last_size=float(r.get("last_size") or 0)
        return max(0.0,vol*px*100.0)+max(0.0,last_size*px*100.0)
    eligible_activity=sum(activity_weight(r) for r in eligible)
    if eligible_total<=FLOW_MAX_CANDIDATES:
        candidates=eligible
    else:
        candidates=[]; running=0.0
        target=max(0.0,min(100.0,FLOW_ACTIVITY_COVERAGE_TARGET))/100.0
        for r in eligible:
            if len(candidates)>=FLOW_MAX_CANDIDATES: break
            candidates.append(r); running+=activity_weight(r)
            if eligible_activity>0 and running/eligible_activity>=target: break
    candidate_coverage_pct=(100.0*len(candidates)/eligible_total) if eligible_total else 0.0
    selected_activity=sum(activity_weight(r) for r in candidates)
    activity_coverage_pct=(100.0*selected_activity/eligible_activity) if eligible_activity>0 else candidate_coverage_pct
    if activity_coverage_pct>=99.0: coverage_confidence="High"
    elif activity_coverage_pct>=95.0: coverage_confidence="Medium"
    else: coverage_confidence="Low"
    meta={r["symbol"]:r for r in candidates if r.get("symbol")}; symbols=list(meta)
    if not symbols:
        return {"ticker":ticker,"feed":ALPACA_OPTIONS_FEED,"sampled":True,"prints":0,"premium":0,"note":"No institutional-candidate contracts found in the current broad chain."}

    try:
        from zoneinfo import ZoneInfo
        et=ZoneInfo("America/New_York"); now=datetime.now(et); day=now.date()
        while day.weekday()>=5: day-=timedelta(days=1)
        start=datetime(day.year,day.month,day.day,9,30,tzinfo=et)
        end=min(now,datetime(day.year,day.month,day.day,16,0,tzinfo=et)) if day==now.date() else datetime(day.year,day.month,day.day,16,0,tzinfo=et)
        if end<=start: end=datetime(day.year,day.month,day.day,16,0,tzinfo=et)
        if ALPACA_OPTIONS_FEED=="indicative" and day==now.date(): end=min(end,now-timedelta(minutes=16))
        start_iso=start.isoformat(); end_iso=end.isoformat()
    except Exception:
        day=datetime.utcnow().date(); start_iso=f"{day.isoformat()}T13:30:00Z"; end_iso=f"{day.isoformat()}T20:00:00Z"

    raw=_option_trade_chunks(symbols,start_iso,end_iso,ALPACA_OPTIONS_FEED)
    total=callprem=putprem=0.0; allprints=0; contract_prem={}; contract_prints={}
    for sym,t in raw:
        p=_safe_float(t.get("p",t.get("price"))); sz=_safe_float(t.get("s",t.get("size")))
        if p is None or sz is None or p<=0 or sz<=0: continue
        prem=p*sz*100.0; allprints+=1; total+=prem
        r=meta.get(sym,{}); typ=str(r.get("type") or "").lower()
        if typ.startswith("c"): callprem+=prem
        elif typ.startswith("p"): putprem+=prem
        contract_prem[sym]=contract_prem.get(sym,0.0)+prem; contract_prints[sym]=contract_prints.get(sym,0)+1

    events=_cluster_institutional_events(raw,meta)
    event_total=sum(e["premium"] for e in events)
    event_calls=sum(e["premium"] for e in events if str(e.get("type") or "").lower().startswith("c"))
    event_puts=sum(e["premium"] for e in events if str(e.get("type") or "").lower().startswith("p"))
    high=sum(1 for e in events if e.get("relevance")=="High"); med=sum(1 for e in events if e.get("relevance")=="Medium")

    unusual=[]
    for sym,r in meta.items():
        vol=float(r.get("volume") or 0); oi=float(r.get("open_interest") or 0)
        ratio=(vol/oi) if oi>0 else (999.0 if vol>0 else 0.0); prem=contract_prem.get(sym,0.0)
        if ratio>=1.5 or prem>=250000:
            unusual.append({"symbol":sym,"type":r.get("type"),"expiration":r.get("expiration"),"strike":r.get("strike"),"volume":int(vol),"open_interest":int(oi),"vol_oi":round(ratio,2) if ratio<999 else None,"premium":round(prem,2),"prints":contract_prints.get(sym,0)})
    unusual=sorted(unusual,key=lambda z:(z["premium"],z.get("vol_oi") or 0),reverse=True)[:12]

    return {
        "ticker":ticker,"feed":ALPACA_OPTIONS_FEED,"sampled":True,"engine_version":"21.2",
        "contracts_sampled":len(symbols),"eligible_contracts":len(eligible),"universe_contracts":len(rows),
        "candidate_coverage_pct":round(candidate_coverage_pct,1),"activity_coverage_pct":round(activity_coverage_pct,1),"coverage_confidence":coverage_confidence,
        "start":start_iso,"end":end_iso,"all_prints":allprints,"gross_premium":round(total,2),
        "call_premium":round(callprem,2),"put_premium":round(putprem,2),
        "call_pct":round(callprem/total*100,1) if total else None,"put_pct":round(putprem/total*100,1) if total else None,
        "notable_threshold":FLOW_MIN_PREMIUM,"institutional_events":len(events),"institutional_premium":round(event_total,2),
        "institutional_call_premium":round(event_calls,2),"institutional_put_premium":round(event_puts,2),
        "institutional_call_pct":round(event_calls/event_total*100,1) if event_total else None,
        "institutional_put_pct":round(event_puts/event_total*100,1) if event_total else None,
        "high_relevance_events":high,"medium_relevance_events":med,
        "largest":events[:15],"events":events[:40],"unusual":unusual,"direction_available":False,
        "note":"V21.3 Institutional Flow Engine: broad ~900-day/wide-strike chain, full candidate coverage when practical, and activity-targeted coverage (default 99.5%) for very large chains. Historical aggressor direction is intentionally not fabricated: Alpaca indicative option trades are delayed while quotes are modified/current, and Alpaca does not expose a historical option-NBBO endpoint in the documented REST API. Contract mix is calls vs puts only; use FlowMS as the directional cross-check unless OPRA live quote/trade capture is available."
    }

def options_quality_payload(ticker, gex_window="0-30", dte_max=35, dte_min=7):
    ticker=ticker.upper().strip()
    dte_min=max(0,min(30,int(dte_min or 0)));dte_max=max(dte_min+1,min(90,int(dte_max or 35)))
    today=pd.Timestamp.now().normalize()
    start=(today+pd.Timedelta(days=dte_min)).strftime("%Y-%m-%d")
    end=(today+pd.Timedelta(days=dte_max)).strftime("%Y-%m-%d")
    rv20,spot=realized_vol_20d(ticker)
    if spot is None: raise RuntimeError(f"Could not determine current price for {ticker}.")
    contracts=alpaca_option_contracts(ticker,start,end)
    meta={x.get("symbol"):x for x in contracts if x.get("symbol")}
    snaps=alpaca_option_chain(ticker,start,end,spot)
    rows=[]
    for sym,snap in snaps.items():
        if sym not in meta: continue
        r=option_contract_row(sym,snap,meta[sym],spot)
        if r["expiration"] and r["moneyness_pct"] is not None and abs(r["moneyness_pct"])<=20: rows.append(r)
    ivrows=[r for r in rows if r["iv"] is not None and abs(r["moneyness_pct"])<=8 and (r["delta"] is None or .20<=abs(r["delta"])<=.80)]
    atm_iv=float(np.median([r["iv"] for r in ivrows])) if ivrows else None
    rv_pct=rv20*100 if rv20 is not None else None
    ratio=atm_iv/rv_pct if atm_iv is not None and rv_pct and rv_pct>0 else None
    if ratio is None: ivstate="Unknown"
    elif ratio<.90: ivstate="Cheap / Crushed"
    elif ratio<1.25: ivstate="Normal"
    elif ratio<1.60: ivstate="Elevated"
    else: ivstate="Juiced"
    liquid=sum(r["liquidity"]=="Liquid" for r in rows)
    tradable=sum(r["liquidity"] in ("Liquid","Tradable") for r in rows)
    liq="Liquid" if liquid>=3 else ("Tradable" if tradable>=3 else "Thin")
    rank={"Liquid":0,"Tradable":1,"Thin":2}
    rows.sort(key=lambda r:(rank.get(r["liquidity"],3),abs(r["moneyness_pct"] or 999),-(r["open_interest"] or 0),-(r["volume"] or 0)))
    # GEX has its own expiration universe, independent of the 7-35D swing
    # contract-selection chain. This prevents trade-horizon filtering from
    # accidentally dropping near-expiry gamma that can dominate dealer positioning.
    bucket=str(gex_window or "0-30").lower()
    if bucket not in ("0-7","8-30","31-90","all","0-30"): bucket="0-30"
    ranges={"0-7":(0,7),"0-30":(0,30),"8-30":(8,30),"31-90":(31,90),"all":(0,365)}
    lo,hi=ranges[bucket]; gs=(today+pd.Timedelta(days=lo)).strftime("%Y-%m-%d"); ge=(today+pd.Timedelta(days=hi)).strftime("%Y-%m-%d")
    gcontracts=alpaca_option_contracts(ticker,gs,ge); gmeta={x.get("symbol"):x for x in gcontracts if x.get("symbol")}
    gsnaps=alpaca_option_chain(ticker,gs,ge,spot); gex_rows=[]
    for sym,snap in gsnaps.items():
        if sym not in gmeta: continue
        rr=option_contract_row(sym,snap,gmeta[sym],spot)
        if rr.get("expiration") and rr.get("moneyness_pct") is not None and abs(rr["moneyness_pct"])<=25:gex_rows.append(rr)
    return {
        "ticker":ticker,"spot":round(spot,2),"dte_min":dte_min,"dte_max":dte_max,"gex_window":bucket,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}",
        "chain_updated_at":datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "rv20":round(rv_pct,1) if rv_pct is not None else None,
        "atm_iv":round(atm_iv,1) if atm_iv is not None else None,
        "iv_rv_ratio":round(ratio,2) if ratio is not None else None,
        "iv_state":ivstate,"liquidity":liq,"liquid_contracts":liquid,"tradable_contracts":tradable,
        "contracts_checked":len(rows),"gex_contracts_checked":len(gex_rows),"positioning":modeled_dealer_positioning(gex_rows,spot),"contracts":rows[:120]
    }



def post_earnings_otm_contract(payload, direction="bullish", expected_move_pct=None, min_dte=None, ideal_dte=None):
    """Pick a discounted OTM contract while requiring a real executable market.

    Primary target: ~2-8% OTM and |delta| ~0.25-0.45. Wider spreads remain
    eligible when OI/volume show genuine participation.

    min_dte/ideal_dte let a caller anchor the pick to a ticker's own expected
    drift horizon instead of just grabbing whatever's cheapest in the loaded
    chain — a contract that expires before a typical continuation move even
    finishes isn't actually a post-earnings-drift trade.
    """
    spot=_safe_float((payload or {}).get("spot"))
    if not spot: return None
    want_put=str(direction).lower().startswith("bear")
    min_dte=int(min_dte) if min_dte is not None else 0
    rows=[]
    for r in (payload or {}).get("contracts") or []:
        typ=str(r.get("type") or "").lower()
        is_put=typ.startswith("p")
        if is_put!=want_put: continue
        strike=_safe_float(r.get("strike")); mid=_safe_float(r.get("mid"))
        bid=_safe_float(r.get("bid")); ask=_safe_float(r.get("ask"))
        oi=int(_safe_float(r.get("open_interest")) or 0); vol=int(_safe_float(r.get("volume")) or 0)
        spread=_safe_float(r.get("spread_pct")); delta=abs(_safe_float(r.get("delta")) or 0)
        dte=r.get("dte")
        if dte is not None and dte<min_dte: continue
        if not strike or not mid or mid<=0 or bid is None or bid<=0 or ask is None or ask<=bid: continue
        otm=((spot-strike)/spot*100) if want_put else ((strike-spot)/spot*100)
        if otm<=0 or otm>12: continue

        # Execution labels: wider spread is fine when there is actual participation.
        if oi>=500 and vol>=100 and spread is not None and spread<=10:
            execution="Liquid"; exec_score=18
        elif oi>=100 and vol>=25 and spread is not None and spread<=18:
            execution="Tradable"; exec_score=15
        elif spread is not None and spread<=30 and (oi>=300 or vol>=75) and oi>=50:
            execution="Wide but Active"; exec_score=11
        elif spread is not None and spread<=35 and oi>=100 and vol>=25:
            execution="Wide but Active"; exec_score=9
        else:
            continue

        # Deliberately favor discounted OTM premium without drifting to lottery tickets.
        otm_score=20 if 2<=otm<=8 else (15 if 1<=otm<2 else 10)
        delta_score=18 if .25<=delta<=.45 else (12 if .18<=delta<.25 or .45<delta<=.55 else 4)
        activity=min(12, 4*np.log10(max(1,oi))) + min(8, 3*np.log10(max(1,vol)))
        premium_score=8 if mid<=3 else (6 if mid<=5 else (3 if mid<=8 else 0))
        coverage=None
        if expected_move_pct is not None and expected_move_pct>0:
            coverage=otm/expected_move_pct
            coverage_score=12 if coverage<=.75 else (7 if coverage<=1.0 else 1)
        else:
            coverage_score=5
        # Favor expirations near the ticker's own expected drift horizon so the
        # contract doesn't lapse before a typical continuation move finishes.
        if ideal_dte is not None and dte is not None:
            duration_score=max(0.0,8.0-abs(dte-ideal_dte)/5.0)
        else:
            duration_score=4.0
        score=otm_score+delta_score+exec_score+activity+premium_score+coverage_score+duration_score
        rr=dict(r)
        rr.update({
            "otm_pct":round(otm,2),"execution_quality":execution,
            "post_earnings_contract_score":round(float(score),1),
            "expected_move_coverage":round(float(coverage),2) if coverage is not None else None,
        })
        rows.append(rr)
    if not rows:return None
    rows.sort(key=lambda r:(-r["post_earnings_contract_score"],r["mid"]))
    return rows[0]


def post_earnings_current_move(ticker,event_date):
    try:
        df=dl_ohlc(ticker,"3mo")
        if df is None or len(df)<2:return {}
        pos=event_session_index(df,pd.Timestamp(event_date))
        if pos is None or pos<=0:return {}
        base=float(df["Close"].iloc[pos-1])
        seg=df.iloc[pos:]
        if not len(seg):return {}
        last=float(seg["Close"].iloc[-1])
        return {
            "current_move_pct":round((last/base-1)*100,2),
            "sessions_since":int(len(seg)),
            "holding_gap":bool((last/base-1)>=0) if last>=base else False,
        }
    except Exception:
        return {}


def historical_continuation_score(profile):
    if not profile:return 0.0
    behavior=profile.get("behavior")
    s=float(profile.get("score") or 0)*4.0
    if behavior=="CONTINUATION":s+=20
    elif behavior=="REVERSION":s-=18
    elif behavior=="FAST REACTION":s-=8
    # Directional persistence carries more weight than raw excursion frequency —
    # a stock can post a big excursion and still give it all back.
    s+=min(18,float(profile.get("pct_directional_persist") or 0)*.22)
    s-=min(18,float(profile.get("pct_directional_revert") or 0)*.22)
    s+=min(12,float(profile.get("pct_gt5_10d") or 0)*.12)
    s+=min(8,float(profile.get("pct_gt10_14d") or 0)*.08)
    return max(0.0,min(100.0,s))

def market_payload():
    tickers=["SPY","RSP","IWM","QQQ","HYG","LQD","^VIX","^TNX"]+list(RRG_UNIVERSE)
    prices=dl_prices(tickers,"18mo")

    if "SPY" not in prices.columns:
        raise RuntimeError("SPY data unavailable after provider retry.")
    spy=prices["SPY"].dropna()
    if spy.empty:
        raise RuntimeError("SPY price history was returned empty after provider retry.")

    internals={}
    internals["SPY"]={"value":float(spy.iloc[-1]),"d1":pct_change(spy,1),"d5":pct_change(spy,5),"d20":pct_change(spy,20)}

    for t,label in [("RSP","Breadth"),("IWM","Small caps"),("QQQ","Growth")]:
        raw = prices[t].dropna() if t in prices.columns else pd.Series(dtype=float)
        if t in prices.columns:
            pair=prices[["SPY",t]].dropna()
            ratio=(pair[t]/pair["SPY"]) if len(pair) else pd.Series(dtype=float)
        else:
            ratio=pd.Series(dtype=float)
        internals[t]={
            "value":float(raw.iloc[-1]) if len(raw) else None,
            "raw_d1":pct_change(raw,1) if len(raw) else None,
            "d5":pct_change(ratio,5) if len(ratio) else None,
            "d20":pct_change(ratio,20) if len(ratio) else None,
            "label":label
        }
    # Credit risk appetite: high yield relative to investment grade.
    if "HYG" in prices and "LQD" in prices:
        credit_pair=prices[["HYG","LQD"]].dropna()
        if len(credit_pair):
            credit_ratio=credit_pair["HYG"]/credit_pair["LQD"]
            internals["CREDIT"]={
                "d5":pct_change(credit_ratio,5),
                "d20":pct_change(credit_ratio,20),
                "label":"HYG/LQD"
            }

    # Treasury context: 10-year yield level and short/medium trend.
    if "^TNX" in prices:
        tnx=prices["^TNX"].dropna()
        if len(tnx):
            internals["TNX"]={
                "value":float(tnx.iloc[-1]),
                "d5":pct_change(tnx,5),
                "d20":pct_change(tnx,20),
                "label":"10Y yield"
            }
    if "^VIX" in prices:
        v=prices["^VIX"].dropna()
        internals["VIX"]={"value":float(v.iloc[-1]) if len(v) else None,"d5":pct_change(v,5)}
    signals=[]
    for t in ("RSP","IWM","QQQ"):
        d5,d20=internals[t]["d5"],internals[t]["d20"]
        if d5 is None or d20 is None: continue
        signals.append(1 if d5>0 and d20>0 else (-1 if d5<0 and d20<0 else 0))
    total=sum(signals)
    participation="Broadening" if total>=2 else ("Narrowing" if total<=-2 else "Mixed")

    # Risk appetite is deliberately a context score, not an RRG input.
    risk_components=[]
    for t in ("RSP","IWM","QQQ"):
        d5=(internals.get(t) or {}).get("d5")
        if d5 is not None:
            risk_components.append(1 if d5>0 else -1)
    cd5=(internals.get("CREDIT") or {}).get("d5")
    if cd5 is not None:
        risk_components.append(1 if cd5>0 else -1)

    risk_score=sum(risk_components)
    if risk_score>=3:
        risk_appetite="Risk-On"
    elif risk_score<=-3:
        risk_appetite="Risk-Off"
    else:
        risk_appetite="Mixed"
    rows=dual_rrg_rows(prices,"SPY",list(RRG_UNIVERSE),8,8)
    for r in rows:
        r["name"]=RRG_UNIVERSE.get(r["ticker"],r["ticker"])
        r["group"]="Core Sector" if r["ticker"] in SECTORS else "Industry / Theme"
        r["alignment"]=alignment_label(r.get("fast"), r.get("trend"))
    valid_index = prices.dropna(how="all").index
    if len(valid_index) == 0:
        raise RuntimeError("Market price frame contains no valid dated observations.")
    return {"asof":valid_index.max().strftime("%Y-%m-%d"),"internals":internals,"participation":participation,"risk_appetite":risk_appetite,"risk_score":risk_score,"sectors":rows}




def _ctx_return(series, sessions):
    try:
        s=series.dropna()
        if len(s)<=sessions:return None
        return float((s.iloc[-1]/s.iloc[-1-sessions]-1)*100)
    except Exception:
        return None


def _context_earnings_catalyst(ticker):
    today=pd.Timestamp.now().normalize()
    try: dates=get_earnings_dates(ticker,16)
    except Exception: dates=[]
    future=[]
    for d in dates:
        try:
            x=pd.Timestamp(d).normalize()
            if x>=today: future.append(x)
        except Exception: pass
    if not future:return {"next_earnings":None,"days_to_earnings":None,"risk":"Unknown"}
    nxt=min(future); days=int((nxt-today).days)
    risk="Binary / imminent" if days<=3 else ("Near-term" if days<=10 else "Clear")
    return {"next_earnings":nxt.strftime("%Y-%m-%d"),"days_to_earnings":days,"risk":risk}


def _context_structure(ticker):
    df=dl_ohlc(ticker,"1y")
    if df is None or len(df)<55:return {"available":False}
    df=df.dropna(subset=["Open","High","Low","Close"]).copy()
    c=df["Close"].astype(float);h=df["High"].astype(float);l=df["Low"].astype(float);spot=float(c.iloc[-1])
    prev=c.shift(1);tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    atr=float(tr.rolling(14).mean().iloc[-1]);sma20=float(c.rolling(20).mean().iloc[-1]);sma50=float(c.rolling(50).mean().iloc[-1])
    hi20=float(h.iloc[-21:-1].max());lo20=float(l.iloc[-21:-1].min());hi10=float(h.iloc[-11:-1].max());lo10=float(l.iloc[-11:-1].min())
    direction="bullish" if spot>=sma20>=sma50 else ("bearish" if spot<=sma20<=sma50 else "neutral")
    if direction=="bullish":
        trigger=hi20;confirmation=trigger+.15*atr;invalidation=max(lo10,sma20-.35*atr);hard_fail=max(lo20,sma50-.5*atr);target1=max(trigger+1.5*atr,spot+1.25*atr);target2=max(trigger+3*atr,spot+2.5*atr);risk=max(.01,trigger-invalidation);reward=max(0,target2-trigger)
    elif direction=="bearish":
        trigger=lo20;confirmation=trigger-.15*atr;invalidation=min(hi10,sma20+.35*atr);hard_fail=min(hi20,sma50+.5*atr);target1=min(trigger-1.5*atr,spot-1.25*atr);target2=min(trigger-3*atr,spot-2.5*atr);risk=max(.01,invalidation-trigger);reward=max(0,trigger-target2)
    else:
        trigger=confirmation=invalidation=hard_fail=target1=target2=None;risk=reward=None
    # Integrity gate: directional plans must have levels in the correct order,
    # AND the trigger must still be within a plausible distance of current
    # price. A trigger can pass the ordering check yet be a stale relic of a
    # large intra-window move (e.g. a rally from the 20-day low to a spike and
    # back) that's left the 20-day extreme many ATRs away from where price
    # actually is now — technically ordered, but not an actionable near-term
    # level. Three ATRs matches this plan's own target2 convention (trigger
    # +/- 3*atr) — a trigger already that far from spot describes a price
    # regime the stock has since moved on from.
    plan_valid=True;plan_error=None
    max_trigger_distance=3*atr if atr>0 else None
    if direction=="bullish" and trigger is not None:
        plan_valid=(invalidation is not None and target1 is not None and target2 is not None and invalidation < trigger < target1 <= target2)
        if not plan_valid: plan_error="Invalid bullish level ordering"
        elif max_trigger_distance is not None and abs(spot-trigger)>max_trigger_distance:
            plan_valid=False;plan_error="Trigger is too far from current price (stale 20-day level after a large move)"
    elif direction=="bearish" and trigger is not None:
        plan_valid=(invalidation is not None and target1 is not None and target2 is not None and invalidation > trigger > target1 >= target2)
        if not plan_valid: plan_error="Invalid bearish level ordering"
        elif max_trigger_distance is not None and abs(spot-trigger)>max_trigger_distance:
            plan_valid=False;plan_error="Trigger is too far from current price (stale 20-day level after a large move)"
    if not plan_valid:
        trigger=confirmation=invalidation=hard_fail=target1=target2=None;risk=reward=None
    r20=_ctx_return(c,20);r50=_ctx_return(c,50);trend_strength=int(spot>sma20)+int(sma20>sma50)+int(r20 is not None and r20>0)+int(r50 is not None and r50>0)
    return {"available":True,"spot":round(spot,2),"atr14":round(atr,2),"direction":direction,"trend_strength":trend_strength,"sma20":round(sma20,2),"sma50":round(sma50,2),"trigger":round(trigger,2) if trigger is not None else None,"confirmation":round(confirmation,2) if confirmation is not None else None,"invalidation":round(invalidation,2) if invalidation is not None else None,"hard_fail":round(hard_fail,2) if hard_fail is not None else None,"target1":round(target1,2) if target1 is not None else None,"target2":round(target2,2) if target2 is not None else None,"rr_to_target2":round(reward/risk,2) if risk else None,"plan_valid":plan_valid,"plan_error":plan_error,"return_20d":round(r20,2) if r20 is not None else None,"return_50d":round(r50,2) if r50 is not None else None}


def institutional_context_payload(ticker,parent=None):
    ticker=ticker.upper().strip();parent=(parent or "").upper().strip() or None
    universe=[ticker,"SPY"]+([parent] if parent and parent not in (ticker,"SPY") else [])
    px=dl_prices(universe,"1y");stock=px[ticker].dropna() if ticker in px else pd.Series(dtype=float);spy=px["SPY"].dropna() if "SPY" in px else pd.Series(dtype=float);par=px[parent].dropna() if parent and parent in px else pd.Series(dtype=float)
    rs={};positives=0;observed=0
    for n in (5,10,20):
        sr=_ctx_return(stock,n);mr=_ctx_return(spy,n);pr=_ctx_return(par,n) if len(par) else None;vm=(sr-mr) if sr is not None and mr is not None else None;vp=(sr-pr) if sr is not None and pr is not None else None
        rs[str(n)]={"stock":round(sr,2) if sr is not None else None,"vs_spy":round(vm,2) if vm is not None else None,"vs_parent":round(vp,2) if vp is not None else None}
        for v in (vm,vp):
            if v is not None: observed+=1;positives+=int(v>0)
    persistence=round(100*positives/observed) if observed else None
    triple=bool(parent and all((rs[str(n)].get("vs_spy") if rs[str(n)].get("vs_spy") is not None else -999)>0 and (rs[str(n)].get("vs_parent") if rs[str(n)].get("vs_parent") is not None else -999)>0 for n in (5,10,20)))
    structure=_context_structure(ticker);catalyst=_context_earnings_catalyst(ticker)
    horizon="1–3 week swing" if structure.get("trend_strength",0)>=4 and persistence is not None and persistence>=67 else ("2–5 day swing" if structure.get("trend_strength",0)>=2 else "Tactical / wait for confirmation")
    signature=f"v24|{parent or 'NONE'}|{horizon}|{'triple' if triple else 'mixed-rs'}|{structure.get('direction','neutral')}"
    try:save_setup_snapshot({"ticker":ticker,"spot":structure.get("spot"),"bias":structure.get("direction"),"score":persistence,"signature":signature,"raw":{"parent":parent,"relative_strength":rs,"structure":structure,"horizon":horizon,"catalyst":catalyst}})
    except Exception:pass
    try:
        hist=setup_history_stats(ticker,signature)
        baseline=setup_history_stats(ticker)
    except Exception as e:
        hist={"count":0,"returns":{},"error":str(e)};baseline={"count":0,"returns":{},"error":str(e)}
    macro=macro_risk_snapshot(7)
    return {"ticker":ticker,"parent":parent,"relative_strength":rs,"rotation_persistence":persistence,"triple_relative_strength":triple,"structure":structure,"horizon":horizon,"catalyst":catalyst,"macro_risk":macro,"signature":signature,"historical_expectancy":hist,"ticker_baseline_expectancy":baseline}

@app.get("/api/institutional-context/<ticker>")
def api_institutional_context(ticker):
    try:
        parent=(request.args.get("parent") or "").upper().strip() or None
        key=f"institutional-v25-1:{ticker.upper()}:{parent or 'NONE'}"
        payload,stale,err=cached_refresh_safe(key,lambda:institutional_context_payload(ticker,parent),ttl=900)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/watchlist")
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


@app.get("/api/feed-comparison/<ticker>")
def api_feed_comparison(ticker):
    try:
        if not ALPACA_API_KEY or not ALPACA_API_SECRET:
            return jsonify({"ok":False,"error":"Alpaca is not configured."}),400
        key=f"feed-comparison-v1:{ticker.upper().strip()}"
        payload=cached(key,lambda:feed_comparison_payload(ticker),ttl=90)
        return jsonify({"ok":True,**payload})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

def auth_required():
    return bool(SCREENER_PASSWORD) and not session.get("authenticated", False)

@app.before_request
def protect_app():
    if request.path in ("/health", "/login") or request.path.startswith("/static/"):
        return None
    if auth_required():
        if request.path.startswith("/api/"):
            return jsonify({"ok":False,"error":"Authentication required."}),401
        return redirect("/login")
    return None

@app.route("/login", methods=["GET","POST"])
def login():
    if not SCREENER_PASSWORD:
        return redirect("/")
    error = ""
    if request.method == "POST":
        supplied = request.form.get("password","")
        if hmac.compare_digest(supplied, SCREENER_PASSWORD):
            session["authenticated"] = True
            return redirect("/")
        error = "Incorrect password."

    html = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0e11">
<title>Market Rotation Screener</title>
<style>
body {
  margin: 0;
  background: #0b0e11;
  color: #e5e7eb;
  font: 16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 22px;
}
.box {
  width: min(420px,100%);
  background: #12161b;
  border: 1px solid #27303a;
  border-radius: 16px;
  padding: 22px;
}
h1 { font-size: 20px; margin: 0 0 8px; }
p { color: #8b95a5; }
input, button {
  width: 100%;
  font: inherit;
  padding: 13px;
  border-radius: 10px;
  box-sizing: border-box;
}
input {
  background: #0b0e11;
  color: #fff;
  border: 1px solid #334155;
  margin: 10px 0;
}
button {
  background: #1d4ed8;
  color: #fff;
  border: 0;
  font-weight: 700;
}
.err { color: #fca5a5; }

.positioningGrid,.flowGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:10px}
.metricCard{background:#101820;border:1px solid #26313b;border-radius:10px;padding:10px}
.metricCard .big{font-size:18px;font-weight:800;margin-top:3px}
.metricCard.good .big{color:#34d399}.metricCard.warn .big{color:#f59e0b}.metricCard.bad .big{color:#f87171}
.positioningGrid.gammaSummary{grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px}
.metricCard.callWall{border-color:#166534;background:linear-gradient(180deg,rgba(34,197,94,.08),#101820)}
.metricCard.callWall .big{color:#4ade80}
.metricCard.flipCard{border-color:#6d28d9;background:linear-gradient(180deg,rgba(139,92,246,.08),#101820)}
.metricCard.flipCard .big{color:#a78bfa}
.metricCard.putWall{border-color:#9a3412;background:linear-gradient(180deg,rgba(249,115,22,.08),#101820)}
.metricCard.putWall .big{color:#fb923c}
.metricCard.spotCard{border-color:#334155;background:linear-gradient(180deg,rgba(148,163,184,.06),#101820)}
.gammaHeaderMeta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0 4px;color:var(--muted);font-size:11px}
.gammaHeaderMeta .callSide{color:#86efac;font-weight:800}.gammaHeaderMeta .putSide{color:#fca5a5;font-weight:800}
@media(max-width:780px){.positioningGrid.gammaSummary{grid-template-columns:repeat(2,minmax(130px,1fr))}}
.flowSplit{height:9px;border-radius:8px;overflow:hidden;background:#24303b;display:flex;margin-top:6px}.flowSplit span:first-child{background:#34d399}.flowSplit span:last-child{background:#f87171}
.flowTable{margin-top:8px}.flowDisclosure{margin-top:8px;padding:8px;border-left:3px solid #f59e0b;background:#111820}


/* v24.2 RRG clarity + proportional canvas */
#sectorChart,#stockChart{width:100%!important;height:auto!important;aspect-ratio:3/2;display:block}
@media(max-width:760px){#sectorChart,#stockChart{aspect-ratio:4/3;height:auto!important}}

/* v24.3 dashboard simplification */
/* Sector rotation heat map removed from Dashboard because Sector Summary + RRG already convey the same rotation signal. Dedicated Heat Map view remains available for deeper stock/sector triage. */

.institutionalScore{font-size:17px;font-weight:900;color:#93c5fd}.instRadarHead{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.instRadarHead .status{margin-left:auto}.instPrint{font-weight:800;color:#e2e8f0}.instHot{color:#86efac}.instWarm{color:#fde68a}.instMuted{color:#94a3b8}
.instContextRow td{border-top:none;padding-top:0}
.darkPoolContext{color:#7f97a8;line-height:1.5}
.newsContextGrid{display:grid;grid-template-columns:1fr 1.25fr;gap:14px}.newsCol{min-width:0}.newsItem{padding:8px 0;border-bottom:1px solid #202936}.newsItem:last-child{border-bottom:none}.newsHeadline{font-size:12px;line-height:1.35;color:#e5e7eb}.newsMeta{font-size:10px;color:#64748b;margin-top:3px}.newsWhy{font-size:11px;color:#9fb0c2;margin-top:4px;line-height:1.35}.newsTicker{display:inline-block;font-size:10px;font-weight:800;color:#bfdbfe;border:1px solid #334155;border-radius:999px;padding:2px 6px;margin-right:5px}.newsCat{font-size:9px;color:#c4b5fd;text-transform:uppercase;letter-spacing:.4px}@media(max-width:800px){.newsContextGrid{grid-template-columns:1fr}}

</style>
</head>
<body>
  <div class="box">
    <h1>Market Rotation Screener</h1>
    <p>Enter your password to continue.</p>
    __ERROR__
    <form method="post">
      <input type="password" name="password" placeholder="Password" autofocus>
      <button type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>"""
    err_html = f'<div class="err">{error}</div>' if error else ""
    return Response(html.replace("__ERROR__", err_html), mimetype="text/html")

@app.get("/api/historical-rrg")
def api_historical_rrg():
    """
    Point-in-time RRG replay. Every RRG value is calculated only from prices
    available on or before the selected as-of date. Forward returns are
    calculated separately and never feed the RRG signal.
    """
    try:
        etf=request.args.get("etf","XLK").upper()
        mode=request.args.get("mode","groups").lower()
        date_raw=request.args.get("date")
        if not date_raw:
            return jsonify({"ok":False,"error":"Choose a historical date."}),400

        target=pd.Timestamp(date_raw).normalize()
        today=pd.Timestamp.now().normalize()
        if target>today:
            target=today

        # Download enough history before the target for Trend RRG and enough
        # future data for the optional forward-return panel.
        start=(target-pd.Timedelta(days=550)).strftime("%Y-%m-%d")
        end=(min(today+pd.Timedelta(days=1),target+pd.Timedelta(days=60))).strftime("%Y-%m-%d")

        if mode=="groups":
            benchmark="SPY"
            members=list(RRG_UNIVERSE.keys())
            names={**SECTORS,**INDUSTRIES}
            source="Layer 1 groups vs SPY"
            holdings_total=len(members)
            holdings_as_screened=len(members)
        else:
            if etf not in RRG_UNIVERSE:
                return jsonify({"ok":False,"error":"Choose an ETF from the Layer-1 universe."}),400
            benchmark=etf
            holdings,holdings_source=cached(f"holdings:{etf}",lambda:get_fund_holdings(etf),ttl=3600)

            limit_raw=request.args.get("limit","20").lower()
            if limit_raw=="all":
                chosen=holdings
            else:
                limit=max(5,min(100,int(limit_raw)))
                chosen=holdings[:limit]

            members=[h["ticker"] for h in chosen]
            names={h["ticker"]:h.get("name",h["ticker"]) for h in chosen}
            holdings_total=len(holdings)
            holdings_as_screened=len(chosen)
            source=f"{etf} holdings · {holdings_source}"

        tickers=[benchmark]+members
        raw=yf.download(tickers,start=start,end=end,auto_adjust=True,progress=False,threads=True)

        if raw is None or raw.empty:
            raise RuntimeError("No historical price data returned.")

        if isinstance(raw.columns,pd.MultiIndex):
            if "Close" in raw.columns.get_level_values(0):
                prices=raw["Close"].copy()
            elif "Adj Close" in raw.columns.get_level_values(0):
                prices=raw["Adj Close"].copy()
            else:
                prices=raw.xs(raw.columns.levels[0][0],axis=1,level=0)
        else:
            prices=raw.copy()
            if len(tickers)==1:
                prices=pd.DataFrame({tickers[0]:prices["Close"]})

        prices.index=pd.to_datetime(prices.index).tz_localize(None)
        prices=prices.sort_index()

        # Snap weekends/holidays to the last completed trading session.
        eligible=prices.index[prices.index<=target]
        if len(eligible)==0:
            raise RuntimeError("No trading session exists on or before that date.")
        asof=pd.Timestamp(eligible[-1]).normalize()

        signal_prices=prices.loc[prices.index<=asof]
        rows=dual_rrg_rows(signal_prices,benchmark,members,8,8)

        # Point-in-time forward returns, kept separate from signal computation.
        horizons=(1,5,10,20)
        for r in rows:
            t=r["ticker"]
            r["name"]=names.get(t,t)
            r["forward"]={}
            if t not in prices.columns:
                continue
            s=prices[t].dropna()
            hist=s[s.index<=asof]
            fut=s[s.index>asof]
            if hist.empty:
                continue
            base=float(hist.iloc[-1])
            for h in horizons:
                if len(fut)>=h:
                    r["forward"][str(h)]=round((float(fut.iloc[h-1])/base-1)*100,2)
                else:
                    r["forward"][str(h)]=None

        return jsonify({
            "ok":True,
            "requested_date":target.strftime("%Y-%m-%d"),
            "asof":asof.strftime("%Y-%m-%d"),
            "mode":mode,
            "benchmark":benchmark,
            "etf":etf,
            "source":source,
            "holdings_total":holdings_total,
            "holdings_as_screened":holdings_as_screened,
            "results":rows,
            "caveat":(
                "Holdings reflect TODAY's fund composition applied retroactively to this "
                "historical date, not the fund's actual holdings as of that date. This "
                "biases the sample toward names that performed well enough to remain (or "
                "become) top holdings today — treat forward-return stats here as "
                "illustrative, not a rigorous backtest."
            ) if mode=="stocks" else None
        })
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


@app.get("/api/premium-support/<ticker>")
def api_premium_support(ticker):
    try:
        direction=(request.args.get("direction") or "bullish").lower()
        if direction not in ("bullish","bearish"):direction="bullish"
        base,_,_=cached_refresh_safe(f"options-v24-1:{ticker.upper()}:0-30:7:35",lambda:options_quality_payload(ticker,"0-30",35,7),ttl=600)
        payload,stale,err=cached_refresh_safe(f"premium-support-v25-9:{ticker.upper()}:{direction}",lambda:premium_support_payload(ticker,direction,base),ttl=1800)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/options/<ticker>")
def api_options(ticker):
    try:
        force=request.args.get("refresh")=="1"
        bucket=(request.args.get("gex_window") or "0-30").lower();dmin=max(0,min(30,int(request.args.get("dte_min",7))));dmax=max(dmin+1,min(90,int(request.args.get("dte_max",35))));payload,stale,err=cached_refresh_safe(f"options-v24-1:{ticker.upper()}:{bucket}:{dmin}:{dmax}",lambda:options_quality_payload(ticker,bucket,dmax,dmin),force=force,ttl=600)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/flow/<ticker>")
def api_flow(ticker):
    try:
        force=request.args.get("refresh") in ("1","true","yes")
        # Reuse the exact options payload already loaded by the deep-dive panel.
        # Previously Flow used an old cache namespace, forcing a second full chain
        # download immediately after /api/options and making the panel appear stuck.
        base,_,_=cached_refresh_safe(f"options-v24-1:{ticker.upper()}:0-30:7:35",lambda:options_quality_payload(ticker,"0-30",35,7),ttl=600)
        payload,stale,err=cached_refresh_safe(f"flow-v23-6:{ticker.upper()}",lambda:flow_payload(ticker,base),force=force,ttl=600)
        return jsonify({"ok":True,"stale":stale,"refresh_error":err,**payload})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.post("/api/options-scan")
def api_options_scan():
    try:
        body=request.get_json(silent=True) or {}
        symbols=[]
        for s in body.get("symbols",[]):
            s=str(s).upper().strip()
            if s and s not in symbols: symbols.append(s)
        if not symbols: return jsonify({"ok":False,"error":"No symbols supplied."}),400
        # Scan the entire filtered ticker set supplied by the live RRG.
        # Keep a generous safety ceiling only to prevent accidental abuse.
        symbols=symbols[:100]
        if not ALPACA_API_KEY or not ALPACA_API_SECRET:
            return jsonify({"ok":False,"error":"Alpaca is not configured. Add APCA_API_KEY_ID and APCA_API_SECRET_KEY in Render."}),422
        def one(sym):
            try:
                p,stale,err=cached_refresh_safe(f"options-v24-1:{sym}:0-30:7:35",lambda:options_quality_payload(sym,"0-30",35,7),ttl=600)
                return {"ok":True,**p,"stale":stale}
            except Exception as e:
                return {"ok":False,"ticker":sym,"error":str(e)}
        results=[]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs=[ex.submit(one,s) for s in symbols]
            for f in as_completed(futs): results.append(f.result())
        order={s:i for i,s in enumerate(symbols)}
        results.sort(key=lambda x:order.get(x.get("ticker"),999))
        return jsonify({"ok":True,"results":results})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


@app.post("/api/setup-snapshot")
def api_setup_snapshot():
    try:return jsonify({"ok":True,**save_setup_snapshot(request.get_json(silent=True) or {})})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),400

@app.get("/api/setup-history/<ticker>")
def api_setup_history(ticker):
    try:return jsonify({"ok":True,**setup_history_stats(ticker,request.args.get("signature"))})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/chart-preview/<ticker>")
def api_chart_preview(ticker):
    ticker=ticker.upper().strip()
    try:
        period=(request.args.get("period") or "1m").lower()
        timeframe=(request.args.get("timeframe") or "1d").lower()
        if period not in ("1m","3m","6m"): period="1m"
        if timeframe not in ("1h","4h","1d","1w"): timeframe="1d"

        rows=[]
        if timeframe in ("1h","4h"):
            bars=alpaca_chart_bars(ticker,timeframe,period)
            max_bars={"1h":{"1m":160,"3m":420,"6m":780},"4h":{"1m":44,"3m":110,"6m":210}}[timeframe][period]
            bars=bars[-max_bars:]
            for b in bars:
                rows.append({
                    "date":pd.Timestamp(b["dt"]).strftime("%Y-%m-%dT%H:%M:%S"),
                    "open":round(float(b["open"]),4),
                    "high":round(float(b["high"]),4),
                    "low":round(float(b["low"]),4),
                    "close":round(float(b["close"]),4),
                    "volume":int(b["volume"])
                })
        else:
            df=dl_ohlc(ticker,"2y")
            if df is None or len(df)==0:
                return jsonify({"ok":False,"error":"No price history available."}),404
            df=df.dropna(subset=["Close"]).copy()
            if timeframe=="1w":
                df=df.resample("W-FRI").agg({
                    "Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"
                }).dropna(subset=["Close"])
                bars_n={"1m":8,"3m":16,"6m":30}.get(period,8)
            else:
                bars_n={"1m":22,"3m":66,"6m":126}.get(period,22)
            df=df.tail(bars_n)
            for idx,row in df.iterrows():
                rows.append({
                    "date":pd.Timestamp(idx).strftime("%Y-%m-%d"),
                    "open":None if pd.isna(row.get("Open")) else round(float(row.get("Open")),4),
                    "high":None if pd.isna(row.get("High")) else round(float(row.get("High")),4),
                    "low":None if pd.isna(row.get("Low")) else round(float(row.get("Low")),4),
                    "close":round(float(row.get("Close")),4),
                    "volume":None if pd.isna(row.get("Volume")) else int(row.get("Volume"))
                })

        try:
            visible_profiles, vp_stale, vp_err=cached_refresh_safe(
                f"visible-v25-1:{ALPACA_STOCK_FEED}:{ticker}:{period}:{timeframe}",
                lambda:alpaca_visible_profiles(ticker,period,timeframe),ttl=120)
        except Exception as visible_err:
            visible_profiles={"sessions":[],"weeks":[],"source":None,"error":str(visible_err)}; vp_stale=False; vp_err=str(visible_err)
        sess=(visible_profiles or {}).get("sessions") or []; weeks=(visible_profiles or {}).get("weeks") or []
        profiles={
            "session":(sess[-1].get("profile") if sess else None),
            "previous":(sess[-2].get("profile") if len(sess)>=2 else None),
            "current_week":(weeks[-1].get("profile") if weeks else None),
            "previous_week":(weeks[-2].get("profile") if len(weeks)>=2 else None),
            "error":(visible_profiles or {}).get("error") or vp_err,
            "stale":vp_stale,
        }
        # Value migration compares the latest two completed session profiles.
        migration=None
        if len(sess)>=2:
            a=sess[-2].get("profile") or {}; b=sess[-1].get("profile") or {}
            dp=(_safe_float(b.get("poc")) or 0)-(_safe_float(a.get("poc")) or 0)
            dvh=(_safe_float(b.get("vah")) or 0)-(_safe_float(a.get("vah")) or 0)
            dvl=(_safe_float(b.get("val")) or 0)-(_safe_float(a.get("val")) or 0)
            tol=max(.01,(float(rows[-1]["close"])*.0005 if rows else .01))
            if dp>tol and dvh>=-tol and dvl>=-tol: state="Rising value"; direction="bullish"
            elif dp<-tol and dvh<=tol and dvl<=tol: state="Falling value"; direction="bearish"
            else: state="Balanced / mixed value"; direction="neutral"
            migration={"state":state,"direction":direction,"poc_change":round(dp,4),"vah_change":round(dvh,4),"val_change":round(dvl,4),"from":sess[-2].get("date"),"to":sess[-1].get("date")}
        return jsonify({
            "ok":True,"ticker":ticker,"period":period,"timeframe":timeframe,
            "bars":rows,"volume_profiles":profiles,"value_migration":migration,
            "visible_profiles":visible_profiles
        })
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


def _strat_scenario(prev, cur):
    ph, pl = float(prev["High"]), float(prev["Low"])
    ch, cl = float(cur["High"]), float(cur["Low"])
    inside = ch <= ph and cl >= pl
    outside = ch > ph and cl < pl
    if outside: return "3"
    if inside: return "1"
    if ch > ph and cl >= pl: return "2U"
    if cl < pl and ch <= ph: return "2D"
    # Rare equal/overlap case: use close direction only as an ambiguous 2.
    return "2U" if float(cur["Close"]) >= float(cur["Open"]) else "2D"


def _strat_frame(df, label):
    if df is None or len(df) < 4:
        return {"timeframe":label,"scenario":"—","pattern":"Insufficient data","direction":"neutral","ftc":"neutral"}
    d=df.dropna(subset=["Open","High","Low","Close"]).copy()
    if len(d)<4:
        return {"timeframe":label,"scenario":"—","pattern":"Insufficient data","direction":"neutral","ftc":"neutral"}
    scenarios=[]
    for i in range(1,len(d)):
        scenarios.append(_strat_scenario(d.iloc[i-1],d.iloc[i]))
    cur=scenarios[-1]
    last=d.iloc[-1]
    prev=d.iloc[-2]
    direction="bullish" if cur=="2U" else ("bearish" if cur=="2D" else "neutral")
    pattern=""
    if len(scenarios)>=3:
        a,b,c=scenarios[-3:]
        if a in ("2U","2D") and b=="1" and c in ("2U","2D"):
            pattern=f"2-1-2 {'Bullish' if c=='2U' else 'Bearish'}"
        elif a=="3" and b=="1" and c in ("2U","2D"):
            pattern=f"3-1-2 {'Bullish' if c=='2U' else 'Bearish'}"
        elif a=="1" and b in ("2U","2D") and c in ("2U","2D"):
            pattern=f"1-2-2 {'Bullish' if c=='2U' else 'Bearish'}"
        elif a in ("2U","2D") and b in ("2U","2D") and c in ("2U","2D") and b!=c:
            pattern=f"2-2 Reversal {'Up' if c=='2U' else 'Down'}"
    if not pattern:
        pattern={"1":"Inside bar / compression","2U":"Directional 2 Up","2D":"Directional 2 Down","3":"Outside bar"}.get(cur,cur)
    # FTC ("full timeframe continuity") should reflect the STRAT scenario
    # direction already computed as `direction` above, not raw candle color.
    # Candle color (close vs open) is a materially different, weaker signal:
    # a bar can close green during an inside-bar (scenario 1) compression with
    # no actual directional trigger, and that was previously still counted as
    # "bullish FTC" — feeding the cross-timeframe continuity score that other
    # parts of the app (Top Setups, confluence direction) treat as confirmed.
    ftc=direction
    return {
        "timeframe":label,
        "scenario":cur,
        "pattern":pattern,
        "direction":direction,
        "ftc":ftc,
        "open":round(float(last["Open"]),4),
        "close":round(float(last["Close"]),4),
        "up_trigger":round(float(last["High"]),4),
        "down_trigger":round(float(last["Low"]),4),
        "active_trigger":round(float(prev["High"] if cur=="2U" else prev["Low"] if cur=="2D" else last["High"]),4),
    }


def _four_hour_from_hourly(hourly):
    if hourly is None or hourly.empty:return pd.DataFrame()
    d=hourly.copy()
    idx=pd.DatetimeIndex(d.index)
    if idx.tz is not None:
        try: idx=idx.tz_convert("America/New_York")
        except Exception: idx=idx.tz_convert(None)
    d.index=idx
    # Keep regular-session bars and aggregate every four sequential hourly bars per session.
    try:d=d.between_time("09:30","16:00")
    except Exception:pass
    chunks=[]
    for _,g in d.groupby(pd.Index(d.index.date)):
        g=g.sort_index().copy()
        if g.empty:continue
        g["_bucket"]=np.arange(len(g))//4
        agg=g.groupby("_bucket").agg(Open=("Open","first"),High=("High","max"),Low=("Low","min"),Close=("Close","last"),Volume=("Volume","sum"))
        # give each synthetic candle the timestamp of the final constituent bar
        times=[g[g["_bucket"]==b].index[-1] for b in agg.index]
        agg.index=pd.DatetimeIndex(times)
        chunks.append(agg)
    return pd.concat(chunks).sort_index() if chunks else pd.DataFrame()


def strat_payload(ticker):
    ticker=ticker.upper().strip()
    # Prefer the paid consolidated Alpaca SIP feed for hourly STRAT bars.
    # This avoids a second Yahoo request immediately after Chart Review and
    # keeps intraday price-action analysis on the same canonical source.
    intraday=pd.DataFrame()
    try:
        abars=alpaca_chart_bars(ticker,"1h","3m")
        if abars:
            intraday=pd.DataFrame([{
                "Open":b.get("open"),"High":b.get("high"),"Low":b.get("low"),
                "Close":b.get("close"),"Volume":b.get("volume"),"dt":b.get("dt")
            } for b in abars])
            if len(intraday):
                intraday.index=pd.to_datetime(intraday.pop("dt")).tz_localize(None)
                intraday=intraday.sort_index()
    except Exception:
        intraday=pd.DataFrame()
    if intraday is None or len(intraday)==0:
        intraday=_yf_download_retry(ticker,"60d","60m",timeout=12,attempts=1,prepost=False)
    daily=dl_ohlc(ticker,"2y")
    if intraday is None:intraday=pd.DataFrame()
    if daily is None:daily=pd.DataFrame()
    if len(intraday):intraday=intraday.sort_index()
    if len(daily):daily=daily.sort_index()
    fourh=_four_hour_from_hourly(intraday)
    weekly=pd.DataFrame()
    if len(daily):
        weekly=daily.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna(subset=["Open","High","Low","Close"])
    frames=[
        _strat_frame(intraday,"1H"),
        _strat_frame(fourh,"4H"),
        _strat_frame(daily,"1D"),
        _strat_frame(weekly,"1W"),
    ]
    bulls=sum(1 for x in frames if x.get("ftc")=="bullish")
    bears=sum(1 for x in frames if x.get("ftc")=="bearish")
    continuity="bullish" if bulls>bears else ("bearish" if bears>bulls else "mixed")
    return {"ticker":ticker,"frames":frames,"bullish_count":bulls,"bearish_count":bears,"continuity":continuity}

@app.get("/api/strat/<ticker>")
def api_strat(ticker):
    ticker=ticker.upper().strip()
    try:
        payload,stale,err=cached_refresh_safe(f"strat-v25-1:{ticker}",lambda:strat_payload(ticker),ttl=120)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:return jsonify({"ok":False,"error":str(e)}),500


@app.get("/api/market")
def api_market():
    try:
        force = request.args.get("refresh")=="1"
        payload, stale, refresh_error = cached_refresh_safe("market", market_payload, force=force)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":refresh_error})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


@app.get("/api/ticker-search")
def api_ticker_search():
    term=(request.args.get("q") or "").strip().upper()
    if not term:
        return jsonify({"ok":True,"query":"","matches":[]})
    matches=[]
    seen=set()
    errors=[]
    for etf,label in RRG_UNIVERSE.items():
        try:
            bundle=cached(f"holdings:{etf}",lambda etf=etf:get_fund_holdings(etf),ttl=3600)
            holdings,source=bundle
            holdings=apply_sector_supplements(etf,holdings)
            for h in holdings:
                ticker=str(h.get("ticker") or "").upper()
                name=str(h.get("name") or "")
                if term==ticker or term in ticker or term in name.upper():
                    key=(ticker,etf)
                    if key not in seen:
                        seen.add(key)
                        matches.append({"ticker":ticker,"name":name,"etf":etf,"group_name":label,"source":source})
        except Exception as e:
            errors.append({"etf":etf,"error":str(e)})
    matches.sort(key=lambda x:(0 if x["ticker"]==term else 1, len(x["ticker"]), x["ticker"], x["etf"]))
    return jsonify({"ok":True,"query":term,"matches":matches[:30],"groups_checked":len(RRG_UNIVERSE),"groups_failed":len(errors)})

@app.get("/api/sector/<etf>")
def api_sector(etf):
    etf=etf.upper()
    if etf not in RRG_UNIVERSE:
        return jsonify({"ok":False,"error":"Choose an ETF from the Layer-1 RRG universe."}),400
    try:
        limit_raw=request.args.get("limit","20").lower()
        limit=None if limit_raw=="all" else max(5,min(100,int(limit_raw)))
        force_holdings = request.args.get("refresh")=="1"
        holding_bundle, holdings_stale, holdings_refresh_error = cached_refresh_safe(
            f"holdings:{etf}", lambda:get_fund_holdings(etf), force=force_holdings, ttl=3600
        )
        holdings, holdings_source = holding_bundle
        holdings = apply_sector_supplements(etf, holdings)
        chosen=holdings if limit is None else holdings[:limit]
        tickers=[etf]+[h["ticker"] for h in chosen]
        prices=dl_prices(tickers,"18mo")
        rows=dual_rrg_rows(prices,etf,[h["ticker"] for h in chosen],8,8)
        meta={h["ticker"]:h for h in holdings}
        # Parent ETF context versus SPY for alignment labels.
        parent_prices=dl_prices(["SPY",etf],"18mo")
        parent_fast_list=rrg_rows(parent_prices,"SPY",[etf],10,5,8)
        parent_trend_list=rrg_rows(parent_prices,"SPY",[etf],25,12,8)
        parent_fast=parent_fast_list[0] if parent_fast_list else None
        parent_trend=parent_trend_list[0] if parent_trend_list else None
        for r in rows:
            m=meta.get(r["ticker"],{})
            r["name"]=m.get("name",r["ticker"]); r["weight"]=m.get("weight")
            r["alignment"]=alignment_label(r.get("fast"),r.get("trend"),parent_fast,parent_trend)
        return jsonify({"ok":True,"sector":etf,"sector_name":RRG_UNIVERSE.get(etf,etf),
                        "holdings_as_screened":len(chosen),"holdings_total":len(holdings),
                        "holdings_source":holdings_source,
                        "holdings_stale":holdings_stale,
                        "holdings_refresh_error":holdings_refresh_error,
                        "results":rows,"holdings":[{"ticker":h["ticker"],"name":h["name"],"weight":h.get("weight")} for h in chosen],"asof":rows[0]["date"] if rows else None})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/postearnings/<etf>")
def api_postearnings(etf):
    etf = etf.upper()
    try:
        if etf not in RRG_UNIVERSE:
            return jsonify({"ok":False,"error":"Choose an ETF from the Layer-1 universe."}),400

        recent_days = max(3, min(30, int(request.args.get("days","10"))))

        # Full holdings universe, cached.
        holdings, holdings_source = cached(
            f"holdings:{etf}",
            lambda:get_fund_holdings(etf),
            ttl=3600
        )
        holdings = apply_sector_supplements(etf, holdings)
        tickers = [h["ticker"] for h in holdings]

        # Calendar-level discovery only.
        recent_map, earnings_diag = discover_recent_earnings(tickers, recent_days)
        now = pd.Timestamp.now().normalize()

        recent = []
        for h in holdings:
            t = h["ticker"]
            meta = recent_map.get(t)
            if not meta:
                continue
            d = pd.Timestamp(meta["date"]).normalize()
            recent.append((h, d, meta))

        if not recent:
            return jsonify({
                "ok":True,
                "sector":etf,
                "sector_name":RRG_UNIVERSE.get(etf,etf),
                "recent_days":recent_days,
                "results":[],
                "holdings_source":holdings_source,
                "holdings_total_loaded":len(holdings),
                "earnings_diagnostics":earnings_diag,
                "message":"No recent earnings were found in the loaded ETF holdings using the available calendar sources."
            })

        # Batch one price request for only the recent reporters + benchmark.
        names = [x[0]["ticker"] for x in recent]
        prices = dl_prices([etf] + names, "18mo")
        rrg_map = {r["ticker"]: r for r in dual_rrg_rows(prices, etf, names, 8, 8)}

        results = []
        for h, d, event_meta in recent:
            r = rrg_map.get(h["ticker"], {})
            results.append({
                "ticker": h["ticker"],
                "name": h["name"],
                "weight": h.get("weight"),
                "earnings_date": d.strftime("%Y-%m-%d"),
                "calendar_days_ago": max(0,(now-d).days),
                "earnings_time": event_meta.get("time"),
                "earnings_source": event_meta.get("source"),
                "eps_estimate": event_meta.get("eps_estimate"),
                "eps_actual": event_meta.get("eps_actual"),
                "revenue_estimate": event_meta.get("revenue_estimate"),
                "revenue_actual": event_meta.get("revenue_actual"),
                "profile": None,
                "rotation": r,
                "current_score": float(r.get("score",0)) if r else 0.0,
            })

        # Rank main screen by current rotation only; historical mover loads lazily.
        results.sort(key=lambda x: -x.get("current_score",0))

        return jsonify({
            "ok":True,
            "sector":etf,
            "sector_name":RRG_UNIVERSE.get(etf,etf),
            "recent_days":recent_days,
            "results":results,
            "holdings_source":holdings_source,
            "holdings_total_loaded":len(holdings),
            "earnings_diagnostics":earnings_diag
        })

    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500



@app.get("/api/postearnings-opportunities")
def api_postearnings_opportunities():
    """Fast market-wide post-earnings scanner.

    Important: options are intentionally NOT loaded in this request. Render can
    terminate long synchronous requests with a 502. This endpoint discovers and
    ranks stock candidates first; the browser then hydrates options per row.
    """
    recent_days=max(3,min(10,int(request.args.get("days","5"))))
    def _build():
        all_holdings={}; parent_map={}; sources=set()
        for etf in RRG_UNIVERSE:
            try:
                holdings,source=cached(f"holdings:{etf}",lambda etf=etf:get_fund_holdings(etf),ttl=3600)
                holdings=apply_sector_supplements(etf,holdings); sources.add(source)
                for h in holdings:
                    sym=str(h.get("ticker") or "").upper().strip()
                    if not sym: continue
                    all_holdings.setdefault(sym,h); parent_map.setdefault(sym,[]).append(etf)
            except Exception:
                continue
        tickers=list(all_holdings)

        # Reuse the calendar discovery cache. Keep this request stock-focused.
        recent_map,diag=discover_recent_earnings(tickers,recent_days)
        reporters=[s for s in tickers if s in recent_map]
        now=pd.Timestamp.now().normalize()
        if not reporters:
            return {"results":[],"universe":len(tickers),
                    "recent_reporters":0,"diagnostics":diag}

        # One batch price request supplies both RRG and current post-earnings move.
        prices=dl_prices(["SPY"]+reporters,"18mo")
        rrg={r["ticker"]:r for r in dual_rrg_rows(prices,"SPY",reporters,8,8)}

        def current_from_frame(sym,event_date):
            try:
                if sym not in prices.columns:return {}
                s=prices[sym].dropna()
                if len(s)<2:return {}
                d=pd.Timestamp(event_date).normalize()
                before=s[s.index.normalize()<d]
                after=s[s.index.normalize()>=d]
                if before.empty or after.empty:return {}
                base=float(before.iloc[-1]); last=float(after.iloc[-1]); day1=float(after.iloc[0])
                return {"current_move_pct":round((last/base-1)*100,2),
                        "day1_move_pct":round((day1/base-1)*100,2),
                        "sessions_since":int(len(after))}
            except Exception:return {}

        # Cheap pre-rank first. This prevents dozens of per-ticker history calls.
        prelim=[]
        for sym in reporters:
            meta=recent_map[sym]; d=pd.Timestamp(meta["date"]).normalize()
            rot=rrg.get(sym,{})
            f=rot.get("fast") or {}; tr=rot.get("trend") or {}
            cur=current_from_frame(sym,d)
            move=abs(float(cur.get("current_move_pct") or 0))
            f_in=((f.get("tail_trajectory")=="Rotating In") if f.get("tail_trajectory") else (f.get("rs_up") is True and f.get("mom_up") is True))
            t_in=((tr.get("tail_trajectory")=="Rotating In") if tr.get("tail_trajectory") else (tr.get("rs_up") is True and tr.get("mom_up") is True))
            pre=move*2.2+(12 if f_in else 0)+(8 if t_in else 0)+(5 if f.get("quadrant") in ("Leading","Improving") else 0)
            prelim.append((pre,sym,d,rot,cur))
        prelim.sort(reverse=True,key=lambda x:x[0])

        # Historical work only for the strongest 10 stock candidates.
        def enrich(item):
            pre,sym,d,rot,cur=item
            dates=merged_historical_earnings_dates(sym,d.strftime("%Y-%m-%d"))
            profile=earnings_profile(sym,dates)
            if not profile:return None
            hist_score=historical_continuation_score(profile)
            f=rot.get("fast") or {}; tr=rot.get("trend") or {}
            f_in=((f.get("tail_trajectory")=="Rotating In") if f.get("tail_trajectory") else (f.get("rs_up") is True and f.get("mom_up") is True))
            t_in=((tr.get("tail_trajectory")=="Rotating In") if tr.get("tail_trajectory") else (tr.get("rs_up") is True and tr.get("mom_up") is True))
            rot_score=(12 if f_in else 0)+(8 if t_in else 0)+(5 if f.get("quadrant") in ("Leading","Improving") else 0)
            move=float(cur.get("current_move_pct") or 0)
            current_score=min(25,abs(move)*2.2)+(4 if profile.get("behavior")=="CONTINUATION" else 0)
            expected=max(float(profile.get("median_exc10") or 0),float(profile.get("median_exc14") or 0))

            # How much runway is left in the expected drift window. A stock on
            # day 9 of a ~14-session historical drift isn't a fresh setup anymore.
            expected_window=14 if profile.get("has_exc14_data") else 10
            sessions_since=cur.get("sessions_since")
            window_progress_pct=round(min(150.0,100.0*sessions_since/expected_window),1) if sessions_since else None
            if window_progress_pct is not None and window_progress_pct>=100:
                current_score-=6  # tail of the move, not the start of it

            # Round-trip / give-back check: if the move has already faded back
            # toward (or through) the pre-earnings base, the initial reaction failed
            # regardless of how big the raw excursion looked.
            day1_move=cur.get("day1_move_pct")
            round_trip=False
            retained_pct=None
            # Only trust the retained/round-trip check when day 1 actually moved
            # a meaningful amount. Dividing by a near-flat first-day reaction
            # (e.g. 0.2%) blows the ratio up into noisy, meaningless numbers.
            if day1_move is not None and abs(day1_move)>=1.0 and sessions_since and sessions_since>1:
                retained_pct=round(100.0*move/day1_move,1)
                if (move*day1_move)<0 or retained_pct<=15:
                    round_trip=True
                    current_score-=15

            # Standardized earnings-surprise magnitude. Only rewarded when it
            # actually agrees with the direction of the price reaction — a beat
            # that the market shrugged off isn't evidence of a real move.
            meta=recent_map.get(sym) or {}
            surprise_pct=None
            est=_safe_float(meta.get("eps_estimate")); act=_safe_float(meta.get("eps_actual"))
            if est not in (None,0) and act is not None:
                surprise_pct=round((act-est)/abs(est)*100,1)
                # Compare against the day-1 reaction (the market's direct response
                # to the surprise), not the cumulative "current" move — a beat that
                # popped on day 1 and later faded was still an aligned reaction.
                react_move=day1_move if day1_move is not None else move
                aligned=(surprise_pct>0 and react_move>=0) or (surprise_pct<0 and react_move<0)
                if aligned:
                    current_score+=min(8.0,abs(surprise_pct)*0.15)

            total=max(0.0,min(100.0,.48*hist_score+current_score+rot_score))
            return {
                "ticker":sym,"name":all_holdings[sym].get("name"),
                "earnings_date":d.strftime("%Y-%m-%d"),
                "calendar_days_ago":max(0,(now-d).days),"parents":parent_map.get(sym,[]),
                "profile":profile,"historical_score":round(hist_score,1),
                "current":cur,"rotation":rot,"best_contract":None,
                "options_execution":"Loading…","options_loading":True,
                "expected_continuation_pct":round(expected,2),
                "direction":"bullish" if move>=0 else "bearish",
                "opportunity_score":round(float(total),1),
                "eps_surprise_pct":surprise_pct,
                "drift_window_sessions":expected_window,
                "drift_window_progress_pct":window_progress_pct,
                "retained_pct_of_day1_move":retained_pct,
                "round_trip":round_trip,
            }

        rows=[]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs=[ex.submit(enrich,x) for x in prelim[:10]]
            for f in as_completed(futs):
                try:
                    x=f.result()
                    if x:rows.append(x)
                except Exception:pass
        rows.sort(key=lambda x:-x.get("opportunity_score",0))
        return {"results":rows[:8],"universe":len(tickers),
                "recent_reporters":len(reporters),"recent_days":recent_days,
                "diagnostics":diag,"holdings_sources":sorted(sources),
                "options_deferred":True}

    try:
        key=f"postearnings-opportunities-v1:{recent_days}"
        payload,stale,err=cached_refresh_safe(key,_build,ttl=300)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


@app.get("/api/postearnings-option/<ticker>")
def api_postearnings_option(ticker):
    ticker=ticker.upper().strip()
    try:
        direction=request.args.get("direction","bullish")
        expected=_safe_float(request.args.get("expected"))

        # Anchor DTE selection to this ticker's own historical drift window rather
        # than reusing whatever chain window happens to be loaded elsewhere. A
        # CONTINUATION name needs enough duration for the drift to actually play
        # out; a REVERSION name shouldn't be biased toward extra duration at all.
        profile=cached(f"peprofile-v1:{ticker}",
                        lambda:earnings_profile(ticker,merged_historical_earnings_dates(ticker)),ttl=3600)
        behavior=(profile or {}).get("behavior")
        if behavior=="CONTINUATION":
            min_dte,ideal_dte=21,30
        elif behavior=="REVERSION":
            min_dte,ideal_dte=0,14
        else:
            min_dte,ideal_dte=10,21
        dte_max=max(30,min(90,ideal_dte+21))

        payload,stale,err=cached_refresh_safe(
            f"options-v22:{ticker}:{dte_max}",lambda:options_quality_payload(ticker,dte_max=dte_max),ttl=600)
        best=post_earnings_otm_contract(payload,direction,expected,min_dte=min_dte,ideal_dte=ideal_dte)
        return jsonify({"ok":True,"ticker":ticker,"best_contract":best,
                        "options_execution":best.get("execution_quality") if best else "No executable OTM contract",
                        "min_dte":min_dte,"ideal_dte":ideal_dte,
                        "stale":stale,"refresh_error":err})
    except Exception as e:
        return jsonify({"ok":False,"ticker":ticker,"error":str(e)}),500


@app.get("/api/earnings-history/<ticker>")
def api_earnings_history(ticker):
    """
    Lazy-load one ticker's historical earnings-move profile.
    Returns explicit diagnostics instead of silently returning profile:null.
    """
    ticker = ticker.upper().strip()
    try:
        event_date = request.args.get("event_date")
        cache_key = f"earnings-profile-v18:{ticker}:{event_date or 'latest'}"

        def build():
            dates = merged_historical_earnings_dates(ticker, event_date)
            profile = earnings_profile(ticker, dates)
            return {
                "profile": profile,
                "dates_found": len(dates),
                "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates[:12]],
            }

        payload = cached(cache_key, build, ttl=3600)
        profile = payload.get("profile")
        if profile is None:
            return jsonify({
                "ok":False,
                "ticker":ticker,
                "error":f"Not enough completed historical earnings events were available to build a profile for {ticker}.",
                "dates_found":payload.get("dates_found",0),
                "dates":payload.get("dates",[])
            }),422

        return jsonify({
            "ok":True,
            "ticker":ticker,
            "profile":profile,
            "dates_found":payload.get("dates_found",0),
            "dates":payload.get("dates",[])
        })

    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500



def dark_pool_spike_context(ticker, spike_date_str):
    """Deterministic context for a flagged large-print day — no LLM, no web
    search. Everything here is derived from data this app already computes
    elsewhere (earnings calendar merge, macro calendar), covering the same
    *scheduled/known-facts* ground a narrative tool would report, without any
    speculative synthesis that would require an LLM."""
    spike_date = pd.Timestamp(spike_date_str)
    lines = []
    try:
        known_dates = sorted(merged_historical_earnings_dates(ticker), reverse=True)
        known_ts = [pd.Timestamp(d) for d in known_dates]
        prior = [d for d in known_ts if d <= spike_date]
        upcoming = [d for d in known_ts if d > spike_date]
        if prior:
            days_since = (spike_date - prior[0]).days
            lines.append(f"{days_since}d after its most recent known earnings report ({prior[0].strftime('%Y-%m-%d')})")
        if upcoming:
            days_until = (upcoming[-1] - spike_date).days
            lines.append(f"{days_until}d before its next known earnings report ({upcoming[-1].strftime('%Y-%m-%d')})")
    except Exception:
        pass
    try:
        nearby_macro = [ev for ev in MACRO_CALENDAR if abs((pd.Timestamp(ev["date"]) - spike_date).days) <= 5]
        if nearby_macro:
            lines.append("Nearby macro events: " + ", ".join(f"{ev['label']} ({ev['date']})" for ev in nearby_macro))
    except Exception:
        pass
    if not lines:
        lines.append("No known earnings or macro calendar events within the surrounding window.")
    return lines

def _institutional_trade_sample(ticker):
    """Sample recent SIP prints without pretending sampled tape is complete dark-pool data."""
    ticker=str(ticker or "").upper().strip()
    if not ticker or not ALPACA_API_KEY or not ALPACA_API_SECRET:
        return {"ticker":ticker,"ok":False,"error":"Alpaca SIP is not configured."}
    from zoneinfo import ZoneInfo
    et=ZoneInfo("America/New_York"); now=datetime.now(et)
    days=[]; d=now.date()
    while len(days)<4:
        if d.weekday()<5: days.append(d)
        d-=timedelta(days=1)
    samples=[]
    for day in days:
        start=datetime.combine(day,datetime.min.time(),tzinfo=et).replace(hour=9,minute=30)
        end=datetime.combine(day,datetime.min.time(),tzinfo=et).replace(hour=16)
        if day==now.date(): end=min(end,now)
        if end<=start: continue
        url=f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/trades"
        params={"start":start.isoformat(),"end":end.isoformat(),"feed":ALPACA_STOCK_FEED,
                "sort":"desc","limit":5000}
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=18)
        if r.status_code in (401,403):
            return {"ticker":ticker,"ok":False,"error":f"Alpaca {ALPACA_STOCK_FEED.upper()} trade access rejected ({r.status_code})."}
        r.raise_for_status(); rows=(r.json() or {}).get("trades") or []
        prints=[]
        for x in rows:
            try:
                price=float(x.get("p") or 0); size=float(x.get("s") or 0); notional=price*size
                if price>0 and size>0: prints.append((notional,x))
            except Exception: pass
        prints.sort(key=lambda z:z[0],reverse=True)
        largest=prints[0][0] if prints else 0
        samples.append({"date":str(day),"largest":largest,"count":len(rows),"top":prints[:8]})
    if not samples or not samples[0]["count"]:
        return {"ticker":ticker,"ok":False,"error":"No recent trade sample returned."}
    cur=samples[0]; prior=[x["largest"] for x in samples[1:] if x["largest"]>0]
    baseline=float(np.median(prior)) if prior else cur["largest"]
    multiple=(cur["largest"]/baseline) if baseline>0 else 1.0
    threshold=max(100000.0,baseline*.35)
    large=[z for z in cur["top"] if z[0]>=threshold]
    large_notional=sum(z[0] for z in large)
    repeated=sum(1 for x in samples if x["largest"]>=max(100000.0,baseline*.8))
    top=[]
    for n,x in cur["top"][:5]:
        top.append({"notional":round(n,2),"price":_safe_float(x.get("p")),"size":int(x.get("s") or 0),
                    "exchange":x.get("x"),"conditions":x.get("c") or [],"time":x.get("t")})
    activity=min(10.0,2.5+min(3.5,max(0,multiple-1)*2.0)+min(2.0,repeated*.55)+min(2.0,large_notional/max(1,baseline)*.55))
    context=None
    if multiple>=2.0:
        try:
            context=dark_pool_spike_context(ticker,cur["date"])
        except Exception:
            context=None
    return {"ticker":ticker,"ok":True,"date":cur["date"],"largest_print":round(cur["largest"],2),"baseline_largest":round(baseline,2),
            "context":context,
            "largest_multiple":round(multiple,2),"large_print_notional":round(large_notional,2),"repeat_days":repeated,
            "sampled_trades":cur["count"],"activity_score":round(activity,1),"top_prints":top,
            "source":f"Alpaca {ALPACA_STOCK_FEED.upper()} sampled trades","sampled":True}

@app.post("/api/institutional-radar")
def institutional_radar():
    try:
        if request.method=="GET":
            raw=[x for x in str(request.args.get("symbols") or "").split(",") if x]
        else:
            body=request.get_json(silent=True) or {}; raw=body.get("symbols") or []
        meta=body.get("meta") or {}
        symbols=[]
        for x in raw:
            t=str(x or "").upper().strip()
            if t and t not in symbols and len(t)<=20: symbols.append(t)
        symbols=symbols[:12]
        if not symbols:return jsonify({"ok":False,"error":"No symbols supplied."}),400
        def one(t):
            return cached(f"institutional-radar-v25-24:{ALPACA_STOCK_FEED}:{t}",lambda:_institutional_trade_sample(t),ttl=600)
        rows=[]
        with ThreadPoolExecutor(max_workers=min(4,len(symbols))) as ex:
            futs={ex.submit(one,t):t for t in symbols}
            for f in as_completed(futs):
                t=futs[f]
                try:r=f.result()
                except Exception as e:r={"ticker":t,"ok":False,"error":str(e)}
                m=meta.get(t) or {}
                if r.get("ok"):
                    rotation=float(m.get("opportunity") or 0); stage=float(m.get("stage") or 0)
                    composite=min(10.0,.72*float(r.get("activity_score") or 0)+.20*rotation+.08*(stage/4*10))
                    r["rotation"]=m; r["composite_score"]=round(composite,1)
                rows.append(r)
        rows.sort(key=lambda x:(x.get("ok") is True,float(x.get("composite_score") or 0)),reverse=True)
        return jsonify({"ok":True,"results":rows,"feed":ALPACA_STOCK_FEED,"disclosure":"Large-print activity uses a bounded sample of Alpaca equity trades. It is not a complete tape and is not labeled dark-pool flow; exchange/condition codes are preserved when available."})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


def _news_category(headline, summary=""):
    text=(str(headline or "")+" "+str(summary or "")).lower()
    tests=[
      ("earnings",("earnings","revenue","eps","guidance","quarter")),
      ("analyst",("upgrade","downgrade","price target","initiated","rating","analyst")),
      ("m&a",("acquire","acquisition","merger","takeover","buyout")),
      ("product",("launch","product","approval","fda","contract","partnership","deal")),
      ("legal/regulatory",("lawsuit","probe","investigation","sec ","doj","antitrust","regulator","recall")),
      ("macro",("fed ","federal reserve","rates","inflation","jobs","tariff","treasury","oil","china")),
    ]
    for cat,words in tests:
        if any(w in text for w in words): return cat
    return "company"

def _why_news_matters(category):
    return {
      "earnings":"Can reset near-term estimates, implied volatility, and the market's accepted valuation range.",
      "analyst":"Can change positioning and near-term expectations, especially when several firms revise targets together.",
      "m&a":"Can create abrupt repricing and alter sector sympathy/relative-strength behavior.",
      "product":"May change forward growth expectations or create a discrete catalyst that confirms or invalidates the setup.",
      "legal/regulatory":"Can introduce asymmetric headline risk that may overwhelm otherwise-clean technical signals.",
      "macro":"May affect the entire sector through rates, risk appetite, commodity inputs, or policy sensitivity.",
      "company":"Company-specific information that may explain unusual price/volume behavior and should be checked against the technical setup.",
    }.get(category,"Relevant context for the current setup.")

def _finnhub_company_news(ticker, days=4):
    if not FINNHUB_API_KEY:return []
    end=pd.Timestamp.now().normalize(); start=end-pd.Timedelta(days=days)
    url="https://finnhub.io/api/v1/company-news"
    try:
        r=requests.get(url,params={"symbol":ticker,"from":start.strftime("%Y-%m-%d"),"to":end.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY},timeout=15,headers={"User-Agent":"MarketRotationScreener/1.0"})
        r.raise_for_status(); rows=r.json() or []; out=[]
        for x in rows[:8]:
            h=str(x.get("headline") or "").strip()
            if not h:continue
            cat=_news_category(h,x.get("summary"))
            out.append({"ticker":ticker,"headline":h,"summary":str(x.get("summary") or "").strip(),"source":x.get("source") or "Finnhub","url":x.get("url"),"datetime":x.get("datetime"),"category":cat,"why":_why_news_matters(cat)})
        return out
    except Exception:return []

def _finnhub_market_news():
    if not FINNHUB_API_KEY:return []
    try:
        r=requests.get("https://finnhub.io/api/v1/news",params={"category":"general","token":FINNHUB_API_KEY},timeout=15,headers={"User-Agent":"MarketRotationScreener/1.0"})
        r.raise_for_status(); rows=r.json() or []; out=[]
        for x in rows[:8]:
            h=str(x.get("headline") or "").strip()
            if not h:continue
            out.append({"headline":h,"source":x.get("source") or "Finnhub","url":x.get("url"),"datetime":x.get("datetime")})
        return out
    except Exception:return []

@app.route("/api/news-context", methods=["GET","POST"])
def api_news_context():
    try:
        body=request.get_json(silent=True) or {}; raw=body.get("symbols") or []
        symbols=[]
        for x in raw:
            t=str(x or "").upper().strip()
            if t and t not in symbols and len(t)<=20:symbols.append(t)
        symbols=symbols[:8]
        market=cached("news-market-v25-29",_finnhub_market_news,ttl=600)
        company={}
        with ThreadPoolExecutor(max_workers=min(4,max(1,len(symbols)))) as ex:
            futs={ex.submit(lambda t=t:cached(f"news-company-v25-29:{t}",lambda:_finnhub_company_news(t),ttl=600)):t for t in symbols}
            for f,t in [(f,t) for f,t in futs.items()]:
                try:company[t]=f.result()
                except Exception:company[t]=[]
        return jsonify({"ok":True,"market":market,"company":company,"symbols":symbols,"source":"Finnhub public news endpoints","deterministic":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "alpaca_api_configured": bool(ALPACA_API_KEY and ALPACA_API_SECRET),
        "finnhub_api_configured": bool(FINNHUB_API_KEY),
        "unusual_whales_api_configured": bool(UW_API_TOKEN),
        "setup_history_storage": _setup_storage_backend(),
        "persistent_setup_history": bool(DATABASE_URL),
    })


@app.get("/api/diagnostics")
def api_diagnostics():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "alpaca": {
            "configured": bool(ALPACA_API_KEY and ALPACA_API_SECRET),
            "feed": ALPACA_OPTIONS_FEED,
            "dte": "0-30"
        },
        "setup_history": {
            "backend": _setup_storage_backend(),
            "persistent": bool(DATABASE_URL),
            "configured": bool(DATABASE_URL)
        },
        "startup_network_calls": False
    })


@app.get("/api/holdings-audit")
def holdings_audit():
    results = []
    for etf, name in RRG_UNIVERSE.items():
        try:
            bundle, stale, err = cached_refresh_safe(
                f"holdings:{etf}", lambda etf=etf:get_fund_holdings(etf), force=False, ttl=3600
            )
            holdings, source = bundle
            results.append({
                "etf":etf,"name":name,"count":len(holdings),"source":source,
                "ok":True,"partial":("TOP holdings fallback" in source),"stale":stale,"error":err
            })
        except Exception as e:
            results.append({"etf":etf,"name":name,"count":0,"source":"—","ok":False,"error":str(e)})
    return jsonify({"ok":True,"results":results})

@app.get("/api/source-status")
def source_status():
    return jsonify({
        "ok": True,
        "finnhub_api_configured": bool(FINNHUB_API_KEY),
        "unusual_whales_api_configured": bool(UW_API_TOKEN),
        "alpaca_api_configured": bool(ALPACA_API_KEY and ALPACA_API_SECRET),
        "earnings_priority": ["Finnhub earnings calendar","Unusual Whales API (optional)","Yahoo earnings calendar","yfinance ticker history"]
    })

HTML = r"""
<style>
:root{--bg:#0b0e11;--panel:#12161b;--line:#27303a;--text:#e5e7eb;--muted:#8b95a5;--green:#22c55e;--blue:#38bdf8;--red:#ef4444;--amber:#f59e0b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1320px;margin:auto;padding:20px}
h1{margin:0;font-size:22px}.sub{color:var(--muted);margin:5px 0 16px}.tabs{display:flex;gap:8px;margin:14px 0}.tab{padding:10px 14px;border:1px solid #334155;background:#17202b;color:var(--text);border-radius:9px;cursor:pointer}.tab.active{background:#1d4ed8;border-color:#2563eb}.view{display:none}.view.active{display:block}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}button,select{background:#17202b;color:var(--text);border:1px solid #334155;border-radius:8px;padding:9px 12px}button{cursor:pointer}.primary{background:#1d4ed8}.status{color:var(--muted);margin-left:auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:14px}.card{background:#0e1319;border:1px solid #202936;border-radius:10px;padding:12px}.card b{font-size:19px}.tiny{font-size:11px;color:var(--muted);margin-top:4px}.note{font-size:12px;color:var(--muted)}
.grid2{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(340px,.8fr);gap:14px}@media(max-width:900px){.grid2{grid-template-columns:1fr}}
canvas{width:100%;height:540px;background:#0d1217;border-radius:10px;border:1px solid #202936}.badge{display:inline-block;font-weight:800;font-size:10px;padding:4px 7px;border-radius:999px;color:#071013}.Leading{background:var(--green)}.Improving{background:var(--blue)}.Lagging{background:var(--red);color:white}.Weakening{background:var(--amber)}.mover{font-weight:800}.mHIGH{color:#fb7185}.mMODERATE{color:#fbbf24}.mLOW{color:#cbd5e1}
table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #202936;padding:9px 7px;vertical-align:top}th{font-size:10px;color:var(--muted);text-transform:uppercase;position:sticky;top:0;background:var(--panel)}.scroll{max-height:590px;overflow:auto}.clickrow{cursor:pointer}.up{color:#4ade80}.down{color:#fb7185}.flag{font-size:10px;background:#312e81;color:#c4b5fd;padding:3px 6px;border-radius:6px}.details{display:none;background:#0d1217}.details.open{display:table-row}.details td{padding:14px}.detailgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}.metric{border:1px solid #202936;border-radius:8px;padding:9px}.metric b{font-size:16px}.eventtable td,.eventtable th{padding:5px}.error{color:#fca5a5}


@media(max-width:700px){
  .wrap{padding:12px;padding-left:max(12px,env(safe-area-inset-left));padding-right:max(12px,env(safe-area-inset-right));padding-bottom:max(18px,env(safe-area-inset-bottom))}
  h1{font-size:20px}
  .sub{font-size:12px;line-height:1.4}
  .tabs{position:sticky;top:0;z-index:20;background:var(--bg);padding:8px 0;margin:8px 0}
  .tab{flex:1;min-height:44px;font-size:13px}
  .panel{padding:12px;border-radius:10px;margin:10px 0}
  .row{align-items:stretch}
  .row>button,.row>select{min-height:44px}
  .row>.status{width:100%;margin-left:0;font-size:11px}
  .cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
  .card{padding:10px}.card b{font-size:17px}
  canvas{height:380px}
  .scroll{max-height:none;overflow-x:auto;-webkit-overflow-scrolling:touch}
  table{min-width:720px}
  th,td{padding:8px 6px}
  #earnings .panel>table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch;min-width:0}
  button{touch-action:manipulation}
}



@media(max-width:700px){
  .wrap{padding:12px;padding-left:max(12px,env(safe-area-inset-left));padding-right:max(12px,env(safe-area-inset-right));padding-bottom:max(18px,env(safe-area-inset-bottom))}
  h1{font-size:20px}
  .sub{font-size:12px;line-height:1.4}
  .tabs{position:sticky;top:0;z-index:20;background:var(--bg);padding:8px 0;margin:8px 0}
  .tab{flex:1;min-height:44px;font-size:13px}
  .panel{padding:12px;border-radius:10px;margin:10px 0}
  .row{align-items:stretch}
  .row>button,.row>select{min-height:44px}
  .row>.status{width:100%;margin-left:0;font-size:11px}
  .cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
  .card{padding:10px}.card b{font-size:17px}
  canvas{height:380px}
  .scroll{max-height:none;overflow-x:auto;-webkit-overflow-scrolling:touch}
  table{min-width:720px}
  th,td{padding:8px 6px}
  #earnings .panel>table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch;min-width:0}
  button{touch-action:manipulation}
}


.optBadge{display:inline-block;border:1px solid #334155;border-radius:999px;padding:2px 7px;font-size:11px;white-space:nowrap}
.optGood{color:#86efac}.optWarn{color:#fde68a}.optBad{color:#fca5a5}
.optionsBtn{padding:5px 8px;font-size:11px}
.peScore{font-size:17px;font-weight:900;color:#75e7ad}.peContract{min-width:190px}.peContract b{color:#dbeafe}
.execWide{color:#fde68a}.execGood{color:#86efac}.histRunner{display:inline-block;margin-top:4px;border:1px solid #7c3aed;color:#c4b5fd;border-radius:999px;padding:2px 6px;font-size:9px;font-weight:800}
.reversionFlag{display:inline-block;margin-top:4px;border:1px solid #b45309;color:#fbbf24;border-radius:999px;padding:2px 6px;font-size:9px;font-weight:800}
.givebackFlag{display:inline-block;margin-top:4px;border:1px solid #b91c1c;color:#fca5a5;border-radius:999px;padding:2px 6px;font-size:9px;font-weight:800}


.setupBtn{display:inline-block;background:#1d4ed8;color:#fff;text-decoration:none;border:1px solid #2563eb;border-radius:8px;padding:9px 12px;font-weight:700}
.setupBtn:hover{filter:brightness(1.08)}
.setupBox{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0;padding:10px 12px;border:1px solid #334155;border-radius:9px;background:#0f141a}
.setupBox.ready{border-color:#166534}
.setupBox code{color:#bfdbfe}

.contractPrimary{font-size:13px;white-space:nowrap}
.occSymbol{font-size:10px;color:#64748b;margin-top:2px;word-break:break-all}
#optionsRows td:nth-child(3){font-size:14px}
@media(max-width:700px){
  .contractPrimary{white-space:normal;line-height:1.25}
  #optionsRows td{min-width:72px}
  #optionsRows td:first-child{min-width:165px}
}


.macroDim{opacity:.16}
.rotationStage{font-size:11px;font-weight:700;white-space:nowrap}
.stage1{color:#fca5a5}.stage2{color:#fde68a}.stage3{color:#7dd3fc}.stage4{color:#86efac}
.previewPeriodBtn.active{border-color:#60a5fa;color:#bfdbfe;background:#172033}

.priceChartPanel{background:linear-gradient(180deg,#0b131b,#091018);border-color:#223244;overflow:hidden}
.priceChartHeader{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:8px;padding:2px 2px 0}
.priceChartTitle{display:flex;align-items:baseline;gap:10px;font-size:15px;letter-spacing:.15px}
.priceChartLast{font-size:13px;font-weight:800;color:#dce6f0}
.priceChartMeta{margin-top:3px;font-size:10px;color:#7f8c9d;letter-spacing:.3px}
.priceChartControls{display:flex;gap:6px;align-items:center}
.priceChartControlStack{display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.vpControls{display:flex;gap:5px;align-items:center;justify-content:flex-end;flex-wrap:wrap}
.tfControls{display:flex;gap:5px;align-items:center;justify-content:flex-end;flex-wrap:wrap}
.tfBtn{padding:5px 8px;border-radius:6px;background:#0b141d;border:1px solid #28394a;color:#8ea0b3;font-size:9px;font-weight:800}
.tfBtn.active{background:#183227;border-color:#2f7d57;color:#d1fae5}

.vpLabel{font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:#718196;margin-right:3px}
.vpModeBtn{padding:5px 8px;border-radius:6px;background:#0b141d;border:1px solid #28394a;color:#8ea0b3;font-size:9px;font-weight:800}
.vpModeBtn.active{background:#13283a;border-color:#3b82f6;color:#dbeafe}
@media(max-width:900px){.priceChartControlStack{align-items:stretch}.vpControls{justify-content:flex-start}}

.priceChartControls .previewPeriodBtn{min-width:44px;padding:6px 10px;border-radius:7px;background:#0c151e;border-color:#2a3a4b;color:#9eacbb;font-size:10px;font-weight:800}
.priceChartControls .previewPeriodBtn.active{background:#0f2740;border-color:#2563eb;color:#dbeafe;box-shadow:inset 0 0 0 1px rgba(59,130,246,.15)}
.priceChartCanvasWrap{border:1px solid #203142;border-radius:10px;background:linear-gradient(180deg,#081017,#070d13);overflow:hidden}

#pricePreviewChart{width:100%;height:auto!important;aspect-ratio:1180/680;display:block;background:transparent}
.vpLevelStrip{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:7px 0 6px}
.vpLevelItem{display:flex;gap:6px;align-items:center;background:#0a131c;border:1px solid #1f3040;border-radius:7px;padding:5px 9px;font-size:9px;color:#9fb0c2}
.vpLevelItem strong{font-size:10px;color:#eef5fb}
.vpSwatch{width:22px;height:2px;display:inline-block;border-radius:2px}
.vpSwatch.vah{background:#a78bfa}.vpSwatch.poc{background:#f59e0b}.vpSwatch.val{background:#60a5fa}
.chartStatsStrip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:8px;border:1px solid #203241;border-radius:9px;overflow:hidden;background:#09131c}
.chartStatsStrip>div{padding:9px 11px;border-right:1px solid #203241}
.chartStatsStrip>div:last-child{border-right:none}
.chartStatsStrip span{display:block;font-size:7.5px;letter-spacing:.7px;color:#718397;margin-bottom:4px}
.chartStatsStrip strong{display:block;font-size:11px;color:#edf4f9}
.chartStatsStrip small{display:block;font-size:8px;color:#718397;margin-top:2px}
@media(max-width:900px){.chartStatsStrip{grid-template-columns:repeat(2,minmax(0,1fr))}}

.priceChartFooter{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-top:7px;color:#8290a1}
@media(max-width:700px){.priceChartHeader{align-items:flex-start;flex-direction:column}.priceChartControls{width:100%}.priceChartControls .previewPeriodBtn{flex:1}#pricePreviewChart{height:300px}.priceChartFooter{align-items:flex-start;flex-direction:column}}

.heatGrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:10px;margin-top:12px}
.heatTile{border:1px solid #273244;border-radius:12px;padding:12px;cursor:pointer;min-height:118px;background:#111821;transition:transform .12s ease,border-color .12s ease,background .12s ease}
.heatTile:hover{transform:translateY(-1px);border-color:#64748b}.heatTile.selected{outline:2px solid #60a5fa;border-color:#60a5fa}
.heatTile.h0,.heatTile.h1{background:#35191d;border-color:#5b252c}.heatTile.h2,.heatTile.h3{background:#2b1d22;border-color:#493039}.heatTile.h4,.heatTile.h5{background:#1b2430}.heatTile.h6,.heatTile.h7{background:#172b2d}.heatTile.h8,.heatTile.h9,.heatTile.h10{background:#13352d}
.heatHead{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.heatTicker{font-size:18px;font-weight:800}.heatScore{font-size:17px;font-weight:800}
.heatMeta{font-size:11px;color:var(--muted);margin-top:6px;line-height:1.45}.heatTags{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}.heatTag{font-size:10px;border:1px solid #334155;border-radius:999px;padding:2px 6px;color:#cbd5e1}.heatLegend{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:8px}
@media(max-width:700px){.heatGrid{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.heatTile{min-height:108px;padding:9px}.heatTicker{font-size:16px}}
#gammaLandscape{width:100%;height:430px;display:block;background:#0d1217;border:1px solid #273244;border-radius:12px;margin-top:10px;cursor:crosshair}
.chainFreshness{margin-left:auto;font-size:12px;color:#94a3b8;white-space:nowrap}.chainFreshness.fresh{color:#86efac}.chainFreshness.aging{color:#fbbf24}.chainFreshness.stale{color:#f87171}@media(max-width:780px){.chainFreshness{width:100%;margin-left:0;margin-top:3px}}
.gammaLegend{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:7px}.gammaLegend b{color:var(--text)}
.gammaLevelDetail{min-height:22px;margin-top:7px;color:#cbd5e1}


/* v22.1 GEX Landscape redesign */
.gexDashboard{border-top:1px solid #1d5e37;padding-top:12px}
.gexTopline{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:10px}.modeledTag{font-size:9px;letter-spacing:.9px;color:#94a3b8;border:1px solid #334155;border-radius:5px;padding:2px 5px;margin-left:4px;vertical-align:2px}
.positioningGrid.gammaSummary{display:grid;grid-template-columns:repeat(6,minmax(135px,1fr));gap:10px;margin:8px 0 12px}
.gexDashboard .metricCard{min-height:88px;padding:12px 13px;border-radius:11px;background:linear-gradient(180deg,#101923,#0c131a);box-shadow:inset 0 1px rgba(255,255,255,.02)}
.gexDashboard .metricCard .tiny:first-child{font-size:10px;font-weight:800;letter-spacing:.5px;color:#cbd5e1}.gexDashboard .metricCard .subLabel{font-size:10px;color:#7f8c9d;margin-top:2px}.gexDashboard .metricCard .big{font-size:21px;margin-top:7px}
.metricCard.netGex{border-color:#1f5b39}.metricCard.netGex .big{color:#4ade80}.metricCard.netGex.negative{border-color:#71313a}.metricCard.netGex.negative .big{color:#f87171}
.metricCard.exposureCard{grid-column:span 1}.exposureMini{font-size:10px;line-height:1.55;margin-top:5px}.exposureMini .pos{color:#4ade80}.exposureMini .neg{color:#f87171}
.gexWorkspace{display:grid;grid-template-columns:minmax(0,1fr) 285px;gap:12px;align-items:start}.gexMain{min-width:0}.gexSectionHead{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:2px 0 7px}.gexSectionHead strong{text-transform:uppercase;letter-spacing:.35px}
#gammaLandscape{height:560px;background:linear-gradient(180deg,#0b1218,#091016);border-color:#223245;border-radius:11px;margin-top:6px}
.gammaLegendTop{margin:4px 0 8px;font-size:10px}.gammaLegendTop .callDot{color:#4ade80}.gammaLegendTop .putDot{color:#f87171}.gammaLegendTop .flipDot{color:#c084fc}.gammaLegendTop .callRailDot{color:#4ade80}.gammaLegendTop .putRailDot{color:#f59e0b}
.gammaSelectedDetail{border:1px solid #25364a;background:#0d151e;border-radius:8px;padding:9px 11px;margin-top:8px;color:#cbd5e1;font-size:11px;min-height:18px}.gammaSelectedDetail .positive{color:#4ade80}.gammaSelectedDetail .negative{color:#f87171}.gammaSelectedDetail span{margin-left:14px}.gexDisclosure{margin-top:8px;padding:8px 10px;border-left:2px solid #334155;background:#0b1218;border-radius:6px;color:#8492a5}
.gexRail{display:grid;gap:10px}.gexRailCard{background:linear-gradient(180deg,#101923,#0c131a);border:1px solid #25364a;border-radius:10px;padding:12px}.gexRailTitle{font-size:10px;font-weight:900;letter-spacing:.55px;color:#e2e8f0;margin-bottom:9px}.gexStatRow,.gexLevelRow,.gexLargestRow,.gexLegendRow{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;font-size:11px;padding:4px 0}.gexStatRow .positive,.gexLargestRow .positive{color:#4ade80}.gexStatRow .negative,.gexLargestRow .negative{color:#f87171}.gexLevelRow{grid-template-columns:12px 1fr auto}.gexSwatch{width:8px;height:8px;border-radius:50%;display:inline-block}.gexSwatch.call{border:2px solid #22c55e;background:transparent}.gexSwatch.flip{height:0;width:10px;border-radius:0;border-top:2px dashed #a78bfa}.gexSwatch.put{height:0;width:10px;border-radius:0;border-top:2px dashed #f59e0b}.gexSwatch.spot{border:1px solid #f8fafc;background:transparent}.gexLargestRow{grid-template-columns:36px 1fr auto}.gexMiniBar{height:8px;background:#17202a;border-radius:4px;overflow:hidden}.gexMiniBar span{display:block;height:100%;border-radius:4px}.gexMiniBar .positive{background:linear-gradient(90deg,#166534,#4ade80)}.gexMiniBar .negative{background:linear-gradient(90deg,#7f1d1d,#ef4444)}.gexLegendRow{grid-template-columns:18px 1fr}.gexLegendLine{height:0;width:16px;border-top:2px solid #f8fafc}.gexLegendLine.flip{border-top-style:dashed;border-color:#a78bfa}.gexLegendLine.call{border-top-style:dashed;border-color:#22c55e}.gexLegendLine.put{border-top-style:dashed;border-color:#f59e0b}.gexLegendBox{height:9px;width:16px;border-radius:2px;background:linear-gradient(90deg,#166534,#22c55e)}.gexLegendBox.put{background:linear-gradient(90deg,#7f1d1d,#ef4444)}
@media(max-width:1150px){.positioningGrid.gammaSummary{grid-template-columns:repeat(3,minmax(135px,1fr))}.gexWorkspace{grid-template-columns:1fr}.gexRail{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:700px){.positioningGrid.gammaSummary{grid-template-columns:repeat(2,minmax(120px,1fr))}.gexRail{grid-template-columns:1fr}.gexTopline{flex-direction:column}.chainFreshness{margin-left:0}.gexDashboard .metricCard{min-height:78px}#gammaLandscape{height:470px}}

/* v22 dashboard redesign */
:root{--nav:#081016;--panel2:#0d151d;--panel3:#101a24;--line2:#223244;--accent:#22c55e;--accent2:#3b82f6;--cyan:#38bdf8}
body{background:radial-gradient(circle at 50% -20%,#12202a 0,#0b0e11 38%,#080b0f 100%);min-height:100vh}
.wrap{max-width:1600px;padding:0 16px 24px}
.appHeader{position:sticky;top:0;z-index:50;margin:0 -16px 16px;padding:10px 16px;background:rgba(6,11,16,.96);backdrop-filter:blur(12px);border-bottom:1px solid #1e2c3a;display:flex;align-items:center;gap:18px}
.brand{display:flex;align-items:center;gap:10px;min-width:270px}.brandMark{width:38px;height:38px;border:2px solid #22c55e;border-radius:50%;display:grid;place-items:center;color:#38bdf8;font-weight:900;font-size:20px;box-shadow:0 0 18px rgba(34,197,94,.12)}
.brandText b{display:block;font-size:15px;letter-spacing:.4px}.brandText span{font-size:10px;color:#22c55e;letter-spacing:1.2px}
.tabs{margin:0;gap:2px;flex:1}.tab{background:transparent;border:0;border-radius:8px;padding:10px 12px;color:#a8b3c2;font-size:12px}.tab:hover{background:#101820;color:#e8edf4}.tab.active{background:#102319;color:#59e783;box-shadow:inset 0 -2px #22c55e}
.headerMeta{display:flex;gap:10px;align-items:center}.versionPill{font-size:11px;color:#4ade80;border:1px solid #1d6b3a;background:#0d2317;padding:6px 9px;border-radius:7px}
.pageIntro{display:none}
.panel{background:linear-gradient(180deg,rgba(16,24,33,.98),rgba(12,18,25,.98));border-color:#213043;box-shadow:0 8px 24px rgba(0,0,0,.12)}
.dashboardGrid{display:grid;grid-template-columns:minmax(260px,.78fr) minmax(560px,1.75fr) minmax(250px,.72fr);gap:12px;align-items:start}
.dashCol{min-width:0}.topSetupsPanel{margin-bottom:12px;border:1px solid #29415a}.topSetupsGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.topSetupCard{border:1px solid #26384a;border-radius:9px;padding:10px;background:#0a121a;cursor:pointer}.topSetupCard.aPlus{border-color:#2d7b59;background:linear-gradient(145deg,rgba(20,76,56,.22),#0a121a 55%)}.topSetupHead{display:flex;justify-content:space-between}.topSetupTicker{font-size:17px;font-weight:900}.topSetupScore{font-size:15px;font-weight:900;color:#75e7ad}.topSetupStatus{font-size:8px;font-weight:900;color:#f7c65d}.topSetupReasons{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}.topSetupReasons span{font-size:8px;border:1px solid #26384a;border-radius:999px;padding:3px 5px}.topSetupReasons .good{border-color:#25694c;color:#71dfaa}.topSetupReasons .warn{border-color:#805d25;color:#f4c363}.topSetupTrigger{margin-top:8px;padding-top:7px;border-top:1px solid #1c2b37;font-size:9px}
.topSetupActions{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
.topSetupAction{border:1px solid #2a4054;background:#0c1721;color:#cbd8e5;border-radius:6px;padding:5px 8px;font-size:8px;font-weight:800;cursor:pointer}
.topSetupAction:hover{border-color:#4e7da3;color:#fff;background:#102131}
.topSetupAction.primaryDive{border-color:#2d7b59;color:#79e4ad;background:#0d2119}
.topSetupAction.gexDive{border-color:#65458e;color:#c6a8ff;background:#181022}
.topSetupAction.optionsDive{border-color:#315c87;color:#83c5ff;background:#0d1926}
.topSetupsEmpty{grid-column:1/-1;padding:13px;border:1px dashed #2b3b4b;border-radius:8px;color:#8092a4;font-size:10px}
.nearestMisses{grid-column:1/-1}
.nearestMissRow{padding:6px 0;border-bottom:1px solid #1b2835;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px}
.nearestMissRow b{font-size:11px}
@media(max-width:900px){.topSetupsGrid{grid-template-columns:1fr}}.dashCol .panel{margin:0 0 12px}.dashTitle{font-size:13px;font-weight:800;letter-spacing:.25px}.dashTopline{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}.dashTopline .note{font-size:10px}
.marketOverviewGrid{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border:1px solid #1b2a38;border-radius:9px;overflow:hidden}.marketQuote{padding:10px 8px;border-right:1px solid #1b2a38;background:#0b1219}.marketQuote:last-child{border-right:0}.marketQuote .sym{font-size:11px;color:#c9d3df}.marketQuote .px{font-size:17px;font-weight:750;margin-top:3px}.marketQuote .chg{font-size:11px;margin-top:2px}
.dashHeatGrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}.dashHeatTile{min-height:90px;border-radius:7px;padding:8px;border:1px solid #263548;cursor:pointer;display:flex;flex-direction:column;justify-content:space-between;transition:.15s}.dashHeatTile:hover{transform:translateY(-1px);filter:brightness(1.08)}.dashHeatTile.selected{outline:2px solid #60a5fa}.dashHeatTile.h0,.dashHeatTile.h1{background:linear-gradient(145deg,#5b171a,#b3262c)}.dashHeatTile.h2,.dashHeatTile.h3{background:linear-gradient(145deg,#3f1b1f,#71252a)}.dashHeatTile.h4,.dashHeatTile.h5{background:linear-gradient(145deg,#1a242f,#253444)}.dashHeatTile.h6,.dashHeatTile.h7{background:linear-gradient(145deg,#153029,#1d4d3c)}.dashHeatTile.h8,.dashHeatTile.h9,.dashHeatTile.h10{background:linear-gradient(145deg,#114029,#18733d)}
.dashHeatTile .sym{font-weight:850;font-size:13px}.dashHeatTile .score{font-size:19px;font-weight:850}.dashHeatTile .state{font-size:9px;text-transform:uppercase;letter-spacing:.5px;color:#d8e1ea}.miniSpark{height:17px;width:100%;opacity:.75}
.heatScale{height:7px;border-radius:999px;background:linear-gradient(90deg,#8e2025 0,#3a2a2c 34%,#253443 50%,#1d5c3f 70%,#1aa04f 100%);margin-top:9px}.heatScaleLabels{display:flex;justify-content:space-between;font-size:9px;color:#8090a2;margin-top:4px}
.breadthList{display:grid;gap:0}.breadthRow{display:grid;grid-template-columns:1fr auto auto;gap:8px;padding:9px 0;border-bottom:1px solid #1b2835;align-items:center}.breadthRow:last-child{border-bottom:0}.breadthRow .name{font-size:11px}.breadthRow .val{font-weight:700}.breadthRow .move{font-size:10px}
.rrgShell .panel{margin:0}.rrgHeader{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px}.rrgHeader h2{margin:0;font-size:14px}.rrgToggle{display:inline-flex;border:1px solid #2d4053;border-radius:7px;overflow:hidden;background:#0a1118}.rrgToggle button{border:0;border-radius:0;background:transparent;padding:7px 13px;font-size:10px}.rrgToggle button.active{background:#1d5bd8;color:white}
#sectorChart{height:500px;border-radius:8px}.selectedSectorCard{display:grid;grid-template-columns:1.15fr 1fr 1fr 1.2fr;gap:0;border:1px solid #1e3040;background:#0b131a;border-radius:9px;margin-top:8px;overflow:hidden}.selectedSectorCard>div{padding:9px 12px;border-right:1px solid #1e3040}.selectedSectorCard>div:last-child{border-right:0}.sscLabel{font-size:9px;color:#7f8ea1;text-transform:uppercase;letter-spacing:.5px}.sscValue{font-size:12px;font-weight:800;margin-top:3px}.sscInterp{font-size:10px;color:#a9b5c4;line-height:1.35;margin-top:3px}
.sectorSummaryPanel{margin-top:10px!important}.sectorSummaryPanel .scroll{max-height:300px}.sectorSummaryPanel table{font-size:11px}.sectorSummaryPanel th,.sectorSummaryPanel td{padding:7px 6px}
.sideSection{padding:12px;border:1px solid #213043;border-radius:9px;background:#0c131a;margin-bottom:10px}.sideSection h3{font-size:11px;margin:0 0 9px;letter-spacing:.4px}.sideSection select{width:100%;font-size:11px}.sideSeg{display:grid;grid-template-columns:1fr 1fr;gap:4px}.heatModeTabs{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.heatModeTabs button{font-size:9px;padding:7px 4px;background:#101923}.heatModeTabs button.active{background:#173b25;border-color:#22c55e;color:#7cf29b}.sideSeg button{font-size:10px;padding:8px 6px;background:#101923}.sideSeg button.active{background:#174fbd;border-color:#3b82f6}.filterPills{display:flex;gap:5px;flex-wrap:wrap}.filterPill{font-size:9px;padding:6px 8px}.filterPill.active{border-color:#3b82f6;background:#102a4d;color:#8fc5ff}.filterPill.leading.active{border-color:#22c55e;background:#0f301d;color:#6ee799}.filterPill.lagging.active{border-color:#ef4444;background:#381316;color:#ff7b7b}.filterPill.weakening.active{border-color:#f59e0b;background:#33260b;color:#ffc857}
.quickBtn{width:100%;margin-top:6px;background:#0e1720;border-color:#2a3948;text-align:left;font-size:10px}.quickBtn:hover{border-color:#4c647a}
.legacyMarketBlock{display:none}.legacySectorGrid{display:none}.rotationLower{margin-top:12px}.rotationLower>.panel:first-child{margin-top:0}
#dashboardSectorTable tbody tr.selected{background:#102439}.fastText{color:#50b8ff}.trendText{color:#7fe29a}.trendLag{color:#ff6b6b}.quadLeading{color:#4ade80}.quadImproving{color:#60a5fa}.quadWeakening{color:#fbbf24}.quadLagging{color:#f87171}
@media(max-width:1100px){.dashboardGrid{grid-template-columns:1fr 1.7fr}.dashRight{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.dashRight .sideSection{margin:0}.marketOverviewGrid{grid-template-columns:repeat(2,1fr)}.dashHeatGrid{grid-template-columns:repeat(4,1fr)}}
@media(max-width:760px){.appHeader{position:sticky;align-items:flex-start;flex-wrap:wrap}.brand{min-width:0}.brandText b{font-size:13px}.tabs{order:3;width:100%;overflow:auto;flex-wrap:nowrap}.tab{white-space:nowrap;min-width:auto}.headerMeta{margin-left:auto}.dashboardGrid{grid-template-columns:1fr}.dashRight{grid-column:auto;display:block}.dashHeatGrid{grid-template-columns:repeat(4,1fr)}#sectorChart{height:390px}.selectedSectorCard{grid-template-columns:1fr 1fr}.selectedSectorCard>div:nth-child(2){border-right:0}.selectedSectorCard>div:nth-child(-n+2){border-bottom:1px solid #1e3040}.rotationLower{margin-top:8px}}


/* v22.2 visual system overhaul — dashboard + GEX */
:root{
 --bg0:#060b10;--bg1:#08111a;--surface:#0c151e;--surface2:#101b26;--surface3:#132131;
 --line:#203246;--lineHi:#2b425a;--text:#f1f5f9;--muted:#8fa0b3;
 --green:#36e27a;--green2:#1fae59;--red:#ff4d55;--amber:#f7a51a;--purple:#b36cff;--blue:#2583ff;--cyan:#35c2ff;
}
*{box-sizing:border-box}
body{background:radial-gradient(circle at 48% -15%,rgba(22,53,75,.40) 0,rgba(6,11,16,0) 34%),linear-gradient(180deg,#071019 0,#060b10 100%);color:var(--text)}
.wrap{max-width:1760px;margin:0 auto;padding:0 18px 28px}
.appHeader{margin:0 -18px 18px;padding:9px 18px;border-bottom:1px solid rgba(59,88,116,.48);background:rgba(5,10,15,.94);box-shadow:0 8px 28px rgba(0,0,0,.22)}
.brand{min-width:315px}.brandMark{width:42px;height:42px;border-color:#2de075;background:radial-gradient(circle at 35% 25%,#123b35,#08151a 70%);box-shadow:0 0 0 1px rgba(53,194,255,.18),0 0 24px rgba(45,224,117,.12)}
.brandText b{font-size:16px;letter-spacing:.2px}.brandText span{color:#27db75;font-size:9px;letter-spacing:1.55px;font-weight:800}
.appNav{display:flex;align-items:stretch;gap:3px;overflow:auto}.appNav .tab,.navJump{border:0;background:transparent;color:#aab7c5;border-radius:8px;padding:7px 11px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-width:72px;font-size:10px;cursor:pointer;white-space:nowrap;transition:.16s}
.appNav .tab:hover,.navJump:hover{background:#0d1822;color:#f3f7fb}.appNav .tab.active{background:linear-gradient(180deg,#123024,#0e211a);color:#67ec92;box-shadow:inset 0 -2px #2edb71}.navIcon{font-size:15px;line-height:1}.versionPill{border-radius:8px;background:#092217;border-color:#176d3b;box-shadow:0 0 0 1px rgba(34,197,94,.05)}
.glossTerm{border-bottom:1px dotted #5b7a8f;cursor:help}
.glossTooltip{position:fixed;z-index:9999;max-width:280px;background:#0f1a24;border:1px solid #2a4a5f;border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.45;color:#d7e6ef;box-shadow:0 8px 24px rgba(0,0,0,.45);display:none}
.glossTooltip.show{display:block}
.glossTooltip b{color:#7fd8ff;display:block;margin-bottom:3px;font-size:11px;letter-spacing:.02em}
.sourceHealthStrip{display:flex;flex-wrap:wrap;gap:6px 10px;padding:6px 20px 0;font-size:10px;color:#7f97a8}
.sourceHealthStrip .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;vertical-align:middle}
.sourceHealthStrip .dot.ok{background:#2edb71}
.sourceHealthStrip .dot.degraded{background:#f59e0b}
.sourceHealthStrip .dot.unknown{background:#3a4a58}
.sourceHealthStrip .src{cursor:default}

.feedCompTable{width:100%;border-collapse:collapse;font-size:12px}
.feedCompTable th,.feedCompTable td{text-align:left;padding:5px 8px;border-bottom:1px solid #1b2835}
.feedCompTable th{color:#7f97a8;font-weight:600;font-size:10px;text-transform:uppercase}
.panel,.sideSection,.gexRailCard{background:linear-gradient(180deg,rgba(13,22,31,.98),rgba(8,15,22,.98));border:1px solid var(--line);box-shadow:0 10px 28px rgba(0,0,0,.14),inset 0 1px rgba(255,255,255,.018)}
.panel{border-radius:13px;padding:14px}.dashCol .panel{margin-bottom:13px}.dashTitle,.gexRailTitle{color:#edf3f8;letter-spacing:.42px}.note,.tiny{color:var(--muted)}
.dashboardGrid{grid-template-columns:minmax(285px,.82fr) minmax(620px,1.82fr) minmax(270px,.78fr);gap:14px}
.marketOverviewGrid{border:1px solid #1e3042;border-radius:10px;background:#081018}.marketQuote{background:linear-gradient(180deg,#0c161f,#091119);padding:12px 10px}.marketQuote .sym{font-size:10px;font-weight:750;letter-spacing:.5px}.marketQuote .px{font-size:19px;letter-spacing:-.2px}.marketQuote .chg{font-weight:700}
.dashHeatGrid{gap:7px}.dashHeatTile{min-height:99px;border-radius:9px;padding:10px;box-shadow:inset 0 1px rgba(255,255,255,.035),0 5px 14px rgba(0,0,0,.12)}.dashHeatTile:hover{transform:translateY(-2px);box-shadow:0 9px 22px rgba(0,0,0,.22)}.dashHeatTile.selected{outline:2px solid #48a2ff;box-shadow:0 0 0 4px rgba(72,162,255,.08)}.dashHeatTile.h0,.dashHeatTile.h1{background:linear-gradient(145deg,#64191f,#b92b32)}.dashHeatTile.h2,.dashHeatTile.h3{background:linear-gradient(145deg,#4a1c21,#7f282e)}.dashHeatTile.h4,.dashHeatTile.h5{background:linear-gradient(145deg,#1b2531,#253543)}.dashHeatTile.h6,.dashHeatTile.h7{background:linear-gradient(145deg,#143328,#1b5a3c)}.dashHeatTile.h8,.dashHeatTile.h9,.dashHeatTile.h10{background:linear-gradient(145deg,#0e472b,#177641)}.dashHeatTile .sym{font-size:15px}.dashHeatTile .score{font-size:22px}.miniSpark{height:19px;filter:drop-shadow(0 0 3px rgba(255,255,255,.1))}.heatScale{height:6px;background:linear-gradient(90deg,#bb2e33 0,#74252a 28%,#273746 50%,#1a6340 73%,#23b85d 100%)}
.breadthRow{padding:10px 1px}.breadthRow .val{font-size:15px}.breadthRow .move{font-weight:750}
.rrgHeader{margin-bottom:10px}.rrgHeader h2{font-size:15px;letter-spacing:.1px}.rrgToggle{border-color:#2b425a;background:#081018;border-radius:8px}.rrgToggle button{padding:8px 15px;font-weight:750}.rrgToggle button.active{background:linear-gradient(180deg,#297cff,#1d5bd8);box-shadow:0 0 0 1px rgba(72,154,255,.2)}
#sectorChart,#stockChart,#pricePreviewChart{background:linear-gradient(180deg,#09121a,#071018);border:1px solid #1e3042;border-radius:10px}.selectedSectorCard{border-color:#22384b;border-radius:10px;background:#09131c}.selectedSectorCard>div{border-color:#203244}.sscValue{font-size:13px}.sectorSummaryPanel table tbody tr:hover{background:#0f2030}.sectorSummaryPanel th{color:#90a2b5;text-transform:uppercase;font-size:9px;letter-spacing:.45px}.sectorSummaryPanel td{border-color:#1b2a37}
.sideSection{border-radius:11px;padding:13px}.sideSection h3{font-size:10px;color:#cfd8e3;letter-spacing:.65px}.sideSection select,input[type=search]{background:#0a121a;border:1px solid #2a3c4e;border-radius:8px;color:#e8eef5;padding:8px}.sideSeg button,.filterPill,.quickBtn{border-radius:7px}.quickBtn{padding:9px 10px}.quickBtn:hover{background:#132231}
.rotationLower>.panel:first-child{border-left:3px solid #23435c}.rotationLower .grid2>.panel{border-radius:12px}.row strong{letter-spacing:.1px}
table{border-collapse:separate;border-spacing:0}th{background:#0c151e;color:#93a4b7;font-size:9px;text-transform:uppercase;letter-spacing:.45px;position:sticky;top:0;z-index:2}td,th{border-bottom:1px solid rgba(34,50,66,.72)}tbody tr:hover td{background:rgba(23,38,51,.55)}
button,select,input{font-family:inherit}button{transition:.15s}button.primary{background:linear-gradient(180deg,#2379ef,#185aca);border-color:#3b82f6;box-shadow:0 4px 14px rgba(37,131,255,.12)}
/* GEX page treatment */
.gexDashboard{margin-top:14px!important;padding:16px!important;border:1px solid #1f3447!important;border-radius:14px!important;background:radial-gradient(circle at 50% -20%,rgba(24,56,77,.26),transparent 34%),linear-gradient(180deg,#09131c,#070e15)!important;box-shadow:0 18px 40px rgba(0,0,0,.22)!important}
.gexTopline{align-items:center;margin-bottom:13px}.gexTopline>div>strong{font-size:17px}.modeledTag{background:#111c27;border-color:#39516a;color:#a9bad0;font-weight:800}.chainFreshness{font-weight:750}
.positioningGrid.gammaSummary{grid-template-columns:repeat(6,minmax(150px,1fr));gap:11px;margin:10px 0 14px}.gexDashboard .metricCard{position:relative;overflow:hidden;min-height:115px;border:1px solid #213549;border-radius:12px;padding:16px 14px 13px 72px;background:linear-gradient(160deg,#0f1a24,#0a121a);box-shadow:0 8px 24px rgba(0,0,0,.13),inset 0 1px rgba(255,255,255,.025)}.gexDashboard .metricCard:before{position:absolute;left:16px;top:19px;width:42px;height:42px;border-radius:50%;display:grid;place-items:center;font-size:24px;font-weight:900;background:#0b151e;border:1px solid #2a3b4b;color:#d8e2ed}.metricCard.callWall:before{content:"↟";color:#39e57a;border-color:#17663b;background:#0b2017}.metricCard.flipCard:before{content:"↔";color:#b36cff;border-color:#57327c;background:#171021}.metricCard.putWall:before{content:"↡";color:#ff4d55;border-color:#74252d;background:#210d10}.metricCard.spotCard:before{content:"∿";color:#f2f6fa}.metricCard.netGex:before{content:"⚖";color:#48e57d;border-color:#17663b;background:#0b2017}.metricCard.exposureCard:before{content:"★";color:#f6b62b;border-color:#6e5221;background:#211b0d}.gexDashboard .metricCard .tiny:first-child{font-size:10px}.gexDashboard .metricCard .big{font-size:24px;letter-spacing:-.35px}.metricCard.callWall .big{color:#49e880}.metricCard.flipCard .big{color:#b77aff}.metricCard.putWall .big{color:#ff545c}.metricCard.spotCard.good .tiny:last-child,.metricCard.spotCard .good{color:#49e880}.metricCard.exposureCard{padding-left:67px}.exposureMini{font-size:11px}
.gexWorkspace{grid-template-columns:minmax(0,1fr) 320px;gap:14px}.gexSectionHead{padding:2px 2px 0}.gexSectionHead strong{font-size:16px}.gexHelpText{margin-top:3px}.gexViewTools{display:flex;align-items:center;gap:6px}.gexViewBtn{font-size:10px;padding:7px 18px;border:1px solid #2a4056;background:#0a141d;color:#8fa0b3;border-radius:6px}.gexViewBtn.active{background:linear-gradient(180deg,#0e4c88,#0c3562);border-color:#2583ff;color:#cde9ff}.gammaLegendTop{display:flex;gap:16px;padding:0 2px;margin:8px 0 7px;font-weight:700}
#gammaLandscape{height:610px;border-radius:12px;border-color:#22384c;background:linear-gradient(180deg,#08131b,#071018);box-shadow:inset 0 1px rgba(255,255,255,.018)}
.gexRail{gap:12px}.gexRailCard{border-radius:12px;padding:14px;background:linear-gradient(180deg,#0d1822,#09121a)}.gexRailTitle{font-size:11px;margin-bottom:10px}.gexStatRow,.gexLevelRow,.gexLargestRow,.gexLegendRow{font-size:11px;padding:5px 0}.gexMiniBar{height:9px;background:#101b26}.gammaSelectedDetail{border-radius:10px;padding:11px 13px;background:#0b1721;border-color:#284158}.gexDisclosure{background:#08131c;border-left-color:#2583ff;border-radius:8px;padding:10px 12px}
#flowSection{border-top:1px solid #1f3344;padding-top:14px;margin-top:18px!important}.flowGrid .metricCard{border-radius:10px;background:linear-gradient(180deg,#0e1821,#091119)}
#optionsDetailSection .cards .metricCard{border-radius:10px}
@media(max-width:1280px){.dashboardGrid{grid-template-columns:minmax(260px,.85fr) minmax(520px,1.7fr)}.dashRight{grid-column:1/-1;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.positioningGrid.gammaSummary{grid-template-columns:repeat(3,minmax(150px,1fr))}.gexWorkspace{grid-template-columns:1fr}.gexRail{grid-template-columns:repeat(2,minmax(0,1fr))}.appNav .tab,.navJump{min-width:64px;padding:7px 8px}}
@media(max-width:760px){.wrap{padding:0 10px 20px}.appHeader{margin:0 -10px 12px;padding:8px 10px}.brand{min-width:0}.brandText span{display:none}.appNav{order:3;width:100%}.appNav .tab,.navJump{min-width:72px}.dashboardGrid{grid-template-columns:1fr}.dashRight{display:block}.positioningGrid.gammaSummary{grid-template-columns:repeat(2,minmax(0,1fr))}.gexDashboard .metricCard{padding-left:58px;min-height:105px}.gexDashboard .metricCard:before{left:11px;width:36px;height:36px;font-size:20px}.gexRail{grid-template-columns:1fr}.gexViewTools .tiny{display:none}#gammaLandscape{height:500px}}


/* v22.4 cleanup + chart/GEX routing */
.priceChartPanelWide{margin-top:14px;padding:16px;background:radial-gradient(circle at 70% -30%,rgba(37,131,255,.08),transparent 36%),linear-gradient(180deg,#0a141d,#071018)}
.priceChartPanelWide .priceChartCanvasWrap{border-color:#294159;box-shadow:inset 0 1px rgba(255,255,255,.02),0 12px 30px rgba(0,0,0,.16)}
#pricePreviewChart{display:block;width:100%;height:560px;background:#071018}
.priceChartPanelWide .priceChartTitle strong{font-size:18px;letter-spacing:.1px}.priceChartPanelWide .priceChartLast{font-size:16px;font-weight:800}.priceChartPanelWide .priceChartMeta{margin-top:4px}
.compactRefresh .quickBtn{margin-top:9px;width:100%;background:#0b1720;border-color:#294057}
.gexPageShell{margin-bottom:14px;background:linear-gradient(180deg,#0c1720,#081018);border-color:#22394e}.gexPageHeader{display:flex;align-items:center;justify-content:space-between;gap:16px}.gexTickerControls{display:flex;gap:8px;align-items:center}.gexTickerControls input{width:190px}.gexPageHint{margin-top:12px;padding:14px;border:1px dashed #2a4258;border-radius:10px;color:#8fa0b3;background:#081119}.gexPageShell+.gexDashboard{margin-top:0!important}
@media(max-width:760px){#pricePreviewChart{height:360px}.gexPageHeader{align-items:flex-start;flex-direction:column}.gexTickerControls{width:100%}.gexTickerControls input{flex:1;width:auto}}


/* v22.5 layout cleanup */
/* v22.35 candlestick proportion fix */
.rrgShell{min-width:0}
/* Keep the RRG at its established dashboard sizing. The prior aspect-ratio override was removed. */
#sectorChart{height:500px}
/* Preserve the candlestick canvas' native 1500:640 proportions so text/candles never stretch. */
.priceChartPanelWide .priceChartCanvasWrap{width:100%;max-width:1320px;margin:0 auto}
#pricePreviewChart{width:100%!important;height:auto!important;aspect-ratio:75/32;display:block;background:#071018}
@media(max-width:760px){#pricePreviewChart{height:auto!important;aspect-ratio:75/32}.priceChartPanelWide .priceChartCanvasWrap{max-width:100%}}
.headerMeta{display:flex;align-items:center;gap:8px}.headerRefresh{padding:6px 10px;border-radius:8px;border:1px solid #234058;background:#0b1721;color:#cfe7ff;font-weight:700;font-size:10px}.headerRefresh:hover{border-color:#3b82f6;color:#fff}
.rrgControlStack{display:flex;flex-direction:column;align-items:flex-end;gap:7px}.rrgInlineFilters{display:flex;align-items:center;gap:7px}.rrgInlineFilters .filterPills{justify-content:flex-end}.rrgInlineFilters .filterPill{padding:4px 7px;font-size:8px}
.dashRight .sectorSummaryPanel{margin-top:0!important}.dashRight .sectorSummaryPanel .scroll{max-height:390px}.dashRight .sectorSummaryPanel table{font-size:9px}.dashRight .sectorSummaryPanel th,.dashRight .sectorSummaryPanel td{padding:6px 4px}.dashRight .sectorSummaryPanel th:nth-child(n+5),.dashRight .sectorSummaryPanel td:nth-child(n+5){display:none}
.gexViewTools{justify-content:flex-end}.gexViewBtn{display:none!important}

/* v22.35 layout + STRAT confluence */
.dashboardGrid{align-items:stretch}
.dashCenter>.panel,.dashRight,.dashRight .sectorSummaryPanel{height:100%;box-sizing:border-box}
#sectorChart{height:650px}
.dashRight .sectorSummaryPanel{display:flex;flex-direction:column;min-height:100%}
.dashRight .sectorSummaryPanel .scroll{max-height:none!important;flex:1;overflow:auto}
.dashRight .sectorSummaryPanel table{font-size:10px}
.rrgFilterBar{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin:8px 0 8px;flex-wrap:wrap}
.rrgSelectFilters{display:flex;gap:7px;align-items:flex-end;flex:1;min-width:0}
.rrgSelectFilters label{display:grid;gap:3px;min-width:130px;flex:1;max-width:210px}.rrgSelectFilters label span{font-size:8px;color:#7f8fa2;letter-spacing:.55px;font-weight:800}.rrgSelectFilters select{width:100%;font-size:10px;padding:6px 7px}
.rrgInlineFilters{margin-left:auto}.rrgInlineFilters .tiny{font-size:8px;letter-spacing:.5px}
.priceActionGrid{display:grid;grid-template-columns:minmax(0,1.62fr) minmax(330px,.68fr);gap:14px;align-items:stretch;margin-top:14px}
.priceActionGrid .priceChartPanelWide{margin-top:0;min-width:0}.priceChartPanelWide .priceChartCanvasWrap{width:100%;max-width:1040px;margin:0 auto}
#pricePreviewChart{width:100%!important;height:auto!important;aspect-ratio:1080/620;display:block;background:#071018}
.stratPanel{margin:0;display:flex;flex-direction:column;min-height:0;background:linear-gradient(180deg,#0d1720,#091119)}
.stratHead{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;border-bottom:1px solid #1d3040;padding-bottom:10px;margin-bottom:9px}.stratContinuity{font-size:10px;font-weight:900;border:1px solid #334155;border-radius:999px;padding:5px 8px;color:#a9b5c3;white-space:nowrap}.stratContinuity.bullish{color:#68e89a;border-color:#1b6b3e;background:#0c2618}.stratContinuity.bearish{color:#ff8181;border-color:#74313a;background:#281217}.stratContinuity.mixed{color:#f5c451;border-color:#6a5520;background:#231d0d}
.stratFrames{display:grid;gap:8px;margin-top:9px}.stratFrame{border:1px solid #223547;background:#0a141d;border-radius:9px;padding:10px}.stratFrameTop{display:flex;align-items:center;justify-content:space-between;gap:8px}.stratTf{font-weight:900;font-size:13px}.stratScenario{font-size:11px;font-weight:900;border-radius:6px;padding:3px 7px;background:#14202b}.stratScenario.bullish{color:#57e78d;background:#0e2a1a}.stratScenario.bearish{color:#ff6c72;background:#30151a}.stratScenario.neutral{color:#b6c3d0;background:#17212b}.stratPattern{font-size:11px;color:#e0e7ef;font-weight:700;margin-top:6px}.stratTrigger{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:7px}.stratTrigger div{background:#0d1822;border-radius:6px;padding:6px;font-size:9px;color:#8fa0b3}.stratTrigger b{display:block;font-size:11px;margin-top:2px;color:#dce7f2}.stratTrigger .up b{color:#5be28e}.stratTrigger .down b{color:#ff7979}.stratFTC{font-size:9px;margin-top:6px;color:#7f90a2}.stratFoot{margin-top:auto;padding-top:10px;border-top:1px solid #1c2b39;color:#748598;line-height:1.45}
.valueAcceptanceCard{margin:10px 0 12px;padding:11px 12px;border:1px solid #26394a;border-radius:10px;background:#0a131c;transition:.15s ease}
.valueAcceptanceCard.bullish{border-color:#1f6f50;background:linear-gradient(135deg,rgba(20,83,60,.20),#0a131c 58%)}
.valueAcceptanceCard.bearish{border-color:#7b3137;background:linear-gradient(135deg,rgba(111,38,45,.20),#0a131c 58%)}
.valueAcceptanceCard.developing{border-color:#8a6420;background:linear-gradient(135deg,rgba(126,86,18,.17),#0a131c 58%)}
.valueAcceptanceCard.warning{border-color:#8a4c28;background:linear-gradient(135deg,rgba(122,60,25,.18),#0a131c 58%)}
.valueAcceptanceCard.neutral{border-color:#26394a}
.valueAcceptanceTop{display:flex;align-items:center;justify-content:space-between;gap:10px}
.valueAcceptanceTop>div{display:flex;flex-direction:column;gap:3px}
.vaEyebrow{font-size:8px;letter-spacing:.9px;color:#74879a;font-weight:800}
.valueAcceptanceTop strong{font-size:12px;color:#edf4fa}
.vaPill{padding:4px 7px;border-radius:999px;border:1px solid #33485b;font-size:8px;font-weight:900;letter-spacing:.4px;color:#9dafbf;background:#101a24;white-space:nowrap}
.vaPill.bullish{border-color:#25835d;color:#71e6ae;background:#0b261c}
.vaPill.bearish{border-color:#94404a;color:#ff8f98;background:#2a1115}
.vaPill.developing{border-color:#9b7023;color:#f8c557;background:#2b210d}
.vaPill.warning{border-color:#a65d2c;color:#ffad72;background:#2d170d}
.vaPill.neutral{border-color:#33485b;color:#9dafbf;background:#101a24}
.valueAcceptanceLevels{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:9px 0 7px}
.valueAcceptanceLevels span{padding:6px 7px;border:1px solid #1c2b37;border-radius:6px;color:#728598;font-size:8px}
.valueAcceptanceLevels b{display:block;margin-top:2px;color:#dbe6ef;font-size:10px}

@media(max-width:1050px){.priceActionGrid{grid-template-columns:1fr}.stratFrames{grid-template-columns:repeat(4,minmax(0,1fr))}.stratFoot{margin-top:10px}.rrgSelectFilters{flex-wrap:wrap}.rrgSelectFilters label{min-width:150px}.dashRight .sectorSummaryPanel{min-height:420px}}
@media(max-width:760px){#sectorChart{height:470px}.rrgFilterBar{align-items:stretch}.rrgSelectFilters{display:grid;grid-template-columns:1fr}.rrgSelectFilters label{max-width:none}.rrgInlineFilters{margin-left:0}.stratFrames{grid-template-columns:1fr 1fr}#pricePreviewChart{aspect-ratio:1080/620}}
@media(max-width:1100px){.rrgControlStack{align-items:stretch}.rrgInlineFilters{flex-wrap:wrap}.rrgInlineFilters .filterPills{justify-content:flex-start}.dashRight .sectorSummaryPanel .scroll{max-height:300px}}



/* v22.35 sector-summary containment */
.dashRight{min-height:0!important;overflow:hidden}
.dashRight .sectorSummaryPanel{
  height:100%!important;
  min-height:0!important;
  max-height:100%!important;
  overflow:hidden!important;
}
.dashRight .sectorSummaryPanel .scroll{
  flex:1 1 auto!important;
  min-height:0!important;
  max-height:none!important;
  overflow-y:auto!important;
  overflow-x:hidden!important;
  scrollbar-gutter:stable;
  padding-right:4px;
}
.dashRight .sectorSummaryPanel .scroll::-webkit-scrollbar{width:8px}
.dashRight .sectorSummaryPanel .scroll::-webkit-scrollbar-track{background:#071018;border-radius:10px}
.dashRight .sectorSummaryPanel .scroll::-webkit-scrollbar-thumb{background:#2a4053;border-radius:10px}
.dashRight .sectorSummaryPanel .scroll::-webkit-scrollbar-thumb:hover{background:#3a5870}


/* v22.35 session volume profile */
#previewVPStatus{color:#8ea2b5}



/* v24.8 mobile dashboard + sector summary */
@media(max-width:760px){
  html,body{width:100%;max-width:100%;overflow-x:hidden}
  .wrap{width:100%;max-width:100%;padding:0 8px 18px}
  .appHeader{display:grid!important;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"brand meta" "nav nav";align-items:center!important;gap:8px 10px;margin:0 -8px 10px;padding:8px 10px;overflow:hidden}
  .brand{grid-area:brand;min-width:0!important;gap:8px;overflow:hidden}
  .brandMark{width:34px;height:34px;flex:0 0 34px;font-size:17px}
  .brandText{min-width:0;overflow:hidden}
  .brandText b{font-size:13px!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:.1px}
  .brandText span{display:none!important}
  .headerMeta{grid-area:meta;margin-left:0!important;gap:6px;white-space:nowrap}
  .headerRefresh{padding:6px 8px;font-size:9px}
  .versionPill{padding:5px 7px;font-size:9px}
  .appNav{grid-area:nav;order:unset!important;width:100%;max-width:100%;min-width:0;overflow-x:auto;overflow-y:hidden;display:flex;justify-content:flex-start;scrollbar-width:none;-webkit-overflow-scrolling:touch}
  .appNav::-webkit-scrollbar{display:none}
  .appNav .tab,.navJump{flex:0 0 auto;min-width:74px!important;padding:6px 7px;font-size:9px}
  .navIcon{font-size:13px}

  .dashboardGrid,.dashCol,.rrgShell,.dashRight,.panel,.sideSection{width:100%;max-width:100%;min-width:0}
  .dashboardGrid{gap:8px}
  .rrgShell>.panel{padding:12px}
  .rrgHeader{display:grid!important;grid-template-columns:1fr;gap:9px;align-items:start}
  .rrgHeader h2{font-size:16px;line-height:1.2;margin:0}
  .rrgControlStack{width:100%;align-items:stretch!important}
  .rrgToggle{display:grid!important;grid-template-columns:1fr 1fr;width:100%}
  .rrgToggle button{width:100%;min-height:42px}
  .rrgFilterBar{display:block!important;margin-top:10px}
  .rrgSelectFilters{display:grid!important;grid-template-columns:1fr!important;gap:8px;width:100%}
  .rrgSelectFilters label{width:100%;max-width:none!important;min-width:0!important}
  .rrgSelectFilters select{min-height:42px;font-size:12px;padding:8px 10px}
  .rrgInlineFilters{width:100%;margin:10px 0 0!important}
  .rrgInlineFilters .tiny{display:block;margin-bottom:6px}
  .rrgInlineFilters .filterPills{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;width:100%}
  .rrgInlineFilters .filterPill{width:100%;min-width:0;padding:7px 4px;font-size:9px;text-align:center}
  #sectorChart{width:100%!important;height:auto!important;aspect-ratio:1/1.05!important;min-height:0}

  .sectorSummaryPanel{height:auto!important;min-height:0!important;padding:10px!important;margin-top:8px!important}
  .sectorSummaryPanel .scroll{max-height:none!important;overflow:visible!important}
  .sectorSummaryPanel table,.sectorSummaryPanel tbody{display:block;width:100%}
  .sectorSummaryPanel thead{display:none}
  .sectorSummaryPanel tr.sectorTickerRow{display:grid;grid-template-columns:28px minmax(0,1fr);grid-template-areas:"rank sector" "rank fast" "rank trend" "rank signal";gap:5px 9px;padding:10px 2px;border-bottom:1px solid #1f3140;width:100%}
  .sectorSummaryPanel tr.sectorTickerRow:last-child{border-bottom:0}
  .sectorSummaryPanel tr.sectorTickerRow td{display:block!important;border:0!important;padding:0!important;min-width:0!important;width:auto!important}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(1){grid-area:rank;color:#9aa9b9;padding-top:2px!important}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2){grid-area:sector}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2)>b{font-size:14px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2) .tiny{font-size:10px;line-height:1.35;margin-top:2px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3){grid-area:fast;display:flex!important;align-items:center;gap:7px;flex-wrap:wrap}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(4){grid-area:trend;display:flex!important;align-items:center;gap:7px;flex-wrap:wrap}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(5){grid-area:signal;margin-top:1px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3)::before,.sectorSummaryPanel tr.sectorTickerRow td:nth-child(4)::before{font-size:8px;letter-spacing:.6px;color:#71859a;font-weight:900;min-width:48px}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3)::before{content:"FAST 10/5"}
  .sectorSummaryPanel tr.sectorTickerRow td:nth-child(4)::before{content:"TREND 25/12"}
  .sectorSummaryPanel .badge{font-size:9px;padding:3px 7px}
  .sectorSummaryPanel .flag{display:inline-block;font-size:9px}
  .sectorSummaryPanel td:nth-child(3) .tiny,.sectorSummaryPanel td:nth-child(4) .tiny{display:inline-block;margin:0;font-size:9px}
  .dashRight .sectorSummaryPanel .scroll{max-height:none!important}
}

@media(max-width:390px){
  .brandText b{font-size:12px!important}
  .headerRefresh{padding:6px 7px}
  .appNav .tab,.navJump{min-width:68px!important}
  .rrgInlineFilters .filterPills{grid-template-columns:repeat(2,minmax(0,1fr))}
}


/* v24.9 risk-support explainability + denser mobile sector summary */
.riskSupportToggle{width:100%;display:flex;align-items:center;justify-content:space-between;border:1px solid #213445;background:#0a141d;color:#91a4b7;border-radius:7px;padding:7px 9px;margin:-2px 0 7px;font-size:9px;font-weight:800;letter-spacing:.25px;text-align:left}
.riskSupportToggle:hover{border-color:#34536d;color:#c6d4e0}.riskScoreInline{border:0;background:transparent;color:inherit;padding:0;font:inherit;cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}.riskScoreInline b{color:#dce8f2}
.riskSupportBreakdown{border:1px solid #203242;background:#09131b;border-radius:8px;padding:4px 9px;margin:0 0 8px}.riskSupportBreakdown[hidden]{display:none!important}.riskPart{display:grid;grid-template-columns:18px 1fr auto;gap:7px;align-items:center;padding:6px 0;border-bottom:1px solid #172735}.riskPart:last-child{border-bottom:0}.riskPart .riskMark{font-size:11px;font-weight:900}.riskPart b{display:block;font-size:10px}.riskPart small{display:block;color:#74889b;font-size:8px;margin-top:1px}.riskPart strong{font-size:9px}.riskPart.good .riskMark,.riskPart.good strong{color:#4ade80}.riskPart.bad .riskMark,.riskPart.bad strong{color:#fb7185}.riskPart.neutral{color:#94a3b8}
@media(max-width:760px){
 .riskSupportToggle{padding:6px 8px;margin:0 0 6px;font-size:8px}.riskSupportBreakdown{padding:3px 7px;margin-bottom:6px}.riskPart{padding:5px 0;grid-template-columns:16px 1fr auto}.riskPart b{font-size:9px}.riskPart small{font-size:7px}.riskPart strong{font-size:8px}
 .sectorSummaryPanel{padding:8px!important}.sectorSummaryPanel .dashTopline{margin-bottom:4px}.sectorSummaryPanel .dashTitle{font-size:11px}.sectorSummaryPanel .dashTopline .note{font-size:8px}
 .sectorSummaryPanel tr.sectorTickerRow{grid-template-columns:20px minmax(0,1fr);gap:2px 6px;padding:6px 1px!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(1){font-size:9px;padding-top:1px!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(2)>b{font-size:12px!important;line-height:1.1}.sectorSummaryPanel tr.sectorTickerRow td:nth-child(2) .tiny{font-size:8px!important;line-height:1.2;margin-top:1px!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3),.sectorSummaryPanel tr.sectorTickerRow td:nth-child(4){gap:4px!important;min-height:18px}.sectorSummaryPanel tr.sectorTickerRow td:nth-child(5){margin-top:0!important}
 .sectorSummaryPanel tr.sectorTickerRow td:nth-child(3)::before,.sectorSummaryPanel tr.sectorTickerRow td:nth-child(4)::before{font-size:6.5px!important;min-width:42px!important;letter-spacing:.35px!important}
 .sectorSummaryPanel .badge{font-size:7px!important;padding:2px 5px!important}.sectorSummaryPanel .flag{font-size:7px!important;padding:2px 5px!important}.sectorSummaryPanel td:nth-child(3) .tiny,.sectorSummaryPanel td:nth-child(4) .tiny{font-size:7px!important}
}
/* v24 Institutional Decision Layer */
.instDecisionPanel{border:1px solid #31506c;background:linear-gradient(180deg,#0d1720,#0a1118);margin:12px 0}.instDecisionHead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:10px}.instDecisionHead h3{margin:0;font-size:14px}.instDecisionHead .horizon{font-size:10px;font-weight:900;color:#7dd3fc;border:1px solid #24566e;border-radius:999px;padding:4px 8px}.instGrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.instCard{border:1px solid #26394b;background:#0b141d;border-radius:8px;padding:9px;min-height:76px}.instCard .k{font-size:8px;letter-spacing:.6px;color:#7f93a8;font-weight:900}.instCard .v{font-size:15px;font-weight:900;margin-top:5px}.instCard .d{font-size:9px;color:#9badbf;line-height:1.45;margin-top:4px}.instSection{margin-top:9px;border-top:1px solid #1d2d3b;padding-top:9px}.instSectionTitle{font-size:9px;font-weight:900;letter-spacing:.7px;color:#cbd5e1;margin-bottom:6px}.instLevelGrid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}.instLevel{padding:7px;border:1px solid #26394b;border-radius:7px;background:#09121a}.instLevel b{display:block;font-size:12px}.instLevel span{font-size:8px;color:#8193a6}.instFactors{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:5px;margin-top:8px}.instFactor{border:1px solid #2a3e50;border-radius:6px;padding:6px;font-size:8px}.instFactor strong{display:block;font-size:11px;margin-top:2px}.instGood{color:#69e6a6}.instWarn{color:#f6c667}.instBad{color:#fb8d96}.topSetupInstitutional{margin-top:8px;border-top:1px solid #203141;padding-top:7px}.topSetupInstGrid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}.topSetupInstMetric{font-size:8px;color:#8294a6}.topSetupInstMetric b{display:block;color:#dbe7f2;font-size:10px;margin-top:2px}@media(max-width:900px){.instGrid{grid-template-columns:repeat(2,1fr)}.instLevelGrid{grid-template-columns:repeat(3,1fr)}.instFactors{grid-template-columns:repeat(2,1fr)}.topSetupInstGrid{grid-template-columns:repeat(2,1fr)}}
</style>
<div class="wrap">
<header class="appHeader">
  <div class="brand"><div class="brandMark">↗</div><div class="brandText"><b>MARKET ROTATION SCREENER</b><span>ROTATION · POSITIONING · OPTIONS</span></div></div>
  <nav class="tabs appNav">
    <button class="tab active" data-view="rotation"><span class="navIcon">▦</span><span>Dashboard</span></button>
    <button class="tab" data-view="history"><span class="navIcon">⌁</span><span>RRG Historical</span></button>
    <button class="tab" data-view="gexpage" id="navGex"><span class="navIcon">⌗</span><span>GEX Landscape</span></button>
    <button class="tab" data-view="earnings" id="navEarnings"><span class="navIcon">◫</span><span>Earnings Movers</span></button>
    <button class="navJump" id="navOptions"><span class="navIcon">▤</span><span>Options Scanner</span></button>
    <button class="navJump" id="navWatch"><span class="navIcon">☆</span><span>Watchlist</span></button>
    <button class="tab" data-view="heatmap"><span class="navIcon">▦</span><span>Heat Map</span></button>
  </nav>
  <div class="headerMeta"><button class="headerRefresh" id="dashRefreshMarket">↻ Refresh</button><span class="versionPill">{{APP_VERSION_PLACEHOLDER}}</span></div>
</header>
<div class="pageIntro"><h1>Market Rotation Screener</h1><div class="sub">Fast <span class="glossTerm" data-gloss="RRG">RRG</span> (10/5) finds change; Trend <span class="glossTerm" data-gloss="RRG">RRG</span> (25/12) confirms persistence.</div></div>
<div id="sourceHealthStrip" class="sourceHealthStrip"></div>


<div id="heatmap" class="view">
  <div class="panel">
    <div class="row"><strong>Confluence Heat Map</strong><span class="note">Visual opportunity map · rotation first, then options/flow context. Darker tiles = stronger current confluence, not a buy signal.</span>
      <label class="note">Groups</label><select id="heatGroupFilter"><option value="core">Core sectors</option><option value="all">All groups</option><option value="industry">Industries / themes</option></select>
      <button id="refreshHeat" class="primary">Refresh map</button><span id="heatStatus" class="status"></span>
    </div>
    <div class="heatLegend"><span>0–3 weak</span><span>4–5 developing</span><span>6–7 actionable watch</span><span>8–10 strong confluence</span></div>
  </div>
  <div class="panel">
    <div class="row"><strong>Sector / Group Map</strong><span class="note">Score = RRG stage + fast tail direction + trend confirmation. Click a tile to load its holdings below.</span></div>
    <div id="sectorHeatGrid" class="heatGrid"></div>
  </div>
  <div class="panel">
    <div class="row"><strong id="stockHeatTitle">Stock Map</strong><span class="note">Stock score uses rotation + your existing options quality signal. Flow/GEX appear on a tile after that ticker is selected and loaded.</span></div>
    <div id="stockHeatGrid" class="heatGrid"></div>
  </div>
</div>

<div id="gexpage" class="view">
  <div class="panel gexPageShell">
    <div class="gexPageHeader">
      <div><div class="dashTitle" style="font-size:18px">GEX LANDSCAPE</div><div class="note">Modeled gamma / open-interest positioning for the selected ticker.</div></div>
      <div class="gexTickerControls"><input id="gexTickerInput" type="search" placeholder="Ticker, e.g. NFLX" autocomplete="off"><select id="gexWindow"><option value="0-7">0–7D</option><option value="0-30" selected>0–30D</option><option value="8-30">8–30D</option><option value="31-90">31–90D</option><option value="all">ALL ≤365D</option></select><button id="gexTickerLoad" class="primary">Load GEX</button></div>
    </div>
    <div id="gexPageHint" class="gexPageHint">Select a stock anywhere in the screener or enter a ticker above. The latest loaded ticker carries into this page automatically.</div>
  </div>
  <div id="gexPageHost"></div>
</div>

<div id="rotation" class="view active">
  <div class="panel topSetupsPanel">
    <div class="dashTopline"><div><span class="dashTitle">★ TOP SETUPS</span><span class="note" style="margin-left:8px">Automatic market-wide scan · click a setup to dive deeper</span></div><div style="display:flex;align-items:center;gap:8px"><button class="secondary" style="padding:5px 8px;font-size:9px" onclick="runAutomaticTopSetups(true)">↻ Scan all</button><span id="topSetupsStatus" class="note">Waiting for market data</span> <button class="secondary" id="loadTopSetups" type="button">Load Top Setups</button></div></div>
    <div id="topSetupsGrid" class="topSetupsGrid"><div class="topSetupsEmpty">Automatic scan starts after market data loads.</div></div>
  </div>
  <details class="panel topSetupsPanel" id="speculativeSignalsPanel" open>
    <summary class="dashTopline" style="cursor:pointer;list-style:none">
      <div><span class="dashTitle">◔ SPECULATIVE SIGNALS</span><span class="note" style="margin-left:8px">Not yet A-quality confirmed — RRG early-turn (still Lagging, tail turning NE) and unusual sampled large-print activity, each tagged by source. Auto-collapses when Top Setups already has good picks.</span></div>
    </summary>
    <div style="display:flex;align-items:center;gap:8px;margin-top:8px">
      <span id="speculativeSignalsStatus" class="note">Run Top Setups first</span>
      <button class="secondary" id="runSpeculativeSignals" type="button" onclick="runSpeculativeSignals()">Scan speculative signals</button>
    </div>
    <div id="earlyTurnGrid" class="topSetupsGrid" style="margin-top:8px"><div class="topSetupsEmpty">Run Top Setups, then tap "Scan speculative signals" to check the same candidate pool for names that haven't been confirmed yet.</div></div>
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr><th>#</th><th>Ticker</th><th>Activity setup</th><th>Largest sampled print</th><th>Persistence</th><th>Rotation confirmation</th><th>Why it surfaced</th></tr></thead>
      <tbody id="institutionalRadarRows"><tr><td colspan="7" class="note">Included in the combined scan above — checks unusual sampled large-print activity across all supportive sectors.</td></tr></tbody>
    </table></div>
    <div id="institutionalRadarDisclosure" class="tiny" style="margin-top:8px">Uses bounded Alpaca SIP trade samples. This is large-print activity, not a claim of dark-pool direction or buyer/seller intent.</div>
  </details>

  <div class="panel" id="newsContextPanel">
    <div class="dashTopline"><div><span class="dashTitle">NEWS + CATALYST CONTEXT</span><span class="note" style="margin-left:8px">Market headlines plus ticker-specific context for the strongest current candidates</span></div><div style="display:flex;align-items:center;gap:8px"><button class="secondary" id="runNewsContext" type="button">Refresh news</button><span id="newsContextStatus" class="note">Ready</span></div></div>
    <div class="newsContextGrid" style="margin-top:10px">
      <div class="newsCol"><strong>Market News</strong><div id="marketNewsRows" class="tiny" style="margin-top:5px">Refresh to load current headlines.</div></div>
      <div class="newsCol"><strong>Why it matters for current setups</strong><div id="setupNewsRows" class="tiny" style="margin-top:5px">Uses the Top Setups / speculative candidate pool and deterministic catalyst labels — no AI-generated claims.</div></div>
    </div>
  </div>

  <div class="panel" id="feedCompPanel">
    <div class="row" style="align-items:center;gap:10px">
      <strong>Feed comparison</strong>
      <span class="note">IEX vs SIP stocks · indicative vs OPRA options — measure the Alpaca upgrade before committing</span>
      <input id="feedCompTicker" placeholder="Ticker" style="width:90px;text-transform:uppercase" maxlength="8">
      <button id="feedCompRun" class="primary">Compare</button>
      <span id="feedCompStatus" class="status"></span>
    </div>
    <div id="feedCompResults" style="margin-top:8px"></div>
  </div>

  <div class="dashboardGrid">
    <aside class="dashCol dashLeft">
      <div class="panel">
        <div class="dashTopline"><span class="dashTitle">MARKET OVERVIEW</span><span id="dashboardUpdated" class="note">Awaiting refresh</span></div>
        <div id="dashboardMarketOverview" class="marketOverviewGrid"></div>
      </div>
      <div class="panel">
        <div class="dashTopline"><span class="dashTitle">BREADTH & RISK</span><span id="regimeSummary" class="note">Loading…</span></div>
        <button type="button" id="riskSupportToggle" class="riskSupportToggle" aria-expanded="false">Risk support details <span>▾</span></button>
        <div id="riskSupportBreakdown" class="riskSupportBreakdown" hidden></div>
        <div id="dashboardBreadth" class="breadthList"></div>
      </div>
      <div class="panel">
        <div class="dashTopline"><span class="dashTitle">MACRO CALENDAR</span><span class="note">FOMC · CPI · Jobs</span></div>
        <div id="dashboardMacro" class="breadthList"></div>
      </div>
    </aside>

    <main class="dashCol dashCenter rrgShell">
      <div class="panel">
        <div class="rrgHeader"><div><h2 id="dashboardRRGTitle">RRG LIVE · FAST ROTATION (10/5)</h2><div class="note">Benchmark: SPY · click a ticker to focus and load holdings</div></div><div class="rrgControlStack"><div class="rrgToggle"><button id="rrgFastBtn" class="active">FAST 10/5</button><button id="rrgTrendBtn">TREND 25/12</button></div></div></div>
        <div class="rrgFilterBar">
          <div class="rrgSelectFilters">
            <label><span>SECTOR / GROUP</span><select id="dashboardSectorSelect"><option value="">Choose sector…</option></select></label>
            <label><span>UNIVERSE</span><select id="groupFilter"><option value="all">All groups</option><option value="core" selected>Core sectors</option><option value="industry">Industries / themes</option></select></label>
            <label><span>MACRO</span><select id="macroBasketFilter"><option value="all">All</option><option value="rate">Rate sensitive</option><option value="cyclical">Cyclicals</option><option value="defensive">Defensives</option><option value="inflation">Inflation sensitive</option></select></label>
          </div>
          <div class="rrgInlineFilters"><span class="tiny">QUADRANT</span><div class="filterPills" id="sectorQuadPills"><button class="filterPill active" data-q="all">All</button><button class="filterPill leading" data-q="Leading">Leading</button><button class="filterPill" data-q="Improving">Improving</button><button class="filterPill weakening" data-q="Weakening">Weakening</button><button class="filterPill lagging" data-q="Lagging">Lagging</button></div></div>
        </div>
        <canvas id="sectorChart" width="900" height="650"></canvas>
        <div id="selectedSectorCard" class="selectedSectorCard"><div><div class="sscLabel">Selected</div><div class="sscValue">Click a sector</div></div><div><div class="sscLabel">Fast 10/5</div><div class="sscValue">—</div></div><div><div class="sscLabel">Trend 25/12</div><div class="sscValue">—</div></div><div><div class="sscLabel">Interpretation</div><div class="sscInterp">Fast finds the turn; Trend checks whether it is persisting.</div></div></div>
      </div>
    </main>

    <aside class="dashCol dashRight">
      <div class="sideSection sectorSummaryPanel"><div class="dashTopline"><span class="dashTitle">SECTOR SUMMARY</span><span class="note">Fast + Trend</span></div><div class="scroll"><table><thead><tr><th>#</th><th>Sector</th><th>Fast</th><th>Trend</th><th>Signal</th></tr></thead><tbody id="sectorRows"></tbody></table></div></div>
    </aside>
  </div>
  <div class="legacyMarketBlock"><button class="primary" id="refreshMarket">Refresh market</button><div id="internals" class="cards"></div></div>
  <div class="rotationLower">
  <div class="panel">
    <div class="row"><strong>Stock screen</strong><span class="note">Tip: click an RRG ticker label to focus its tail in place; other tails dim. Click again to clear. Use a stock table row to open the chart / volume-profile deep dive. Search/filters use loaded data and cached universes repopulate instantly.</span>
      <label class="note">ETF / group</label>
      <select id="coreSectorSelect">
        <option value="">Choose ETF…</option>
        <option value="XLK">XLK · Technology</option>
<option value="XLC">XLC · Communication Services</option>
<option value="XLY">XLY · Consumer Discretionary</option>
<option value="XLF">XLF · Financials</option>
<option value="XLI">XLI · Industrials</option>
<option value="XLB">XLB · Materials</option>
<option value="XLE">XLE · Energy</option>
<option value="XLV">XLV · Health Care</option>
<option value="XLP">XLP · Consumer Staples</option>
<option value="XLU">XLU · Utilities</option>
<option value="XLRE">XLRE · Real Estate</option>
<option value="SMH">SMH · Semiconductors</option>
<option value="IGV">IGV · Software</option>
<option value="XBI">XBI · Biotech</option>
<option value="IBB">IBB · Biotechnology</option>
<option value="ITB">ITB · Homebuilders</option>
<option value="XRT">XRT · Retail</option>
<option value="KRE">KRE · Regional Banks</option>
<option value="XME">XME · Metals & Mining</option>
<option value="XOP">XOP · Oil & Gas Exploration</option>
<option value="OIH">OIH · Oil Services</option>
<option value="IYT">IYT · Transportation</option>
<option value="ITA">ITA · Aerospace & Defense</option>
<option value="TAN">TAN · Solar</option>
<option value="PBW">PBW · Clean Energy</option>
      </select>
      <label class="note">Holdings</label>
      <select id="liveHoldingsLimit">
        <option value="20" selected>20</option>
        <option value="50">50</option>
        <option value="all">All</option>
      </select>

      <label class="note">Quadrant</label>
      <select id="liveQuadrantFilter">
        <option value="all" selected>All</option>
        <option value="Leading">Leading</option>
        <option value="Improving">Improving</option>
        <option value="Weakening">Weakening</option>
        <option value="Lagging">Lagging</option>
      </select>
      <label class="note">Tail</label>
      <select id="liveTailFilter">
        <option value="all" selected>All</option>
        <option value="Potential Turn">Potential Turn 👀</option>
        <option value="Rotating In">Rotating In ↗</option>
        <option value="Rotating Out">Rotating Out ↙</option>
      </select>
      <label class="note">Search</label>
      <input id="liveTickerSearch" type="search" placeholder="Ticker / name…" autocomplete="off" style="width:96px">
      <span class="tiny">Enter = find ticker + load options</span>


      <span id="sectorTitle" class="note">Choose a sector</span>
      
      <button id="refreshSector">Refresh</button><button id="scanOptions">Scan options</button><span id="sstatus" class="status"></span>
    </div>
  </div>
  <div class="grid2">
    <div class="panel"><canvas id="stockChart" width="900" height="540"></canvas></div>
    <div class="panel"><div class="scroll"><table><thead><tr><th></th><th>Ticker</th><th>Score</th><th>Fast</th><th>Trend</th><th>Rotation stage</th><th>Opportunity</th><th>Options</th></tr></thead><tbody id="stockRows"></tbody></table></div></div>
  </div>

  <div class="priceActionGrid">
    <div class="panel priceChartPanel priceChartPanelWide" id="previewPanel">
      <div class="priceChartHeader">
        <div>
          <div class="priceChartTitle"><strong id="previewTitle">Chart Preview</strong><span id="previewLastPrice" class="priceChartLast"></span></div>
          <div id="previewMeta" class="priceChartMeta">Daily candles · select a ticker to preview</div>
        </div>
        <div class="priceChartControlStack">
          <div class="tfControls">
            <span class="vpLabel">Chart</span>
            <button id="tf1H" class="tfBtn">1H</button>
            <button id="tf4H" class="tfBtn">4H</button>
            <button id="tf1D" class="tfBtn active">1D</button>
            <button id="tf1W" class="tfBtn">1W</button>
          </div>
          <div class="priceChartControls">
            <button id="preview1M" class="previewPeriodBtn active">1M</button>
            <button id="preview3M" class="previewPeriodBtn">3M</button>
            <button id="preview6M" class="previewPeriodBtn">6M</button>
          </div>
          <div class="vpControls">
            <span class="vpLabel">Volume Profile</span>
            <button id="vpAuto" class="vpModeBtn active">Per Session</button>
            <button id="vpOff" class="vpModeBtn">Off</button>
            <button id="vpSession" class="vpModeBtn">Session</button>
            <button id="vpPrevious" class="vpModeBtn">Previous Session</button>
          </div>
        </div>
      </div>
      <div class="tiny" id="vpSessionLabel" style="color:#7f97a8;margin:2px 0 4px">Session: —</div>
      <div class="vpLevelStrip" id="vpLevelStrip">
        <div class="vpLevelItem"><span class="vpSwatch vah"></span><span class="glossTerm" data-gloss="VAH">VAH</span><strong id="vpVahTop">—</strong></div>
        <div class="vpLevelItem"><span class="vpSwatch poc"></span><span class="glossTerm" data-gloss="POC">POC</span><strong id="vpPocTop">—</strong></div>
        <div class="vpLevelItem"><span class="vpSwatch val"></span><span class="glossTerm" data-gloss="VAL">VAL</span><strong id="vpValTop">—</strong></div>
      </div>
      <div id="stockDeepDiveAnchor"></div><div class="priceChartCanvasWrap"><canvas id="pricePreviewChart" width="1180" height="680"></canvas></div>
      <div class="chartStatsStrip">
        <div><span>PROFILE RANGE</span><strong id="statSessionRange">—</strong><small id="statSessionRangePct">—</small></div>
        <div><span>VISIBLE RANGE</span><strong id="statVisibleRange">—</strong><small id="statVisibleRangePct">—</small></div>
        <div><span>PROFILE VOLUME</span><strong id="statSessionVol">—</strong><small>RTH source volume</small></div>
        <div><span>AVG VOLUME (20)</span><strong id="statAvgVol">—</strong><small id="statVsAvgVol">—</small></div>
      </div>
      <div class="priceChartFooter"><span id="previewStatus" class="status">Select a ticker to preview price.</span><span class="tiny" id="previewVPStatus">Per-session SVP · cleaner profile separation · 68% value area · price badge moved to axis gutter.</span></div>
    </div>
    <aside class="panel stratPanel" id="stratPanel">
      <div class="stratHead"><div><div class="dashTitle">PRICE ACTION · <span class="glossTerm" data-gloss="STRAT">STRAT</span></div><div class="note">1H · 4H · 1D · 1W trigger confluence</div></div><span id="stratContinuity" class="stratContinuity">—</span></div>
      <div id="stratStatus" class="tiny">Select a ticker to load STRAT scenarios.</div>
      <div class="valueAcceptanceCard neutral" id="valueAcceptanceCard">
        <div class="valueAcceptanceTop">
          <div><span class="vaEyebrow">VALUE ACCEPTANCE</span><strong id="valueAcceptanceState">Awaiting chart</strong></div>
          <span class="vaPill neutral" id="valueAcceptancePill">—</span>
        </div>
        <div class="tiny" id="valueAcceptanceRefLabel" style="color:#7f97a8;margin-top:2px">Reference: —</div>
        <div class="valueAcceptanceLevels">
          <span>VAH <b id="analysisVah">—</b></span>
          <span>POC <b id="analysisPoc">—</b></span>
          <span>VAL <b id="analysisVal">—</b></span>
        </div>
        <div class="tiny" id="valueAcceptanceDetail">Uses the completed profile immediately preceding the current trigger period.</div>
      </div>
      <div id="stratFrames" class="stratFrames"></div>
      <div class="stratFoot tiny">FTC compares each current timeframe candle with its open. Trigger levels are the current bar high/low for the next directional break.</div>
    </aside>
  </div>

  <div class="panel" id="optionsPanel">
    <div class="row">
      <strong>Options · 7–35 DTE</strong>
      <span class="note">Alpaca options data for screening. Positioning is modeled; flow is sampled and does not infer buyer/seller intent.</span>
      <a id="alpacaSignupBtn" href="https://app.alpaca.markets/signup" target="_blank" rel="noopener" class="setupBtn">Connect Alpaca / Get API Key ↗</a>
      <span id="optionsUnderlying" class="note"></span>
      <span id="optionsStatus" class="status"></span>
    </div>
    <div id="alpacaSetupBox" class="setupBox">
      <b>Alpaca setup</b>
      <span class="note">Create a free Alpaca account, then add <code>APCA_API_KEY_ID</code> and <code>APCA_API_SECRET_KEY</code> in Render → Environment. Redeploy after saving.</span>
    </div>

    <div id="positioningSection" class="gexDashboard" style="display:none;margin-top:10px">
      <div class="gexTopline">
        <div><strong>Dealer Positioning <span class="modeledTag">MODELED</span></strong><div class="note">Gamma × OI heuristic — transparent approximation, not actual dealer inventory.</div></div>
        <span id="chainFreshness" class="chainFreshness"></span>
      </div>
      <div class="card" id="tradeThesisPanel" style="margin:10px 0 12px"><div class="row"><strong>TRADE THESIS</strong><span class="badge" id="thesisBias">AWAITING DATA</span></div><div id="thesisText" class="note" style="margin-top:8px">Load a ticker to synthesize rotation, value, STRAT and GEX.</div><div id="confluenceZone" class="tiny" style="margin-top:8px"></div><div id="historicalSetup" class="tiny" style="margin-top:8px"></div></div><div id="positioningSummary" class="positioningGrid gammaSummary"></div>
      <div class="gexWorkspace">
        <div class="gexMain">
          <div class="gexSectionHead"><div><strong>GEX LANDSCAPE</strong><span class="note"> Modeled GEX / OI positioning</span><div class="tiny gexHelpText">Click a strike to view details, open interest, and net exposure.</div></div><div class="gexViewTools"><span class="tiny">Click a strike to inspect it.</span></div></div>
          <div class="gammaLegend gammaLegendTop"><span class="callDot">● CALL GEX</span><span class="putDot">● PUT GEX</span><span>○ SPOT</span><span class="flipDot">-- GAMMA FLIP</span><span class="callRailDot">-- CALL WALL</span><span class="putRailDot">-- PUT WALL</span></div>
          <canvas id="gammaLandscape" width="1200" height="560"></canvas>
          <div id="gammaLevelDetail" class="gammaSelectedDetail">Click a strike row for call GEX, put GEX, net GEX and open interest.</div>
          <div class="gexDisclosure tiny">Modeled from chain gamma / OI using a call-positive / put-negative convention. The flip re-prices Black-Scholes gamma across hypothetical spot levels.</div>
        </div>
        <aside class="gexRail">
          <div class="gexRailCard"><div class="gexRailTitle">GEX SUMMARY</div><div id="gexSummary"></div></div>
          <div class="gexRailCard"><div class="gexRailTitle">KEY LEVELS</div><div id="gexKeyLevels"></div></div>
          <div class="gexRailCard"><div class="gexRailTitle">LARGEST NET GEX BY STRIKE</div><div id="gexLargest"></div></div>
          <div class="gexRailCard"><div class="gexRailTitle">LEGEND</div><div id="gexLegend"></div></div>
        </aside>
      </div>
      <div id="positioningLevels" style="display:none"></div>
    </div>
    <div id="flowSection" style="display:none;margin-top:12px">
      <div class="row"><strong>Institutional Flow · event engine</strong><button id="refreshFlow" class="ghost">Refresh flow</button><span id="flowStatus" class="status"></span></div>
      <div id="flowSummary" class="flowGrid"></div>
      <div id="flowDisclosure" class="flowDisclosure tiny"></div>
      <div class="scroll flowTable"><table><thead><tr><th>Contract</th><th>Trade</th><th>Size</th><th>Premium</th><th>Time</th></tr></thead><tbody id="flowRows"></tbody></table></div>
      <div id="unusualFlow" class="tiny" style="margin-top:8px"></div>
    </div>
    <div id="optionsScanSection" style="display:none">
      <div class="row" style="margin-top:10px">
        <strong>Scan Results</strong>
        <span class="note">Ranked across all currently filtered RRG tickers. Select a ticker row above to load its full chain automatically.</span>
      </div>
      <div class="scroll"><table>
        <thead><tr><th>#</th><th>Ticker</th><th>Liquidity</th><th>ATM IV</th><th>IV state</th><th>IV/RV</th><th>Liquid contracts</th><th>Tradable contracts</th></tr></thead>
        <tbody id="optionsScanRows"></tbody>
      </table></div>
    </div>
    <div id="optionsDetailSection">
      <div id="optionsSummary" class="cards"></div>
      <div class="row" style="margin:10px 0 6px 0">
        <strong>Options chain</strong>
        <label class="note">Call / Put</label>
        <select id="optTypeFilter"><option value="all">Calls + puts</option><option value="call">Calls</option><option value="put">Puts</option></select>
        <label class="note">Liquidity</label>
        <select id="optLiquidityFilter"><option value="all">Any</option><option value="Tradable">Tradable+</option><option value="Liquid">Liquid only</option></select>
      </div>
      <div class="scroll"><table>
        <thead><tr><th>Contract</th><th>DTE</th><th>Mid</th><th>Bid</th><th>Ask</th><th>Spread</th><th>Vol</th><th>OI</th><th>IV</th><th>Delta</th><th>Liquidity</th></tr></thead>
        <tbody id="optionsRows"><tr><td colspan="11" class="note">Select a ticker in the RRG/table to load a human-readable 0–30 DTE chain, including weekly contracts under 7 DTE. Mid premium is highlighted; the raw OCC symbol is shown in small text.</td></tr></tbody>
      </table></div>
    </div>
  </div>


  <div class="panel" id="watchlistPanel">
    <div class="row">
      <strong>★ Live Watchlist</strong>
      <span class="note">Saved locally in this browser.</span>
      <button id="refreshLiveWatchlist">Refresh prices/options</button><button id="clearLiveWatchlist">Clear all</button>
      <span id="liveWatchStatus" class="status"></span>
    </div>
    <div class="scroll">
      <table>
        <thead><tr><th></th><th>Ticker</th><th>ETF</th><th>Added</th><th>Price</th><th>Since add</th><th>Rotation</th><th>Options</th><th>Opportunity</th></tr></thead>
        <tbody id="liveWatchRows"></tbody>
      </table>
    </div>
  </div>
  </div>
</div>

<div id="earnings" class="view">
  <div class="panel">
    <div class="row"><strong>🔥 Post-Earnings Opportunities</strong>
      <label class="note">Reported within</label><select id="earnDays"><option selected>5</option><option>7</option><option>10</option></select><span class="note">trading days (approx.)</span>
      <span class="note">Automatically scans all sectors + industries/themes</span>
      <label class="note">Mover filter</label><select id="moverFilter"><option value="all">All</option><option value="hm">High + Moderate</option><option value="high">High only</option></select>
      <label class="note">Search</label><input id="earnTickerSearch" type="search" placeholder="Ticker / name…" autocomplete="off" style="width:120px">
      <button class="primary" id="runEarnings">Scan all earnings</button><span id="estatus" class="status"></span>
    </div>
    <div class="note" style="margin-top:9px">Ranks recent reporters by historical 5–14D continuation, current post-earnings move and trajectory-first RRG. Options favor discounted OTM contracts (~2–8% OTM, roughly 0.25–0.45 delta) with real OI/volume. Liquid, Tradable, and Wide but Active can qualify; poor/no-market contracts are rejected.</div>
  </div>
  <div class="panel">
    <table><thead><tr><th>#</th><th>Opportunity</th><th>Recent earnings</th><th>Historical continuation</th><th>Current / RRG</th><th>Best OTM contract</th><th>Details</th></tr></thead><tbody id="earnRows"></tbody></table>
  </div>
</div>

<div id="history" class="view">
  <div class="panel">
    <div class="row">
      <strong>Historical RRG · point-in-time replay</strong><span class="note">Filter historical chart by quadrant and tail trajectory.</span>
      <label class="note">View</label>
      <select id="histMode">
        <option value="groups">Groups vs SPY</option>
        <option value="stocks">Stocks within ETF</option>
      </select>
      <label class="note">ETF</label>
      <select id="histETF">""" + "".join([f'<option value="{k}">{k} · {v}</option>' for k,v in RRG_UNIVERSE.items()]) + r"""</select>
      <label class="note" id="histLimitLabel">Holdings</label>
      <select id="histLimit">
        <option value="20" selected>20</option>
        <option value="50">50</option>
        <option value="all">All</option>
      </select>
      <span class="histFilterPair">
        <label class="note">Quadrant</label>
      <select id="histQuadrantFilter">
        <option value="all" selected>All</option>
        <option value="Leading">Leading</option>
        <option value="Improving">Improving</option>
        <option value="Weakening">Weakening</option>
        <option value="Lagging">Lagging</option>
      </select>
        <label class="note">Tail</label>
      <select id="histTailFilter">
        <option value="all" selected>All</option>
        <option value="Potential Turn">Potential Turn 👀</option>
        <option value="Rotating In">Rotating In ↗</option>
        <option value="Rotating Out">Rotating Out ↙</option>
      </select>
      </span>
      <label class="note">Search</label>
      <input id="histTickerSearch" type="search" placeholder="Ticker…" autocomplete="off" style="width:96px">

      <label class="note">As of</label>
      <input type="date" id="histDate">
      <button id="histPrev">← Previous day</button>
      <button class="primary" id="runHistory">Load</button>
      <button id="histNext">Next day →</button>
      <span id="histStatus" class="status"></span>
    </div>
    <div class="note" style="margin-top:9px">
      The RRG is calculated only with price data available on or before the selected date. Historical stock mode defaults to top 20 holdings. Search/filters are instant; previously loaded Group/Stock, ETF, date and holdings-limit combinations repopulate from browser-session cache.
    </div>
    <div id="histCaveat" class="note" style="margin-top:6px;color:#f59e0b"></div>
  </div>
  <div class="grid2">
    <div class="panel">
      <div class="row"><strong id="histTitle">Historical RRG</strong><span class="note">Click a ticker row or chart label to focus it; all other displayed tails dim. Click again to clear.</span></div>
      <canvas id="historyChart" width="900" height="540"></canvas>
    </div>
    <div class="panel">
      <div class="scroll"><table>
        <thead><tr><th>#</th><th>Ticker</th><th>Fast</th><th>Trend</th><th>+1D</th><th>+5D</th><th>+10D</th><th>+20D</th></tr></thead>
        <tbody id="histRows"></tbody>
      </table></div>
    </div>
  </div>
</div>

</div>
<div id="glossTooltip" class="glossTooltip"></div>
<script>
const GLOSSARY={
 RRG:"Relative Rotation Graph — plots a stock or sector's relative strength (RS-Ratio) against the momentum of that strength (RS-Momentum) to show whether it's leading, weakening, lagging, or improving versus a benchmark.",
 GEX:"Gamma Exposure — a model of how options dealers are positioned. Positive/dampening gamma tends to pin price near current levels; negative/amplifying gamma tends to accelerate moves.",
 VAH:"Value Area High — the top of the price range where roughly 68% of a session's volume traded. A close above VAH suggests buyers are willing to pay outside the prior 'fair value' zone.",
 POC:"Point of Control — the single price level with the most traded volume in a session; often acts as a magnet or pivot.",
 VAL:"Value Area Low — the bottom of the price range where roughly 68% of a session's volume traded. A close below VAL suggests sellers are pushing outside the prior 'fair value' zone.",
 STRAT:"A price-action framework classifying each bar as Inside (1), Directional (2U/2D), or Outside (3) relative to the prior bar, used here across 1H/4H/1D/1W to gauge multi-timeframe agreement.",
 FTC:"Full Timeframe Continuity — how many of the 1H/4H/1D/1W timeframes are currently in an aligned directional STRAT scenario (not just a green/red candle).",
 IV:"Implied Volatility — the options market's forward-looking estimate of how much a stock will move, baked into an option's price.",
 DTE:"Days To Expiration — how many calendar days remain until an option contract expires.",
};
function glossTerm(label,key){const k=key||label;if(!GLOSSARY[k])return label;return `<span class="glossTerm" data-gloss="${k}">${label}</span>`;}
document.addEventListener("click",function(e){const el=e.target.closest(".glossTerm"),tip=document.getElementById("glossTooltip");if(!tip)return;if(!el){tip.classList.remove("show");return;}const def=GLOSSARY[el.dataset.gloss];if(!def){tip.classList.remove("show");return;}tip.innerHTML=`<b>${el.dataset.gloss}</b>${def}`;const r=el.getBoundingClientRect();tip.style.top=Math.min(window.innerHeight-20,r.bottom+8)+"px";tip.style.left=Math.max(8,Math.min(window.innerWidth-296,r.left))+"px";tip.classList.add("show");e.stopPropagation();});
const SOURCE_LABELS={yfinance:"Yahoo (prices)",alpaca_stocks:"Alpaca (stocks)",alpaca_options:"Alpaca (options)",finnhub:"Finnhub",unusual_whales:"Unusual Whales",nasdaq_yahoo_calendar:"Earnings calendar"};
function timeAgo(iso){if(!iso)return null;const x=Math.max(0,(Date.now()-new Date(iso).getTime())/1000);if(x<60)return "just now";if(x<3600)return Math.round(x/60)+"m ago";if(x<86400)return Math.round(x/3600)+"h ago";return Math.round(x/86400)+"d ago";}
async function refreshSourceHealth(){const el=document.getElementById("sourceHealthStrip");if(!el)return;try{const r=await fetch("/api/source-health"),j=await r.json();if(!j?.ok||!Array.isArray(j.sources))return;el.innerHTML=j.sources.map(x=>{const label=SOURCE_LABELS[x.name]||x.name;const detail=x.status==="ok"?`Last success ${timeAgo(x.last_success)||"—"}`:x.status==="degraded"?`Falling back — last success ${timeAgo(x.last_success)||"never this session"}, last error ${timeAgo(x.last_error)}`:"Not called yet this session";return `<span class="src" title="${detail.replace(/"/g,'&quot;')}"><span class="dot ${x.status}"></span>${label}</span>`;}).join("");}catch(e){}}
document.addEventListener("click",function(e){
 const trigger=e.target.closest("#riskSupportToggle,#riskScoreInline");
 if(!trigger)return;
 const box=document.getElementById("riskSupportBreakdown"),btn=document.getElementById("riskSupportToggle");
 if(!box)return;
 const opening=box.hasAttribute("hidden");
 if(opening)box.removeAttribute("hidden");else box.setAttribute("hidden","");
 if(btn){btn.setAttribute("aria-expanded",opening?"true":"false");const a=btn.querySelector("span");if(a)a.textContent=opening?"▴":"▾";}
});
document.addEventListener("DOMContentLoaded",refreshSourceHealth);setInterval(refreshSourceHealth,5*60*1000);
async function refreshMacroCalendar(){const el=document.getElementById("dashboardMacro");if(!el)return;try{const r=await fetch("/api/macro-calendar?within_days=90"),j=await r.json();if(!j?.ok||!Array.isArray(j.events))return;if(!j.events.length){el.innerHTML=`<div class="note">No confirmed major macro dates in the next 90 days.</div>`;return;}el.innerHTML=j.events.map(e=>{const high=e.importance==="HIGH",urgent=e.days_away<=1&&high,tag=high?"HIGH":(e.importance||"WATCH");return `<div class="breadthRow"><div class="name">${urgent?"⚠️ ":""}${e.label}<div class="tiny">${tag} · ${e.time||"time TBA"} · ${e.source||"official source"}</div></div><div class="val ${urgent?"neg":""}">${e.date}</div><div class="move ${urgent?"neg":""}">${e.days_away}d</div></div>`;}).join("");}catch(e){}}
document.addEventListener("DOMContentLoaded",refreshMacroCalendar);setInterval(refreshMacroCalendar,60*60*1000);


function fmtDelta(a,b){
 if(a==null||b==null||!Number.isFinite(Number(a))||!Number.isFinite(Number(b)))return "—";
 a=Number(a);b=Number(b); if(a===0)return b===0?"0%":"n/a";
 const pct=((b-a)/Math.abs(a))*100; return `${pct>0?"+":""}${pct.toFixed(1)}%`;
}
function feedCompRow(label,freeVal,paidVal,deltaText,freeFmt=(x)=>x,paidFmt=(x)=>x){
 return `<tr><td>${label}</td><td>${freeVal==null?"—":freeFmt(freeVal)}</td><td>${paidVal==null?"—":paidFmt(paidVal)}</td><td>${deltaText}</td></tr>`;
}
function feedCompUnavailable(side,reason){return `<div class="note" style="margin-top:4px">${side}: not available — ${reason||"unknown reason"}</div>`;}
async function runFeedComparison(){
 const input=document.getElementById("feedCompTicker"),st=document.getElementById("feedCompStatus"),out=document.getElementById("feedCompResults");
 const ticker=(input?.value||"").trim().toUpperCase(); if(!ticker){if(st)st.textContent="Enter a ticker";return;}
 if(st)st.textContent="Comparing…";if(out)out.innerHTML="";
 try{
   const r=await fetch(`/api/feed-comparison/${encodeURIComponent(ticker)}`),j=await r.json();
   if(!j?.ok){if(st)st.textContent=j?.error||"Comparison failed";return;}
   if(st)st.textContent=`${ticker} · ${new Date().toLocaleTimeString()}`; const s=j.stocks||{},o=j.options||{}; let html="";
   if(s.iex?.available&&s.sip?.available){html+=`<div class="tiny" style="margin-bottom:4px">STOCKS · session ${s.sip.session_date||s.iex.session_date||"—"}</div><table class="feedCompTable"><thead><tr><th></th><th>IEX (free)</th><th>SIP (paid)</th><th>Δ</th></tr></thead><tbody>${feedCompRow("Session volume",s.iex.total_volume,s.sip.total_volume,fmtDelta(s.iex.total_volume,s.sip.total_volume),(x)=>Number(x).toLocaleString(),(x)=>Number(x).toLocaleString())}${feedCompRow("VAH",s.iex.vah,s.sip.vah,fmtDelta(s.iex.vah,s.sip.vah),(x)=>"$"+x,(x)=>"$"+x)}${feedCompRow("POC",s.iex.poc,s.sip.poc,fmtDelta(s.iex.poc,s.sip.poc),(x)=>"$"+x,(x)=>"$"+x)}${feedCompRow("VAL",s.iex.val,s.sip.val,fmtDelta(s.iex.val,s.sip.val),(x)=>"$"+x,(x)=>"$"+x)}</tbody></table>`;}
   else{html+=`<div class="tiny" style="margin-bottom:4px">STOCKS</div>`;if(!s.iex?.available)html+=feedCompUnavailable("IEX",s.iex?.reason);if(!s.sip?.available)html+=feedCompUnavailable("SIP",s.sip?.reason);}
   if(o.indicative?.available&&o.opra?.available){html+=`<div class="tiny" style="margin:10px 0 4px">OPTIONS</div><table class="feedCompTable"><thead><tr><th></th><th>Indicative (free)</th><th>OPRA (paid)</th><th>Δ</th></tr></thead><tbody>${feedCompRow("Contracts in range",o.indicative.contracts,o.opra.contracts,fmtDelta(o.indicative.contracts,o.opra.contracts))}${feedCompRow("Median spread %",o.indicative.median_spread_pct,o.opra.median_spread_pct,fmtDelta(o.indicative.median_spread_pct,o.opra.median_spread_pct))}${feedCompRow("Call wall",o.indicative.call_wall,o.opra.call_wall,fmtDelta(o.indicative.call_wall,o.opra.call_wall),(x)=>"$"+x,(x)=>"$"+x)}${feedCompRow("Put wall",o.indicative.put_wall,o.opra.put_wall,fmtDelta(o.indicative.put_wall,o.opra.put_wall),(x)=>"$"+x,(x)=>"$"+x)}<tr><td>Gamma regime</td><td>${o.indicative.gamma_regime||"—"}</td><td>${o.opra.gamma_regime||"—"}</td><td>${o.indicative.gamma_regime===o.opra.gamma_regime?"same":"DIFFERS"}</td></tr></tbody></table>`;}
   else{html+=`<div class="tiny" style="margin:10px 0 4px">OPTIONS</div>`;if(!o.indicative?.available)html+=feedCompUnavailable("Indicative",o.indicative?.reason);if(!o.opra?.available)html+=feedCompUnavailable("OPRA",o.opra?.reason);}
   if(out)out.innerHTML=html||`<div class="note">No comparable data returned.</div>`;
 }catch(e){if(st)st.textContent="Comparison failed — "+(e?.message||"network error");}
}
document.getElementById("feedCompRun")?.addEventListener("click",runFeedComparison);
document.getElementById("feedCompTicker")?.addEventListener("keydown",e=>{if(e.key==="Enter")runFeedComparison();});

function fmtCompact(n){
 const x=Number(n); if(!Number.isFinite(x)) return "—";
 const a=Math.abs(x);
 if(a>=1e9)return (x/1e9).toFixed(a>=1e10?1:2)+"B";
 if(a>=1e6)return (x/1e6).toFixed(a>=1e7?1:2)+"M";
 if(a>=1e3)return (x/1e3).toFixed(a>=1e4?1:2)+"K";
 return Math.round(x).toLocaleString();
}
let sectorData=[],currentSector=null,earnResults=[],liveStockData=[],liveSearchData=[],liveSearchSector=null,liveSearchLoading=false,sectorRequestSeq=0,previewTicker=null,previewPeriod="1m",previewRequestSeq=0;
let globalTopSetupData=[],automaticTopSetupsRunning=false,automaticTopSetupsLastRun=0;

let previewVPMode="auto",previewPayload=null,previewTimeframe="1d";
let sectorRRGMode="fast", sectorQuadrantFilter="all", dashboardPayload=null, dashboardHeatMode="composite";
const clientCache={market:null,sectors:new Map(),historical:new Map()};
function cacheKeySector(etf,limit){return `${etf}|${limit}`}
function cacheKeyHistory(mode,etf,date,limit){return `${mode}|${etf}|${date}|${limit}`}



let institutionalRadarResults=[];
function instMoney(v){v=Number(v||0);if(v>=1e9)return "$"+(v/1e9).toFixed(1)+"B";if(v>=1e6)return "$"+(v/1e6).toFixed(1)+"M";if(v>=1e3)return "$"+(v/1e3).toFixed(0)+"K";return "$"+v.toFixed(0)}
function renderInstitutionalRadar(){
 const body=document.getElementById("institutionalRadarRows");if(!body)return;
 const good=institutionalRadarResults.filter(x=>x.ok).slice(0,6);
 if(!good.length){body.innerHTML=`<tr><td colspan="7" class="note">No qualifying activity returned from this scan.</td></tr>`;return}
 body.innerHTML=good.map((x,i)=>{
   const m=x.rotation||{}, mult=Number(x.largest_multiple||0), cls=mult>=2?"instHot":mult>=1.25?"instWarm":"instMuted";
   const why=[];if(mult>=1.5)why.push(`${mult.toFixed(1)}× sampled baseline`);if((x.repeat_days||0)>=2)why.push(`${x.repeat_days} active sessions`);if((m.stage||0)>=3)why.push("confirmed rotation");if((m.tail||"")==="Rotating In")why.push("tail rotating in");
   const mainRow=`<tr class="clickrow" data-inst-open="${x.ticker}"><td>${i+1}</td><td><b>${x.ticker}</b><div class="tiny">${m.etf||""}</div></td><td><span class="institutionalScore">${Number(x.composite_score||0).toFixed(1)}/10</span><div class="tiny">activity ${Number(x.activity_score||0).toFixed(1)}/10</div></td><td><span class="instPrint ${cls}">${instMoney(x.largest_print)}</span><div class="tiny">${mult.toFixed(1)}× prior sampled largest · ${x.sampled_trades||0} trades sampled</div></td><td>${x.repeat_days||0}/4 sessions<div class="tiny">large-print persistence</div></td><td>${m.stage||0}/4 · ${m.quadrant||"—"}<div class="tiny">${m.tail||"—"} · opportunity ${m.opportunity||0}/10</div></td><td>${why.join(" · ")||"Large-print activity under review"}<div class="tiny">Click to open chart/options</div></td></tr>`;
   // Deterministic (not AI-generated) context for genuine spikes only.
   const contextRow=(x.context&&x.context.length)?`<tr class="instContextRow"><td></td><td colspan="6"><div class="tiny darkPoolContext">${x.context.map(n=>`<div>· ${n}</div>`).join("")}</div></td></tr>`:"";
   return mainRow+contextRow;
 }).join("");
 body.querySelectorAll("[data-inst-open]").forEach(row=>row.addEventListener("click",()=>openSectorStockTicker(row.dataset.instOpen,{scroll:true})));
}
async function runInstitutionalRadar(){
 const st=document.getElementById("institutionalRadarStatus"),btn=document.getElementById("runInstitutionalRadar");
 // Was previously limited to whatever single ETF/group happened to be
 // currently loaded in the drill-down view. Broadened to the same all-sectors
 // candidate pool Top Setups and Early Turn Watch already reuse, so this
 // scans across every supportive sector at once instead of one at a time.
 const rows=(window.allSupportiveCandidates||[]).slice().sort((a,b)=>opportunityScore(b)-opportunityScore(a)).slice(0,12);
 if(!rows.length){if(st)st.textContent="Run Top Setups first to build the candidate pool.";return}
 const symbols=rows.map(x=>x.ticker),meta={};rows.forEach(x=>{const rs=rotationStage(x);meta[x.ticker]={etf:x._parentTicker||currentSector||"",quadrant:x.fast?.quadrant||x.quadrant||"",tail:effectiveTailSignal(x)||x.tail_trajectory||"",stage:rs.level||0,opportunity:opportunityScore(x)}});
 if(btn)btn.disabled=true;if(st)st.textContent=`Scanning ${symbols.length} candidates across all supportive sectors…`;
 try{
   const r=await fetch("/api/institutional-radar",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({symbols,meta})});
   const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||`Scan failed (${r.status})`);
   institutionalRadarResults=j.results||[];renderInstitutionalRadar();
   const ok=institutionalRadarResults.filter(x=>x.ok).length;if(st)st.textContent=`${ok}/${symbols.length} candidates analyzed across all supportive sectors`;
   const d=document.getElementById("institutionalRadarDisclosure");if(d&&j.disclosure)d.textContent=j.disclosure;
 }catch(e){if(st)st.innerHTML=`<span class="error">${e.message}</span>`}finally{if(btn)btn.disabled=false}
}



function newsTime(ts){
 if(!ts)return "";const d=new Date(Number(ts)*1000);if(Number.isNaN(d.getTime()))return "";
 const m=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getMonth()];
 let h=d.getHours(),ap=h>=12?"PM":"AM";h=h%12||12;return `${m} ${d.getDate()} · ${h}:${String(d.getMinutes()).padStart(2,"0")} ${ap}`;
}
function newsLink(x){
 const esc=v=>String(v||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
 const safe=esc(x?.headline||"");const u=String(x?.url||"").trim();
 const ok=/^https?:\/\//i.test(u);
 return ok?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer" class="newsHeadline">${safe}</a>`:`<span class="newsHeadline">${safe}</span>`;
}
function currentNewsSymbols(){
 const seen=[];
 const add=t=>{t=String(t||"").toUpperCase();if(t&&!seen.includes(t))seen.push(t)};
 (window.currentTopSetupRows||[]).forEach(x=>add(x?.x?.ticker||x?.ticker));
 (earlyTurnWatchData||[]).forEach(x=>add(x?.ticker));
 (institutionalRadarResults||[]).filter(x=>x.ok).forEach(x=>add(x?.ticker));
 (window.allSupportiveCandidates||[]).slice().sort((a,b)=>opportunityScore(b)-opportunityScore(a)).slice(0,8).forEach(x=>add(x?.ticker));
 return seen.slice(0,8);
}
async function runNewsContext(){
 const st=document.getElementById("newsContextStatus"),btn=document.getElementById("runNewsContext");if(btn)btn.disabled=true;if(st)st.textContent="Loading current news…";
 try{
   const symbols=currentNewsSymbols();
   const safeSymbols=(symbols||[]).map(normalizeStockTicker).filter(isSafeStockTicker).slice(0,8);
   const newsUrl="/api/news-context"+(safeSymbols.length?`?symbols=${safeSymbols.map(x=>encodeURIComponent(x)).join(",")}`:"");
   let r;
   try{r=await window.fetch(newsUrl)}catch(e){throw new Error(`News request dispatch failed: ${e?.name||"Error"}: ${e?.message||e}`)}
   const raw=await r.text();let j={};
   try{j=raw?JSON.parse(raw):{}}catch(e){throw new Error(`News response unreadable (${r.status})`)}
   if(!r.ok||!j.ok)throw Error(j.error||`News failed (${r.status})`);
   const m=document.getElementById("marketNewsRows");
   if(m)m.innerHTML=(j.market||[]).slice(0,6).map(x=>`<div class="newsItem">${newsLink(x)}<div class="newsMeta">${x.source||""}${newsTime(x.datetime)?" · "+newsTime(x.datetime):""}</div></div>`).join("")||`<div class="newsItem">No current market headlines returned by the configured news feed.</div>`;
   const c=document.getElementById("setupNewsRows"),blocks=[];
   (symbols||[]).forEach(t=>{const rows=(j.company?.[t]||[]).slice(0,2);rows.forEach(x=>blocks.push(`<div class="newsItem"><div><span class="newsTicker">${t}</span><span class="newsCat">${x.category||"company"}</span></div>${newsLink(x)}<div class="newsMeta">${x.source||""}${newsTime(x.datetime)?" · "+newsTime(x.datetime):""}</div><div class="newsWhy"><b>Why it matters:</b> ${x.why||"Relevant context for the current setup."}</div></div>`))});
   if(c)c.innerHTML=blocks.join("")||`<div class="newsItem">No ticker-specific headlines returned for the current candidate pool.</div>`;
   if(st)st.textContent=`${(j.market||[]).length} market headlines · ${blocks.length} setup headlines`;
 }catch(e){if(st)st.innerHTML=`<span class="error">${e.message}</span>`}finally{if(btn)btn.disabled=false}
}
setTimeout(()=>{const b=document.getElementById("runNewsContext");if(b&&!b.dataset.bound){b.dataset.bound="1";b.addEventListener("click",runNewsContext)}},0);

const LIVE_WATCHLIST_KEY="marketRotationLiveWatchlistV1";
let liveWatchlist=[];

function loadLiveWatchlist(){
 try{
   const raw=localStorage.getItem(LIVE_WATCHLIST_KEY);
   liveWatchlist=raw?JSON.parse(raw):[];
   if(!Array.isArray(liveWatchlist))liveWatchlist=[];
 }catch(e){liveWatchlist=[]}
 syncWatchlistFromServer();
}

async function syncWatchlistFromServer(){
 try{
   let r=await fetch("/api/watchlist"),j=await r.json();
   if(!j?.ok||!Array.isArray(j.items))return;
   if(!j.items.length&&liveWatchlist.length){
     await Promise.all(liveWatchlist.map(x=>fetch("/api/watchlist",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticker:x.ticker,added_price:x.added_price??null})}).catch(()=>null)));
     r=await fetch("/api/watchlist");j=await r.json();if(!j?.ok||!Array.isArray(j.items))return;
   }
   const oldByTicker=new Map(liveWatchlist.map(x=>[liveWatchKey(x.ticker),x]));
   liveWatchlist=j.items.map(row=>({...oldByTicker.get(liveWatchKey(row.ticker)),ticker:row.ticker,added_at:row.added_at,added_price:row.added_price}));
   try{localStorage.setItem(LIVE_WATCHLIST_KEY,JSON.stringify(liveWatchlist))}catch(e){}
   renderLiveWatchlist();refreshLiveBookmarkButtons();
 }catch(e){}
}

function saveLiveWatchlist(){
 try{localStorage.setItem(LIVE_WATCHLIST_KEY,JSON.stringify(liveWatchlist))}catch(e){}
 renderLiveWatchlist();
 refreshLiveBookmarkButtons();
}

function normalizeStockTicker(raw){
 const t=String(raw||"").trim().toUpperCase();
 // Normalize common class-share separator for market-data endpoints.
 // Keep ^ only for index symbols; stock holdings should not contain it.
 return t.replace(/\//g,".").replace(/\s+/g,"");
}
function isSafeStockTicker(raw){
 const t=normalizeStockTicker(raw);
 return /^[A-Z0-9.^-]{1,20}$/.test(t);
}
function safeTickerEndpoint(path,ticker,query=""){
 const sym=normalizeStockTicker(ticker);
 if(!isSafeStockTicker(sym))throw new Error(`Invalid ticker symbol: ${sym||ticker}`);
 const p=String(path||"").startsWith("/")?String(path):`/${String(path||"")}`;
 return `${p}/${encodeURIComponent(sym)}${query||""}`;
}
function safeTickerUrl(path,ticker,params={}){
 // Safari has intermittently thrown a DOMException ("The string did not match the expected pattern")
 // before dispatching chart requests when a prebuilt query string is passed through the generic helper.
 // Build the query from known scalar values and keep the final URL same-origin and relative.
 const base=safeTickerEndpoint(path,ticker);
 const q=[];
 Object.entries(params||{}).forEach(([k,v])=>{
   if(v===undefined||v===null)return;
   q.push(`${encodeURIComponent(String(k))}=${encodeURIComponent(String(v))}`);
 });
 return q.length?`${base}?${q.join("&")}`:base;
}
const tickerRequestInflight=new Map(),tickerResponseCache=new Map();
async function safeTickerFetchJson(path,ticker,params={},opts={}){
 const url=safeTickerUrl(path,ticker,params),ttl=Number(opts.ttl||0),now=Date.now();
 const cached=tickerResponseCache.get(url);
 if(ttl>0&&cached&&now-cached.at<ttl)return cached.value;
 if(tickerRequestInflight.has(url))return tickerRequestInflight.get(url);
 const promise=(async()=>{
   const waits=[0,1000,3000,7000,12000];
   let lastErr=null;
   for(let attempt=0;attempt<waits.length;attempt++){
     if(waits[attempt])await new Promise(r=>setTimeout(r,waits[attempt]));
     let r;
     try{r=await window.fetch(url,{method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}})}
     catch(e){lastErr=new Error(`Request could not be dispatched: ${e?.message||e}`);continue;}
     let raw="",j={};
     try{raw=await r.text();j=raw?JSON.parse(raw):{};}
     catch(e){
       lastErr=new Error(`Service returned an unreadable response (${r.status})`);
       if([429,502,503,504].includes(r.status))continue;
       throw lastErr;
     }
     if(r.ok&&j?.ok){
       if(ttl>0)tickerResponseCache.set(url,{at:Date.now(),value:j});
       return j;
     }
     lastErr=new Error(j?.error||`Request failed (${r.status})`);
     if(![429,502,503,504].includes(r.status))throw lastErr;
   }
   const stale=tickerResponseCache.get(url);
   if(stale)return {...stale.value,_client_stale:true};
   throw lastErr||new Error("Request failed");
 })();
 tickerRequestInflight.set(url,promise);
 try{return await promise}finally{tickerRequestInflight.delete(url)}
}
async function openSectorStockTicker(rawTicker,{scroll=true}={}){
 const ticker=normalizeStockTicker(rawTicker);
 const st=document.getElementById("sstatus");
 if(!isSafeStockTicker(ticker)){
   if(st)st.textContent=`Could not open ticker: ${ticker||rawTicker}`;
   return false;
 }
 try{
   // Update selection first so the UI responds immediately.
   toggleRRGFocus("stockChart",ticker);
   syncLiveRowSelection();
   if(activeOptionsData?.ticker && activeOptionsData.ticker!==ticker){
     activeOptionsData=null;
     const ost=document.getElementById("optionsStatus");
     if(ost)ost.textContent=`Loading ${ticker} options…`;
   }

   // Load each data module independently so one failure cannot block the others.
   const tasks=[
     Promise.resolve(loadChartPreview(ticker)).catch(e=>console.warn(`${ticker} chart failed`,e)),
     Promise.resolve(loadStrat(ticker)).catch(e=>console.warn(`${ticker} STRAT failed`,e))
   ];
   if(alpacaConfigured!==false){
     tasks.push(Promise.resolve(loadOptionsTicker(ticker,{scroll:false})).catch(e=>console.warn(`${ticker} options failed`,e)));
   }
   if(scroll){
     setTimeout(()=>{
       const el=document.getElementById("stockDeepDiveAnchor")||document.getElementById("pricePreviewChart");
       safeScrollIntoView(el)
     },25);
   }
   await Promise.allSettled(tasks);
   return true;
 }catch(e){
   console.error("Sector ticker open failed",e);
   if(st)st.textContent=`${ticker} load failed: ${e?.message||e}`;
   return false;
 }
}

function liveWatchKey(ticker){
 return String(ticker||"").toUpperCase();
}

function isLiveWatched(ticker){
 return liveWatchlist.some(x=>liveWatchKey(x.ticker)===liveWatchKey(ticker));
}

function liveBookmarkButtonHTML(ticker){
 const saved=isLiveWatched(ticker);
 return `<button class="bookmarkBtn ${saved?"saved":""}" data-live-bookmark="${ticker}" title="${saved?"Remove from watchlist":"Add to watchlist"}">${saved?"★":"☆"}</button>`;
}

function currentLiveWatchItem(x){
 const opt=optionScanMap[x.ticker]||{};
 const spot=(activeOptionsData?.ticker===x.ticker?activeOptionsData.spot:opt.spot);
 return {
   ticker:x.ticker,
   etf:currentSector||"—",
   fast:x.fast?.quadrant||x.quadrant||"—",
   trend:x.trend?.quadrant||"—",
   tail:effectiveTailSignal(x)||x.tail_trajectory||"—",
   stage:rotationStage(x).label,
   stage_level:rotationStage(x).level,
   added_price:spot??null,
   current_price:spot??null,
   iv_state:opt.iv_state||null,
   liquidity:opt.liquidity||null,
   opportunity:opportunityScore(x),
   added_at:new Date().toISOString()
 };
}

function toggleLiveWatch(item){
 if(!item||!item.ticker)return;
 const key=liveWatchKey(item.ticker);
 const i=liveWatchlist.findIndex(x=>liveWatchKey(x.ticker)===key);
 if(i>=0){liveWatchlist.splice(i,1);fetch(`/api/watchlist/${encodeURIComponent(item.ticker)}`,{method:"DELETE"}).catch(()=>{});}
 else{liveWatchlist.unshift(item);fetch("/api/watchlist",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticker:item.ticker,added_price:item.added_price??null})}).catch(()=>{});}
 saveLiveWatchlist();
}

function refreshLiveBookmarkButtons(){
 document.querySelectorAll("[data-live-bookmark]").forEach(btn=>{
   const saved=isLiveWatched(btn.dataset.liveBookmark);
   btn.textContent=saved?"★":"☆";
   btn.classList.toggle("saved",saved);
   btn.title=saved?"Remove from watchlist":"Add to watchlist";
 });
}

function renderLiveWatchlist(){
 const rows=document.getElementById("liveWatchRows");
 if(!rows)return;
 if(!liveWatchlist.length){
   rows.innerHTML=`<tr><td colspan="9"><span class="note">No saved tickers yet. Click ☆ beside a live RRG ticker.</span></td></tr>`;
 }else{
   rows.innerHTML=liveWatchlist.map(x=>{
     const ret=(x.added_price!=null&&x.current_price!=null)?(x.current_price/x.added_price-1)*100:null;
     const added=x.added_at?new Date(x.added_at).toLocaleDateString("en-US",{month:"short",day:"numeric"}):"—";
     return `<tr class="clickrow" data-watch-open="${x.ticker}">
     <td><button class="bookmarkBtn saved" data-live-watch-remove="${x.ticker}" title="Remove">★</button></td>
     <td><b>${x.ticker}</b></td>
     <td>${x.etf||"—"}</td>
     <td>${added}<div class="tiny">${x.added_price==null?"price pending":"$"+fmt(x.added_price,2)}</div></td>
     <td>${x.current_price==null?"—":"$"+fmt(x.current_price,2)}</td>
     <td class="${ret==null?"":ret>=0?"up":"down"}">${ret==null?"—":pct(ret)}</td>
     <td>${x.stage_level?`${x.stage_level}/4 · `:""}${x.stage||x.fast||"—"}<div class="tiny">${x.tail||"—"}</div></td>
     <td>${x.liquidity||"—"}<div class="tiny">${x.iv_state||"IV pending"}</div></td>
     <td>${x.opportunity==null?"—":x.opportunity+"/10"}</td>
   </tr>`}).join("");

   document.querySelectorAll("[data-watch-open]").forEach(row=>row.addEventListener("click",evt=>{
     if(evt.target.closest("[data-live-watch-remove]"))return;
     const t=row.dataset.watchOpen;if(!t)return;
     // Use the same isolated, stale-safe opener as RRG rows and heat-map tiles.
     Promise.resolve(openSectorStockTicker(t,{scroll:true}))
       .catch(e=>console.warn("Watchlist ticker open failed",e));
   }));
   document.querySelectorAll("[data-live-watch-remove]").forEach(btn=>btn.addEventListener("click",()=>{
     const ticker=btn.dataset.liveWatchRemove,key=liveWatchKey(ticker);
     liveWatchlist=liveWatchlist.filter(x=>liveWatchKey(x.ticker)!==key);
     saveLiveWatchlist();
     fetch(`/api/watchlist/${encodeURIComponent(ticker)}`,{method:"DELETE"}).then(()=>syncWatchlistFromServer()).catch(()=>{});
   }));
 }
 const st=document.getElementById("liveWatchStatus");
 if(st)st.textContent=`${liveWatchlist.length} saved`;
}
async function refreshLiveWatchlistData(){
 const st=document.getElementById("liveWatchStatus");
 if(!liveWatchlist.length){if(st)st.textContent="No saved tickers.";return}
 if(alpacaConfigured===false){if(st)st.textContent="Connect Alpaca to refresh watchlist prices/options.";return}
 const btn=document.getElementById("refreshLiveWatchlist");if(btn)btn.disabled=true;
 if(st)st.textContent=`Refreshing ${liveWatchlist.length} saved tickers…`;
 try{
   const symbols=liveWatchlist.map(x=>x.ticker);
   const r=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols})});
   const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Refresh failed");
   (j.results||[]).forEach(o=>{
     if(!o.ticker||o.ok===false)return;
     optionScanMap[o.ticker]=o;
     const i=liveWatchlist.findIndex(x=>liveWatchKey(x.ticker)===liveWatchKey(o.ticker));
     if(i<0)return;
     if(liveWatchlist[i].added_price==null)liveWatchlist[i].added_price=o.spot??null;
     liveWatchlist[i].current_price=o.spot??liveWatchlist[i].current_price??null;
     liveWatchlist[i].iv_state=o.iv_state||null;
     liveWatchlist[i].liquidity=o.liquidity||null;
     const lx=liveStockData.find(x=>x.ticker===o.ticker);
     if(lx){
       liveWatchlist[i].stage=rotationStage(lx).label;
       liveWatchlist[i].stage_level=rotationStage(lx).level;
       liveWatchlist[i].tail=effectiveTailSignal(lx)||lx.tail_trajectory||liveWatchlist[i].tail;
       liveWatchlist[i].opportunity=opportunityScore(lx);
     }
   });
   saveLiveWatchlist();
   if(st)st.textContent=`${liveWatchlist.length} saved · refreshed`;
 }catch(e){if(st)st.innerHTML=`<span class="error">${e.message}</span>`}
 finally{if(btn)btn.disabled=false}
}

const quadColors={Leading:"#22c55e",Improving:"#38bdf8",Lagging:"#ef4444",Weakening:"#f59e0b"};
function fmt(v,n=2){return(v==null||!isFinite(v))?"—":Number(v).toFixed(n)}
function pct(v){return(v==null)?"—":(v>=0?"+":"")+fmt(v,2)+"%"}
function badge(q){return q?`<span class="badge ${q}">${q.toUpperCase()}</span>`:"—"}
function dir(r){if(!r||!r.ticker)return"—";let s=`<span class="${r.rs_up?'up':'down'}">RS-Ratio ${r.rs_up?'↑':'↓'}</span> · <span class="${r.mom_up?'up':'down'}">RS-Momentum ${r.mom_up?'↑':'↓'}</span>`;if(r.l_to_i)s+=' <span class="flag">L→I</span>';if(r.early_turn)s+=' <span class="flag">EARLY TURN</span>';return s}
const rrgFocusState={};

function rrgModeRow(r,mode="fast"){
 const src=(mode==="trend"?(r?.trend||r?.fast||r):(r?.fast||r))||{};
 return {...r,...src,fast:r?.fast||src,trend:r?.trend||null,alignment:r?.alignment};
}
function rrgRowsForChart(id,rows){
 if(id!=="sectorChart")return rows||[];
 return (rows||[]).map(r=>rrgModeRow(r,sectorRRGMode));
}
function quadrantClass(q){return q?`quad${q}`:""}
function selectedInterpretation(x){
 const f=x?.fast||{},t=x?.trend||{};
 if(f.quadrant==="Improving" && f.rs_up && f.mom_up && (!t.quadrant || t.quadrant==="Lagging" || t.quadrant==="Weakening"))return "Early rotation — fast improving ahead of the broader trend.";
 if((f.quadrant==="Leading"||f.quadrant==="Improving") && (t.quadrant==="Leading"||t.quadrant==="Improving"))return "Aligned — fast and trend both support relative leadership.";
 if((f.quadrant==="Weakening"||f.quadrant==="Lagging") && t.quadrant==="Leading")return "Pullback inside a stronger long-term relative trend.";
 if(f.quadrant==="Lagging" && t.quadrant==="Lagging")return "Weak on both horizons — low-priority until momentum turns.";
 return `${x?.alignment||"Mixed"} — compare fast change with trend persistence.`;
}
function updateSelectedSectorCard(ticker){
 const el=document.getElementById("selectedSectorCard"); if(!el)return;
 const x=(sectorData||[]).find(r=>r.ticker===ticker);
 if(!x){el.innerHTML='<div><div class="sscLabel">Selected</div><div class="sscValue">Click a sector</div></div><div><div class="sscLabel">Fast 10/5</div><div class="sscValue">—</div></div><div><div class="sscLabel">Trend 25/12</div><div class="sscValue">—</div></div><div><div class="sscLabel">Interpretation</div><div class="sscInterp">Fast finds the turn; Trend checks whether it is persisting.</div></div>';return;}
 const f=x.fast||{},t=x.trend||{};
 el.innerHTML=`<div><div class="sscLabel">Selected</div><div class="sscValue">${x.ticker}</div><div class="tiny">${x.name||x.group||""}</div></div>
 <div><div class="sscLabel">Fast 10/5</div><div class="sscValue ${quadrantClass(f.quadrant)}">${f.quadrant||"—"} ${f.rs_up&&f.mom_up?"↗":""}</div><div class="tiny">RS ${f.x==null?"—":fmt(f.x,1)} · Mom ${f.y==null?"—":fmt(f.y,1)}</div></div>
 <div><div class="sscLabel">Trend 25/12</div><div class="sscValue ${quadrantClass(t.quadrant)}">${t.quadrant||"—"} ${t.rs_up&&t.mom_up?"↗":""}</div><div class="tiny">RS ${t.x==null?"—":fmt(t.x,1)} · Mom ${t.y==null?"—":fmt(t.y,1)}</div></div>
 <div><div class="sscLabel">Interpretation</div><div class="sscInterp">${selectedInterpretation(x)}</div></div>`;
}
function setSectorRRGMode(mode){
 sectorRRGMode=mode==="trend"?"trend":"fast";
 ["rrgFastBtn"].forEach(id=>document.getElementById(id)?.classList.toggle("active",sectorRRGMode==="fast"));
 ["rrgTrendBtn"].forEach(id=>document.getElementById(id)?.classList.toggle("active",sectorRRGMode==="trend"));
 const ttl=document.getElementById("dashboardRRGTitle");if(ttl)ttl.textContent=sectorRRGMode==="fast"?"RRG LIVE · FAST ROTATION (10/5)":"RRG LIVE · TREND (25/12)";
 renderGroups();
}
function sparklineSVG(x,mode="fast"){
 const src=mode==="trend"?(x?.trend||{}):(x?.fast||x||{}), pts=src.tail||[];
 if(pts.length<2)return "";
 const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys); const w=72,h=16;
 const path=pts.map((p,i)=>{const px=(p.x-xmin)/Math.max(.001,xmax-xmin)*(w-2)+1,py=h-1-(p.y-ymin)/Math.max(.001,ymax-ymin)*(h-2);return `${i?"L":"M"}${px.toFixed(1)},${py.toFixed(1)}`}).join(" ");
 return `<svg class="miniSpark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${path}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
}
function _rrgCanvasContext(c){
 const rect=c.getBoundingClientRect();
 const W=Math.max(320,Math.round(rect.width||c.clientWidth||900));
 const H=Math.max(280,Math.round(rect.height||c.clientHeight||600));
 const dpr=Math.min(2.5,Math.max(1,window.devicePixelRatio||1));
 const bw=Math.max(1,Math.round(W*dpr)),bh=Math.max(1,Math.round(H*dpr));
 if(c.width!==bw||c.height!==bh){c.width=bw;c.height=bh;}
 const ctx=c.getContext("2d");
 ctx.setTransform(dpr,0,0,dpr,0,0);
 return {ctx,W,H};
}
function drawRRG(id,rows,focusTicker=undefined){
 rows=rrgRowsForChart(id,rows);
 const c=document.getElementById(id),cv=_rrgCanvasContext(c),ctx=cv.ctx,W=cv.W,H=cv.H,p=48;
 rrgFocusState[id]=rrgFocusState[id]||{selected:null,rows:[],hits:[]};
 const state=rrgFocusState[id];
 if(focusTicker!==undefined)state.selected=focusTicker;
 state.rows=rows||[];
 state.hits=[];

 ctx.clearRect(0,0,W,H);
 ctx.fillStyle="#0d1217";
 ctx.fillRect(0,0,W,H);

 if(!rows||!rows.length){
   ctx.fillStyle="#8b95a5";
   ctx.fillText("No data yet",p,p);
   return;
 }

 let xs=[],ys=[];
 rows.forEach(r=>(r.tail||[]).forEach(pt=>{if(Number.isFinite(Number(pt.x)))xs.push(Number(pt.x));if(Number.isFinite(Number(pt.y)))ys.push(Number(pt.y));}));
 // Keep 100/100 exactly centered and use ONE pixels-per-RRG-unit scale for
 // both axes. This preserves tail angles and quadrant geometry instead of
 // stretching X and Y independently to fill the panel.
 const dx=Math.max(2,...xs.map(v=>Math.abs(v-100)))+.65;
 const dy=Math.max(2,...ys.map(v=>Math.abs(v-100)))+.65;
 const plotW=Math.max(1,W-2*p),plotH=Math.max(1,H-2*p);
 const unitsPerPx=Math.max((2*dx)/plotW,(2*dy)/plotH);
 const halfX=unitsPerPx*plotW/2,halfY=unitsPerPx*plotH/2;
 const xmin=100-halfX,xmax=100+halfX,ymin=100-halfY,ymax=100+halfY;
 const X=x=>p+(x-xmin)/(xmax-xmin)*plotW,
       Y=y=>H-p-(y-ymin)/(ymax-ymin)*plotH,
       cx=X(100),cy=Y(100);

 ctx.strokeStyle="#475569";
 ctx.lineWidth=1;
 ctx.globalAlpha=1;
 ctx.beginPath();
 ctx.moveTo(cx,p);ctx.lineTo(cx,H-p);
 ctx.moveTo(p,cy);ctx.lineTo(W-p,cy);
 ctx.stroke();

 ctx.fillStyle="#64748b";
 ctx.font="10px sans-serif";
 ctx.fillText("IMPROVING",p+6,p+14);
 ctx.fillText("LEADING",W-p-50,p+14);
 ctx.fillText("LAGGING",p+6,H-p-8);
 ctx.fillText("WEAKENING",W-p-70,H-p-8);

 // RRG axes: horizontal = relative strength, vertical = relative momentum.
 ctx.save();
 ctx.globalAlpha=.9;
 ctx.fillStyle="#94a3b8";
 ctx.font="bold 11px sans-serif";
 ctx.textAlign="center";
 ctx.fillText("RELATIVE STRENGTH (RS)  →",W/2,H-10);

 ctx.translate(13,H/2);
 ctx.rotate(-Math.PI/2);
 ctx.fillText("RELATIVE MOMENTUM  →",0,0);
 ctx.restore();

 // Small direction cues around the 100/100 reference cross.
 ctx.save();
 ctx.fillStyle="#64748b";
 ctx.font="9px sans-serif";
 ctx.textAlign="left";
 ctx.fillText("weaker RS",p+4,cy-7);
 ctx.textAlign="right";
 ctx.fillText("stronger RS",W-p-4,cy-7);
 ctx.save();
 ctx.translate(cx+9,p+30);
 ctx.rotate(-Math.PI/2);
 ctx.textAlign="right";
 ctx.fillText("stronger momentum",0,0);
 ctx.restore();
 ctx.save();
 ctx.translate(cx+9,H-p-28);
 ctx.rotate(-Math.PI/2);
 ctx.textAlign="left";
 ctx.fillText("weaker momentum",0,0);
 ctx.restore();
 ctx.restore();

 const selected=state.selected;

 // Draw non-selected tails first so the selected path stays visually on top.
 const ordered=selected
   ? [...rows.filter(r=>r.ticker!==selected),...rows.filter(r=>r.ticker===selected)]
   : rows;

 ordered.forEach(r=>{
   let pts=r.tail||[];
   if(!pts.length)return;

   const isSelected=selected===r.ticker;
   const basket=(id==="sectorChart")?activeMacroBasket():null;
   const outsideBasket=!!(basket && !basket.has(r.ticker));
   const isFaded=(selected && !isSelected)||outsideBasket;
   const color=quadColors[r.quadrant];

   ctx.strokeStyle=color;
   ctx.lineWidth=1.65;
   ctx.globalAlpha=isFaded?.06:(isSelected?1:.72);
   ctx.beginPath();
   pts.forEach((pt,j)=>{
     j?ctx.lineTo(X(pt.x),Y(pt.y)):ctx.moveTo(X(pt.x),Y(pt.y));
   });
   ctx.stroke();

   let last=pts[pts.length-1],
       ex=X(last.x),ey=Y(last.y);

   ctx.globalAlpha=isFaded?.16:1;
   ctx.fillStyle=color;
   ctx.beginPath();
   ctx.arc(ex,ey,5,0,Math.PI*2);
   ctx.fill();

   const label=r.ticker;
   ctx.font="bold 11px sans-serif";
   const labelX=ex+(isSelected?10:7);
   const labelY=ey-(isSelected?8:6);
   const labelWidth=ctx.measureText(label).width;
   ctx.globalAlpha=isFaded?.18:1;
   ctx.fillStyle=color;
   ctx.fillText(label,labelX,labelY);

   // Make both the endpoint and ticker label clickable/tappable.
   state.hits.push({
     ticker:r.ticker,
     x1:Math.min(ex-10,labelX-4),
     y1:Math.min(ey-12,labelY-18),
     x2:Math.max(ex+10,labelX+labelWidth+6),
     y2:Math.max(ey+12,labelY+8)
   });
 });

 ctx.globalAlpha=1;
 ctx.lineWidth=1;
}



function toggleRRGFocus(id,ticker){
 const state=rrgFocusState[id];
 if(!state)return;
 const next=state.selected===ticker?null:ticker;
 drawRRG(id,state.rows,next);
 if(id==="sectorChart"){syncSectorRowSelection();updateSelectedSectorCard(next||currentSector);}
 if(id==="stockChart")syncLiveRowSelection();
 if(id==="historyChart")syncHistoricalRowSelection();
}


function syncSectorRowSelection(){
 const selected=rrgFocusState["sectorChart"]?.selected||null;
 document.querySelectorAll("[data-sector]").forEach(r=>{
   r.classList.toggle("selectedSectorRow",selected===r.dataset.sector);
 });
}

function syncLiveRowSelection(){
 const selected=rrgFocusState["stockChart"]?.selected||null;
 document.querySelectorAll("[data-live-ticker]").forEach(r=>{
   r.classList.toggle("selectedLiveRow",selected===r.dataset.liveTicker);
 });
}

function syncHistoricalRowSelection(){
 const selected=rrgFocusState["historyChart"]?.selected||null;
 document.querySelectorAll("[data-hist-ticker]").forEach(r=>{
   r.classList.toggle("selectedHistRow",selected===r.dataset.histTicker);
 });
}

function installRRGInteractions(id){
 const c=document.getElementById(id);
 if(!c || c.dataset.rrgInteractive==="1")return;
 c.dataset.rrgInteractive="1";

 function canvasPoint(evt){
   const rect=c.getBoundingClientRect();
   const clientX=evt.touches?evt.touches[0].clientX:evt.clientX;
   const clientY=evt.touches?evt.touches[0].clientY:evt.clientY;
   return {
     x:(clientX-rect.left)*(c.width/rect.width),
     y:(clientY-rect.top)*(c.height/rect.height)
   };
 }

 function hitTicker(evt){
   const state=rrgFocusState[id];
   if(!state)return null;
   const pt=canvasPoint(evt);
   // Reverse so visually top-most labels/endpoints win overlapping clicks.
   return [...(state.hits||[])].reverse().find(h=>
     pt.x>=h.x1&&pt.x<=h.x2&&pt.y>=h.y1&&pt.y<=h.y2
   )?.ticker||null;
 }

 c.addEventListener("click",evt=>{
   const ticker=hitTicker(evt);
   if(!ticker)return;
   toggleRRGFocus(id,ticker);
   // Stock RRG clicks are inspection-only: focus the selected tail and dim the
   // others in place. Opening the chart/volume-profile deep dive remains a
   // separate action via the stock table/watchlist, so clicking the RRG no
   // longer yanks the user away from the tail they are trying to inspect.
   if(id==="sectorChart"){
     syncSectorRowSelection();
     selectSector(ticker,{source:"rrg"});
   }
 });

 c.addEventListener("mousemove",evt=>{
   c.style.cursor=hitTicker(evt)?"pointer":"default";
 });

 c.addEventListener("mouseleave",()=>{
   c.style.cursor="default";
 });
}

installRRGInteractions("sectorChart");
installRRGInteractions("stockChart");
installRRGInteractions("historyChart");

function installStockSummaryFocusOnly(){
 document.addEventListener("click",evt=>{
   const target=evt.target;
   if(!target||!target.closest)return;
   if(target.closest("button,a,input,select,textarea"))return;
   const row=target.closest("[data-live-ticker]");
   if(!row)return;
   evt.preventDefault();
   evt.stopImmediatePropagation();
   const ticker=row.dataset.liveTicker;
   if(ticker)toggleRRGFocus("stockChart",ticker);
 },true);
}
installStockSummaryFocusOnly();
const MACRO_BASKETS={
 rate:new Set(["XLK","XLC","XLU","XLRE","XLP"]),
 cyclical:new Set(["XLY","XLI","XLF","XLB"]),
 defensive:new Set(["XLV","XLP","XLU"]),
 inflation:new Set(["XLE","XLB"])
};
function activeMacroBasket(){
 const key=document.getElementById("macroBasketFilter")?.value||"all";
 return key==="all"?null:MACRO_BASKETS[key];
}
function filteredGroups(){
 let f=document.getElementById("groupFilter")?.value||"all";
 return sectorData.filter(x=>{
   const gok=f==="all"||(f==="core"&&x.group==="Core Sector")||(f==="industry"&&x.group==="Industry / Theme");
   const q=sectorRRGMode==="trend"?(x.trend?.quadrant):(x.fast?.quadrant||x.quadrant);
   const qok=sectorQuadrantFilter==="all"||q===sectorQuadrantFilter;
   return gok&&qok;
 });
}
function renderGroups(){
 let data=filteredGroups();

 // Clear sector focus if the selected ETF is hidden by the current group filter.
 const sectorState=rrgFocusState["sectorChart"];
 if(sectorState?.selected && !data.some(x=>x.ticker===sectorState.selected)){
   sectorState.selected=null;
 }

 drawRRG("sectorChart",data);
 document.getElementById("sectorRows").innerHTML=data.map((x,k)=>`<tr class="clickrow sectorTickerRow ${activeMacroBasket()&&!activeMacroBasket().has(x.ticker)?"macroDim":""}" data-sector="${x.ticker}"><td>${k+1}</td><td><b>${x.ticker}</b><div class="tiny">${x.name} · ${x.group}</div></td><td>${compactRRG(x.fast)}</td><td>${compactRRG(x.trend)}</td><td>${alignBadge(x.alignment)}</td></tr>`).join("");

 document.querySelectorAll("[data-sector]").forEach(el=>el.addEventListener("click",async()=>{
   const t=el.dataset.sector;
   toggleRRGFocus("sectorChart",t);
   await selectSector(t,{source:"rrg"});
 }));

 syncSectorRowSelection();
 updateSelectedSectorCard(rrgFocusState["sectorChart"]?.selected||currentSector);
 const dsel=document.getElementById("dashboardSectorSelect");if(dsel&&currentSector&&[...dsel.options].some(o=>o.value===currentSector))dsel.value=currentSector;
}

async function auditHoldings(){
 const p=document.getElementById("auditPanel");p.style.display="block";p.textContent="Checking issuer holdings feeds…";
 try{
   let r=await fetch("/api/holdings-audit"),j=await r.json();if(!j.ok)throw Error(j.error||"Audit failed");
   p.innerHTML=`<div class="scroll"><table><thead><tr><th>ETF</th><th>Holdings loaded</th><th>Source</th><th>Status</th></tr></thead><tbody>${j.results.map(x=>`<tr><td><b>${x.etf}</b><div class="tiny">${x.name}</div></td><td>${x.count}</td><td>${x.source}</td><td>${!x.ok?"⚠️ "+(x.error||"failed"):(x.partial?"⚠️ PARTIAL":"✓ FULL")}</td></tr>`).join("")}</tbody></table></div>`;
 }catch(e){p.innerHTML=`<span class="error">${e.message}</span>`}
}
function applyMarketPayload(j,fromCache=false){
 sectorData=j.sectors||[];
 const st=document.getElementById("mstatus");
 if(st) st.textContent=fromCache?`Cached · through ${j.asof||"—"}`:(j.stale?`Refresh source unavailable — showing last good data through ${j.asof}`:`Through ${j.asof}`);
 const i=j.internals||{};

 const arrow=v=>v==null?"→":v>0?"↑":v<0?"↓":"→";
 const tone=v=>v==null?"":v>0?"up":v<0?"down":"";
 const valPct=v=>v==null?"—":pct(v);

 const regime=document.getElementById("regimeSummary");
 const riskScoreDisplay=j.risk_score==null?"—":`${Math.max(0,Math.min(4,Math.round((j.risk_score+4)/2)))}/4`;
 if(regime){
   regime.innerHTML=`<b>${j.risk_appetite||"Mixed"}</b> · Participation: <b>${j.participation||"—"}</b> · <button type="button" class="riskScoreInline" id="riskScoreInline">Risk support <b>${riskScoreDisplay}</b></button>`;
 }
 const rb=document.getElementById("riskSupportBreakdown");
 if(rb){
   const riskParts=[
     ["Breadth",i.RSP?.d5,"RSP / SPY"],
     ["Small caps",i.IWM?.d5,"IWM / SPY"],
     ["Growth",i.QQQ?.d5,"QQQ / SPY"],
     ["Credit",i.CREDIT?.d5,"HYG / LQD"]
   ];
   rb.innerHTML=riskParts.map(([label,v,detail])=>{
     const known=v!=null,good=known&&Number(v)>0,bad=known&&Number(v)<0;
     const mark=!known?"—":good?"✓":"✕";
     const state=!known?"neutral":good?"good":"bad";
     const move=!known?"—":`${Number(v)>=0?"+":""}${fmt(Number(v),2)}% / 5d`;
     return `<div class="riskPart ${state}"><span class="riskMark">${mark}</span><div><b>${label}</b><small>${detail}</small></div><strong>${move}</strong></div>`;
   }).join("");
 }

 document.getElementById("internals").innerHTML=`
   <div class="card"><div class="tiny">SPY TREND</div><b>${valPct(i.SPY?.d5)}</b><div class="tiny">${valPct(i.SPY?.d20)} / 20d</div></div>
   <div class="card"><div class="tiny">RSP/SPY · BREADTH</div><b>${arrow(i.RSP?.d5)} ${valPct(i.RSP?.d5)}</b><div class="tiny">${valPct(i.RSP?.d20)} / 20d</div></div>
   <div class="card"><div class="tiny">IWM/SPY · SMALL CAPS</div><b>${arrow(i.IWM?.d5)} ${valPct(i.IWM?.d5)}</b><div class="tiny">${valPct(i.IWM?.d20)} / 20d</div></div>
   <div class="card"><div class="tiny">QQQ/SPY · GROWTH</div><b>${arrow(i.QQQ?.d5)} ${valPct(i.QQQ?.d5)}</b><div class="tiny">${valPct(i.QQQ?.d20)} / 20d</div></div>
   <div class="card"><div class="tiny">HYG/LQD · CREDIT</div><b>${arrow(i.CREDIT?.d5)} ${valPct(i.CREDIT?.d5)}</b><div class="tiny">${valPct(i.CREDIT?.d20)} / 20d</div></div>
   <div class="card"><div class="tiny">10Y TREASURY YIELD</div><b>${i.TNX?.value==null?"—":fmt(i.TNX.value,2)+"%"}</b><div class="tiny">${arrow(i.TNX?.d5)} ${valPct(i.TNX?.d5)} / 5d · ${valPct(i.TNX?.d20)} / 20d</div></div>`;
 renderGroups();
 renderHeatMap();
 renderDashboardMarket(j);
 if("requestIdleCallback" in window)requestIdleCallback(()=>runAutomaticTopSetups(false),{timeout:3000});
 else setTimeout(()=>runAutomaticTopSetups(false),1800);
}

function fmtDashValue(v,d=2){return v==null?"—":Number(v).toFixed(d)}
function renderDashboardMarket(j){
 dashboardPayload=j;const i=j?.internals||{},ov=document.getElementById("dashboardMarketOverview"),up=document.getElementById("dashboardUpdated"),br=document.getElementById("dashboardBreadth");
 if(up)up.textContent=`Through ${j?.asof||"—"}`;
 const quote=(sym,obj,changeKey="raw_d1")=>{const c=obj?.[changeKey]??obj?.d1,cls=c==null?"":c>=0?"up":"down";return `<div class="marketQuote"><div class="sym">${sym}</div><div class="px">${fmtDashValue(obj?.value,2)}</div><div class="chg ${cls}">${c==null?"—":`${c>=0?"+":""}${fmt(c,2)}%`}</div></div>`};
 if(ov)ov.innerHTML=quote("SPY",i.SPY,"d1")+quote("QQQ",i.QQQ)+quote("IWM",i.IWM)+quote("VIX",i.VIX,"d5");
 const cls=v=>v==null?"":v>=0?"up":"down",pctv=v=>v==null?"—":`${v>=0?"+":""}${fmt(v,2)}%`;
 if(br)br.innerHTML=`<div class="breadthRow"><div class="name">RSP / SPY · breadth</div><div class="val">${pctv(i.RSP?.d5)}</div><div class="move ${cls(i.RSP?.d5)}">5d</div></div><div class="breadthRow"><div class="name">IWM / SPY · small caps</div><div class="val">${pctv(i.IWM?.d5)}</div><div class="move ${cls(i.IWM?.d5)}">5d</div></div><div class="breadthRow"><div class="name">HYG / LQD · credit</div><div class="val">${pctv(i.CREDIT?.d5)}</div><div class="move ${cls(i.CREDIT?.d5)}">5d</div></div><div class="breadthRow"><div class="name">10Y Treasury yield</div><div class="val">${i.TNX?.value==null?"—":fmt(i.TNX.value,2)+"%"}</div><div class="move ${cls(i.TNX?.d5)}">${pctv(i.TNX?.d5)}</div></div>`;
 renderDashboardHeat();populateDashboardSectorSelect();
}
function dashboardHeatScore(x){
 if(dashboardHeatMode==="fast")return Math.max(0,Math.min(10,rotationStage({...x,trend:{}}).level*2+(x.fast?.tail_trajectory==="Rotating In"?1:0)));
 if(dashboardHeatMode==="trend"){
   const t=x.trend||{},base={Leading:9,Improving:7,Weakening:3,Lagging:1}[t.quadrant]??5;
   return Math.max(0,Math.min(10,base+(t.rs_up&&t.mom_up?1:0)-(t.rs_up===false&&t.mom_up===false?1:0)));
 }
 return sectorHeatScore(x);
}
function renderDashboardHeat(){
 const g=document.getElementById("dashboardHeatGrid");if(!g)return;const rows=(sectorData||[]).filter(x=>x.group==="Core Sector").sort((a,b)=>dashboardHeatScore(b)-dashboardHeatScore(a));
 g.innerHTML=rows.map(x=>{const sc=dashboardHeatScore(x),q=dashboardHeatMode==="trend"?(x.trend?.quadrant||"—"):(x.fast?.quadrant||x.quadrant);return `<div class="dashHeatTile ${heatTone(sc)} ${currentSector===x.ticker?"selected":""}" data-dash-sector="${x.ticker}"><div><div class="sym">${x.ticker}</div><div class="score">${sc.toFixed(1)}</div><div class="state">${q||"—"}</div></div>${sparklineSVG(x,dashboardHeatMode==="trend"?"trend":"fast")}</div>`}).join("");
 document.querySelectorAll("[data-dash-sector]").forEach(el=>el.addEventListener("click",async()=>{const t=el.dataset.dashSector;toggleRRGFocus("sectorChart",t);await selectSector(t,{source:"dashboard"});document.querySelectorAll("[data-dash-sector]").forEach(n=>n.classList.toggle("selected",n.dataset.dashSector===t));}));
}
function populateDashboardSectorSelect(){const sel=document.getElementById("dashboardSectorSelect");if(!sel)return;const prev=sel.value||currentSector||"";const rows=(sectorData||[]).filter(x=>x.group==="Core Sector");sel.innerHTML='<option value="">Choose sector…</option>'+rows.map(x=>`<option value="${x.ticker}">${x.ticker} · ${x.name}</option>`).join("");if(prev&&[...sel.options].some(o=>o.value===prev))sel.value=prev;}

async function loadMarket(force=false){
 const st=document.getElementById("mstatus");
 if(force)automaticTopSetupsLastRun=0;
 if(!force&&clientCache.market){applyMarketPayload(clientCache.market,true);return}
 if(st)st.textContent="Updating…";
 try{
   const r=await fetch("/api/market"+(force?"?refresh=1":""));
   const j=await r.json();
   if(!j.ok)throw Error(j.error);
   clientCache.market=j;
   applyMarketPayload(j,false);
 }catch(e){
   if(st)st.innerHTML=`<span class="error">Refresh failed: ${e.message}. Existing results were kept; wait a minute and retry.</span>`;
   const du=document.getElementById("dashboardUpdated");
   if(du)du.innerHTML=`<span class="error">Data load failed: ${e.message}</span>`;
   console.error("Market load failed",e);
 }
}

function applySectorPayload(j,fromCache=false,expectedSector=null){
 if(expectedSector && expectedSector!==currentSector)return false;
 liveStockData=j.results||[];
 const st=document.getElementById("sstatus");
 st.textContent=(fromCache?"Cached · ":"")+(j.holdings_stale?"Holdings refresh unavailable — using last good list · ":"")+`${j.holdings_as_screened} of ${j.holdings_total} holdings · ${j.holdings_source||"source unknown"} · through ${j.asof||"—"}`;
 renderLiveStocks();
 return true;
}

async function loadSector(force=false,throwOnError=false){
 // Event listeners can pass an Event object as the first argument. Only a literal
 // true should force a refresh; this keeps sector clicks deterministic across browsers.
 force=(force===true);
 if(!currentSector)return false;
 const requestedSector=String(currentSector||"").trim().toUpperCase();
 if(!/^[A-Z0-9.^-]{1,12}$/.test(requestedSector)){
   const err=new Error(`Invalid sector symbol: ${requestedSector}`);
   if(throwOnError)throw err;
   const st=document.getElementById("sstatus");if(st)st.innerHTML=`<span class="error">${err.message}</span>`;
   return false;
 }
 currentSector=requestedSector;
 const requestId=++sectorRequestSeq;

 if(liveSearchSector && liveSearchSector!==requestedSector){
   liveSearchData=[];
   liveSearchSector=null;
 }

 // Clear the previous sector immediately so stale rows are never displayed
 // while a newly selected ETF is loading.
 liveStockData=[];
 const stockState=rrgFocusState["stockChart"];
 if(stockState)stockState.selected=null;
 renderLiveStocks();

 const st=document.getElementById("sstatus");
 const lim=document.getElementById("liveHoldingsLimit").value;
 const key=cacheKeySector(requestedSector,lim);
 document.getElementById("sectorTitle").textContent=requestedSector;

 if(!force&&clientCache.sectors.has(key)){
   if(requestId!==sectorRequestSeq || requestedSector!==currentSector)return;
   applySectorPayload(clientCache.sectors.get(key),true,requestedSector);
   return;
 }

 st.textContent=`Updating ${requestedSector}…`;
 try{
   // Keep this high-frequency Safari path maximally boring: a relative ASCII URL
   // and default fetch options. Avoid WebKit URL/Request overload edge cases.
   const url=`/api/sector/${encodeURIComponent(requestedSector)}?limit=${encodeURIComponent(String(lim))}`;
   const r=await fetch(url);
   const j=await r.json();
   if(!j.ok)throw Error(j.error);

   // Cache the response under the ETF that actually initiated it.
   clientCache.sectors.set(key,j);

   // If the user changed sectors while this request was in flight,
   // do not let the late response overwrite the newly selected ETF.
   if(requestId!==sectorRequestSeq || requestedSector!==currentSector)return;

   applySectorPayload(j,false,requestedSector);
   return true;
 }catch(e){
   if(requestId!==sectorRequestSeq || requestedSector!==currentSector)return false;
   const msg=(e&&e.message)?e.message:String(e);
   st.innerHTML=`<span class="error">${msg}</span>`;
   if(throwOnError)throw e;
   return false;
 }
}

async function selectSector(ticker,{source="ui",scrollToStocks=false,force=false}={}){
 const t=String(ticker||"").trim().toUpperCase();
 if(!t)return false;
 currentSector=t;
 updateSelectedSectorCard(t);renderDashboardHeat();
 liveSearchData=[];liveSearchSector=null;
 const search=document.getElementById("liveTickerSearch");if(search)search.value="";
 const sel=document.getElementById("coreSectorSelect");if(sel&&[...sel.options].some(o=>o.value===t))sel.value=t;
 const title=document.getElementById("sectorTitle");if(title)title.textContent=t+" selected";
 const hs=document.getElementById("heatStatus");if(source==="heat"&&hs)hs.textContent=`Loading ${t} holdings…`;
 document.querySelectorAll("[data-heat-sector]").forEach(n=>n.classList.toggle("selected",n.dataset.heatSector===t));
 const ok=await loadSector(force,source==="heat");
 if(ok){
   renderHeatMap();
   if(source==="heat"&&hs)hs.textContent=`${t} loaded · click a stock tile to open its chart/positioning`;
   if(scrollToStocks)setTimeout(()=>{safeScrollIntoView(document.getElementById("stockHeatTitle"))},50);
 }
 return ok;
}


function potentialTurnFromTail(x){
  // Early, pre-confirmation RRG hook:
  // still Lagging at the endpoint, but recent tail reverses from deterioration
  // into rightward / northeast improvement. This is a WATCH signal, not confirmation.
  const pts = x.tail || x.points || x.rrg_tail || [];
  const endpointQuadrant = x.quadrant || x.fast?.quadrant || "";
  if(endpointQuadrant !== "Lagging" || !Array.isArray(pts) || pts.length < 4) return false;

  const xy = p => {
    if(Array.isArray(p)) return [Number(p[0]), Number(p[1])];
    return [
      Number(p.x ?? p.rs_ratio ?? p.ratio ?? p.rs ?? p["RS-Ratio"]),
      Number(p.y ?? p.rs_momentum ?? p.momentum ?? p.mom ?? p["RS-Momentum"])
    ];
  };
  const P = pts.map(xy).filter(p=>Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if(P.length < 4) return false;

  const n=P.length, a=P[n-4], b=P[n-3], c=P[n-2], d=P[n-1];
  const oldDx = c[0]-a[0], oldDy = c[1]-a[1];
  const newDx = d[0]-c[0], newDy = d[1]-c[1];

  // Current segment must be decisively rightward and at least flat-to-up.
  const recentImproving = newDx > 0 && newDy > -0.10*Math.abs(newDx);

  // Previous path must show deterioration: leftward and/or downward.
  const priorDeteriorating = oldDx < 0 || oldDy < 0;

  // Require a real directional hook, not tiny noise.
  const oldMag=Math.hypot(oldDx,oldDy), newMag=Math.hypot(newDx,newDy);
  if(oldMag===0 || newMag===0) return false;
  const cos=(oldDx*newDx+oldDy*newDy)/(oldMag*newMag);
  const meaningfulHook = cos < 0.75; // > ~41° direction change

  return recentImproving && priorDeteriorating && meaningfulHook;
}

function effectiveTailSignal(x){
  if(potentialTurnFromTail(x)) return "Potential Turn";
  return x.tail_trajectory || "";
}

function baseTailBadge(x){
 if(!x||!x.tail_trajectory||x.tail_trajectory==="Neutral")return "—";
 if(x.tail_trajectory==="Rotating In")return '<span class="flag">↗ ROTATING IN</span>';
 if(x.tail_trajectory==="Rotating Out")return '<span class="flag">↙ ROTATING OUT</span>';
 return "—";
}
function tailBadge(x){
  if(potentialTurnFromTail(x)) return `<span class="potentialTurnBadge">👀 POTENTIAL TURN</span>`;
  return baseTailBadge(x);
}


function filteredLiveStocks(){
 const q=document.getElementById("liveQuadrantFilter")?.value||"all";
 const t=document.getElementById("liveTailFilter")?.value||"all";
 const s=(document.getElementById("liveTickerSearch")?.value||"").trim().toUpperCase();
 const source=(s && liveSearchSector===currentSector && liveSearchData.length)?liveSearchData:liveStockData;
 return source.filter(x=>{
   const qok=q==="all"||x.quadrant===q;
   const tok=t==="all"||effectiveTailSignal(x)===t;
   const sok=!s||String(x.ticker||"").toUpperCase().includes(s)||String(x.name||"").toUpperCase().includes(s);
   return qok&&tok&&sok;
 });
}


let liveSearchTimer=null;

async function ensureLiveSearchUniverse(){
 const input=document.getElementById("liveTickerSearch");
 const term=(input?.value||"").trim();
 if(!term){
   renderLiveStocks();
   return;
 }
 if(!currentSector){
   const st=document.getElementById("sstatus");
   if(st)st.textContent=`Finding ${term.toUpperCase()} across all sectors & groups…`;
   try{
     const r=await fetch(`/api/ticker-search?q=${encodeURIComponent(term)}`);
     const j=await r.json();
     if(!r.ok||!j.ok)throw Error(j.error||"Global ticker search failed");
     const exact=(j.matches||[]).find(x=>x.ticker===term.toUpperCase());
     const target=exact||((j.matches||[]).length===1?j.matches[0]:null);
     if(target){
       currentSector=target.etf;
       const sel=document.getElementById("coreSectorSelect");
       if(sel)sel.value=target.etf;
       liveSearchData=[];
       liveSearchSector=null;
       document.getElementById("sectorTitle").textContent=`${target.etf} · ${target.group_name}`;
       if(st)st.textContent=`Found ${target.ticker} in ${target.etf} · ${target.group_name}. Loading RRG + options…`;
       await loadSector();
       await ensureLiveSearchUniverse();
     }else if((j.matches||[]).length){
       const choices=j.matches.slice(0,6).map(x=>`${x.ticker} → ${x.etf}`).join(" · ");
       if(st)st.textContent=`Multiple matches: ${choices}. Type the exact ticker.`;
     }else{
       if(st)st.textContent=`No ticker/name match found across the available sector & industry groups.`;
     }
   }catch(e){
     if(st)st.innerHTML=`<span class="error">Global search failed: ${e.message}</span>`;
   }
   return;
 }
 if(liveSearchSector===currentSector && liveSearchData.length){
   renderLiveStocks();
   return;
 }
 if(liveSearchLoading)return;

 liveSearchLoading=true;
 const st=document.getElementById("sstatus");
 if(st)st.textContent=`Searching all ${currentSector} holdings…`;

 try{
   const key=cacheKeySector(currentSector,"all");
   let j=clientCache.sectors.get(key);
   if(!j){
     const sectorSym=normalizeStockTicker(currentSector);
     if(!isSafeStockTicker(sectorSym))throw Error(`Invalid sector symbol: ${sectorSym}`);
     const r=await fetch(`/api/sector/${encodeURIComponent(sectorSym)}?limit=all`);
     j=await r.json();
     if(!r.ok||!j.ok)throw Error(j.error||"Search lookup failed");
     clientCache.sectors.set(key,j);
   }
   liveSearchData=j.results||[];
   liveSearchSector=currentSector;
   const matches=filteredLiveStocks();
   if(st)st.textContent=matches.length
     ? `${matches.length} match${matches.length===1?"":"es"} across all ${j.holdings_total||liveSearchData.length} ${currentSector} holdings`
     : `No match found across all ${j.holdings_total||liveSearchData.length} ${currentSector} holdings`;
   renderLiveStocks();
   // If search resolves to an exact ticker (or a single match), focus it on the RRG.
   const exact=matches.find(x=>String(x.ticker||"").toUpperCase()===term.toUpperCase());
   const target=exact||(matches.length===1?matches[0]:null);
   if(target){
     const state=rrgFocusState["stockChart"]||(rrgFocusState["stockChart"]={selected:null});
     state.selected=target.ticker;
     renderLiveStocks();
     loadChartPreview(target.ticker);

     // Ticker-search workflow: once a single stock is resolved, automatically
     // load its options chain so the user does not need to click Analyze Ticker.
     if(alpacaConfigured!==false){
       loadOptionsTicker(target.ticker,{scroll:false});
     }
   }
 }catch(e){
   if(st)st.innerHTML=`<span class="error">Search failed: ${e.message}</span>`;
 }finally{
   liveSearchLoading=false;
 }
}

function handleLiveTickerSearch(){
 clearTimeout(liveSearchTimer);
 const term=(document.getElementById("liveTickerSearch")?.value||"").trim();
 if(!term){
   renderLiveStocks();
   return;
 }
 // Fast local response first; then expand to all ETF holdings after a short debounce.
 renderLiveStocks();
 liveSearchTimer=setTimeout(ensureLiveSearchUniverse,250);
}


let alpacaConfigured=null;

async function checkAlpacaStatus(){
 const box=document.getElementById("alpacaSetupBox");
 try{
   const r=await fetch("/api/diagnostics");
   const j=await r.json();
   alpacaConfigured=!!(j.alpaca&&j.alpaca.configured);
   if(box){
     const signup=document.getElementById("alpacaSignupBtn");
     if(alpacaConfigured){
       box.classList.add("ready");
       box.innerHTML='<b>✓ Alpaca connected</b><span class="note">Options screening is ready. Dealer positioning loads with each ticker; institutional flow loads on demand for faster analysis.</span>';
       if(signup)signup.style.display="none";
     }else{
       box.classList.remove("ready");
       if(signup)signup.style.display="";
     }
   }
 }catch(e){
   alpacaConfigured=null;
 }
}

function safeScrollIntoView(el,{smooth=false}={}){
 if(!el)return false;
 try{
   // iOS Safari/WebKit has repeatedly thrown the opaque DOMException
   // “The string did not match the expected pattern” on the options overload.
   // Use only the legacy Boolean overload; CSS handles scroll behavior.
   el.scrollIntoView(true);
   return true;
 }catch(e){return false}
}
function focusOptionsPanel(){
 const panel=document.getElementById("optionsPanel");
 safeScrollIntoView(panel,{smooth:true});
}

let optionScanMap={},activeOptionsData=null;

function optionBadgeHTML(x){
 if(!x)return '<span class="tiny">Not scanned</span>';
 if(x.error||x.ok===false)return '<span class="optBadge optBad">Error</span>';
 const liq=x.liquidity||"—",label=x.execution_label||liq,cls=liq==="Liquid"?"optGood":liq==="Tradable"?"optWarn":"optBad";
 return `<span class="optBadge ${cls}">${label}</span><div class="tiny">${x.iv_state||"—"}</div>`;
}
function dteFromExpiration(exp){
 if(!exp)return null;const a=new Date(exp+"T12:00:00"),b=new Date();
 return Math.max(0,Math.round((a-b)/86400000));
}

function optionScanScore(x){
 if(!x||x.ok===false||x.error)return -999;
 const liq={Liquid:3,Tradable:2,Thin:0}[x.liquidity]||0;
 const iv={ "Cheap / Crushed":3, "Normal":2, "Elevated":0, "Juiced":-2, "Unknown":0 }[x.iv_state]||0;
 const contracts=Math.min(3,(x.liquid_contracts||0)/3)+Math.min(2,(x.tradable_contracts||0)/5);
 return liq*3+iv*2+contracts;
}

function renderOptionsScanResults(results){
 const section=document.getElementById("optionsScanSection");
 const body=document.getElementById("optionsScanRows");
 if(!section||!body)return;
 const arr=(results||[]).slice().sort((a,b)=>optionScanScore(b)-optionScanScore(a));
 section.style.display="block";
 body.innerHTML=arr.map((x,k)=>{
   if(x.ok===false||x.error){
     return `<tr><td>${k+1}</td><td><b>${x.ticker||"—"}</b></td><td colspan="6"><span class="error">${x.error||"Scan failed"}</span></td></tr>`;
   }
   return `<tr>
     <td>${k+1}</td>
     <td><b>${x.ticker}</b><div class="tiny">$${x.spot==null?"—":fmt(x.spot,2)}</div></td>
     <td>${optionBadgeHTML(x)}</td>
     <td>${x.atm_iv==null?"—":fmt(x.atm_iv,1)+"%"}</td>
     <td>${x.iv_state||"—"}</td>
     <td>${x.iv_rv_ratio==null?"—":fmt(x.iv_rv_ratio,2)}</td>
     <td>${x.liquid_contracts??0}</td>
     <td>${x.tradable_contracts??0}</td>
   </tr>`;
 }).join("");
}


function formatOptionDate(exp){
 if(!exp)return "—";
 const d=new Date(exp+"T12:00:00");
 return d.toLocaleDateString("en-US",{month:"short",day:"numeric"});
}

function optionTypeShort(t){
 if(!t)return "";
 const s=String(t).toLowerCase();
 return s.startsWith("c")?"C":s.startsWith("p")?"P":"";
}

function optionTypeWord(t){
 const s=String(t||"").toLowerCase();
 return s.startsWith("c")?"Call":s.startsWith("p")?"Put":String(t||"");
}

function readableContractHTML(r,underlying){
 const date=formatOptionDate(r.expiration);
 const strike=r.strike==null?"—":fmt(r.strike,2).replace(/\.00$/,"");
 const word=optionTypeWord(r.type);
 const root=underlying||activeOptionsData?.ticker||"";
 return `<div class="contractPrimary"><b>${root}</b> · ${date} · <b>$${strike} ${word}</b></div>
         <div class="occSymbol">${r.symbol||""}</div>`;
}


function moneyShort(v){
 if(v==null||!isFinite(Number(v)))return "—";v=Number(v);const a=Math.abs(v);
 if(a>=1e9)return "$"+fmt(v/1e9,2)+"B";if(a>=1e6)return "$"+fmt(v/1e6,2)+"M";if(a>=1e3)return "$"+fmt(v/1e3,1)+"K";return "$"+fmt(v,0);
}
let gammaLandscapeHitboxes=[];
let selectedGammaStrike=null;
function gexSigned(v){
 if(v==null||!isFinite(Number(v)))return "—";const n=Number(v);return `${n>=0?"+":"-"}${moneyShort(Math.abs(n))}`;
}
function drawGammaLandscape(p,spot){
 const c=document.getElementById("gammaLandscape"),ctx=c?.getContext("2d");if(!c||!ctx)return;
 const rows=(p?.landscape_levels||p?.levels||[]).filter(x=>Number.isFinite(Number(x.strike))).sort((a,b)=>a.strike-b.strike);
 const W=c.width,H=c.height;ctx.clearRect(0,0,W,H);ctx.fillStyle="#071018";ctx.fillRect(0,0,W,H);gammaLandscapeHitboxes=[];
 if(!rows.length||!spot){ctx.fillStyle="#94a3b8";ctx.font="13px sans-serif";ctx.fillText("No modeled strike landscape available",24,32);return}
 const pad={l:86,r:86,t:38,b:40};
 const strikeW=66,netW=82,centerW=strikeW+netW,mid=W/2,strikeL=mid-centerW/2,strikeR=strikeL+strikeW,netR=mid+centerW/2;
 const plotL=pad.l,plotR=W-pad.r,rowH=(H-pad.t-pad.b)/rows.length;
 const maxCall=Math.max(1,...rows.map(x=>Math.abs(Number(x.call_gex)||0))),maxPut=Math.max(1,...rows.map(x=>Math.abs(Number(x.put_gex)||0)));
 const leftMax=strikeL-plotL-8,rightMax=plotR-netR-8;
 ctx.textBaseline="middle";
 // headers
 ctx.font="bold 11px sans-serif";ctx.textAlign="left";ctx.fillStyle="#4ade80";ctx.fillText("CALL GEX",plotL,18);
 ctx.textAlign="center";ctx.fillStyle="#94a3b8";ctx.fillText("STRIKE",strikeL+strikeW/2,18);ctx.fillText("NET GEX",strikeR+netW/2,18);
 ctx.textAlign="right";ctx.fillStyle="#f87171";ctx.fillText("PUT GEX",plotR,18);
 rows.forEach((r,i)=>{
   const y=pad.t+(i+.5)*rowH,call=Math.abs(Number(r.call_gex)||0),put=Math.abs(Number(r.put_gex)||0),net=Number(r.net_gex)||0;
   const cw=call/maxCall*leftMax,pw=put/maxPut*rightMax;
   const isSelected=selectedGammaStrike!=null&&Math.abs(Number(r.strike)-Number(selectedGammaStrike))<1e-6;
   if(isSelected){ctx.fillStyle="rgba(59,130,246,.09)";ctx.fillRect(plotL,y-rowH/2,plotR-plotL,rowH)}
   ctx.globalAlpha=.22;ctx.strokeStyle="#1f3242";ctx.beginPath();ctx.moveTo(plotL,y);ctx.lineTo(plotR,y);ctx.stroke();ctx.globalAlpha=1;
   // value labels at edges
   ctx.font="10px sans-serif";ctx.textAlign="left";ctx.fillStyle="#4ade80";ctx.fillText(moneyShort(call),plotL,y);
   ctx.textAlign="right";ctx.fillStyle="#f87171";ctx.fillText(`(${moneyShort(put)})`,plotR,y);
   // bars
   ctx.globalAlpha=.78;ctx.fillStyle="#21b95c";ctx.fillRect(strikeL-cw,y-rowH*.32,cw,Math.max(2,rowH*.64));
   ctx.fillStyle="#e4474f";ctx.fillRect(netR,y-rowH*.32,pw,Math.max(2,rowH*.64));ctx.globalAlpha=1;
   // central ladder
   if(isSelected){ctx.fillStyle="#24344a";ctx.fillRect(strikeL+4,y-rowH*.42,strikeW-8,rowH*.84);ctx.strokeStyle="#60a5fa";ctx.strokeRect(strikeL+4,y-rowH*.42,strikeW-8,rowH*.84)}
   ctx.font="11px sans-serif";ctx.textAlign="center";ctx.fillStyle="#e2e8f0";ctx.fillText(`$${Number(r.strike).toFixed(r.strike<100?1:0)}`,strikeL+strikeW/2,y);
   ctx.fillStyle=net>=0?"#4ade80":"#f87171";ctx.fillText(`${net>=0?"+":""}${moneyShort(net)}`,strikeR+netW/2,y);
   gammaLandscapeHitboxes.push({x:plotL,y:y-rowH/2,w:plotR-plotL,h:rowH,row:r});
 });
 const minS=Number(rows[0].strike),maxS=Number(rows[rows.length-1].strike);
 // IMPORTANT: the landscape is a categorical strike ladder. Map overlays to
 // the same row centers, interpolating only between adjacent displayed strikes.
 // This keeps spot/walls/flip aligned even when strike spacing is irregular.
 const strikeCenters=rows.map((r,i)=>({s:Number(r.strike),y:pad.t+(i+.5)*rowH}));
 const yForStrike=v=>{
   const n=Number(v); if(!Number.isFinite(n)||n<minS||n>maxS)return null;
   for(let i=0;i<strikeCenters.length;i++){
     if(Math.abs(n-strikeCenters[i].s)<1e-9)return strikeCenters[i].y;
     if(i<strikeCenters.length-1 && n>strikeCenters[i].s && n<strikeCenters[i+1].s){
       const a=strikeCenters[i],b=strikeCenters[i+1],t=(n-a.s)/(b.s-a.s);
       return a.y+t*(b.y-a.y);
     }
   }
   return strikeCenters[strikeCenters.length-1].y;
 };
 function rail(v,color,label,dash=[],side="left"){
   if(v==null||v<minS||v>maxS)return;const y=yForStrike(v);if(y==null)return;ctx.save();ctx.setLineDash(dash);ctx.strokeStyle=color;ctx.lineWidth=1.55;ctx.beginPath();ctx.moveTo(plotL,y);ctx.lineTo(plotR,y);ctx.stroke();ctx.setLineDash([]);
   const txt=`${label} $${Number(v).toFixed(2)}`;ctx.font="bold 9px sans-serif";const tw=ctx.measureText(txt).width;const bx=side==="left"?6:W-tw-18,by=Math.max(25,Math.min(H-20,y-15));ctx.fillStyle="rgba(7,16,24,.92)";ctx.fillRect(bx-4,by-8,tw+8,16);ctx.fillStyle=color;ctx.textAlign="left";ctx.fillText(txt,bx,by);ctx.restore();
 }
 rail(spot,"#f8fafc","SPOT",[],"left");
 rail(p.modeled_flip,"#a78bfa","GAMMA FLIP",[5,5],"left");
 rail(p.call_wall,"#22c55e","CALL WALL",[5,4],"left");
 rail(p.put_wall,"#f59e0b","PUT WALL",[5,4],"right");
}
function inspectGammaLandscape(evt){
 const c=document.getElementById("gammaLandscape"),d=document.getElementById("gammaLevelDetail");if(!c||!d)return;
 const rect=c.getBoundingClientRect(),sx=c.width/rect.width,sy=c.height/rect.height,x=(evt.clientX-rect.left)*sx,y=(evt.clientY-rect.top)*sy;
 const h=gammaLandscapeHitboxes.find(b=>x>=b.x&&x<=b.x+b.w&&y>=b.y&&y<=b.y+b.h);if(!h)return;
 const r=h.row;selectedGammaStrike=r.strike;drawGammaLandscape(activeOptionsData?.positioning,activeOptionsData?.spot);
 d.innerHTML=`<b>Selected $${fmt(r.strike,2)}</b><span>Call GEX <b class="positive">${moneyShort(r.call_gex)}</b></span><span>Put GEX <b class="negative">${moneyShort(r.put_gex)}</b></span><span>Net <b class="${r.net_gex>=0?'positive':'negative'}">${gexSigned(r.net_gex)}</b></span><span>Call OI <b>${(r.call_oi||0).toLocaleString()}</b></span><span>Put OI <b>${(r.put_oi||0).toLocaleString()}</b></span>`;
}
function renderGexRail(p,spot){
 const rows=(p?.landscape_levels||p?.levels||[]).slice();
 const totalCall=Number(p.total_call_gex??rows.reduce((a,x)=>a+(Number(x.call_gex)||0),0));
 const totalPut=Number(p.total_put_gex??rows.reduce((a,x)=>a+(Number(x.put_gex)||0),0));
 const net=Number(p.net_gex)||0,ratio=Math.abs(totalPut)>0?totalCall/Math.abs(totalPut):null;
 const flip=p.modeled_flip,spotVsFlip=spot&&flip?((spot/flip-1)*100):null;
 const s=document.getElementById('gexSummary');if(s)s.innerHTML=`
   <div class="gexStatRow"><span>Total Call GEX</span><b class="positive">${moneyShort(totalCall)}</b></div>
   <div class="gexStatRow"><span>Total Put GEX</span><b class="negative">${moneyShort(totalPut)}</b></div>
   <div class="gexStatRow"><span>Net GEX (Call − Put)</span><b class="${net>=0?'positive':'negative'}">${gexSigned(net)}</b></div>
   <div class="gexStatRow"><span>Call / Put Ratio</span><b>${ratio==null?'—':fmt(ratio,2)}</b></div>
   <div class="gexStatRow"><span>Regime</span><b class="${net>=0?'positive':'negative'}">${p.gamma_regime||'—'}</b></div>
   <div class="gexStatRow"><span>Spot vs Flip</span><b>${spotVsFlip==null?'—':`${spotVsFlip>=0?'+':''}${fmt(spotVsFlip,1)}%`}</b></div>`;
 const key=document.getElementById('gexKeyLevels');if(key)key.innerHTML=`
   <div class="gexLevelRow"><i class="gexSwatch call"></i><span>Call Wall (Upside Ceiling)</span><b>${p.call_wall==null?'—':'$'+fmt(p.call_wall,2)}</b></div>
   <div class="gexLevelRow"><i class="gexSwatch flip"></i><span>Gamma Flip (Transition)</span><b>${p.modeled_flip==null?'—':'$'+fmt(p.modeled_flip,2)}</b></div>
   <div class="gexLevelRow"><i class="gexSwatch put"></i><span>Put Wall (Downside Floor)</span><b>${p.put_wall==null?'—':'$'+fmt(p.put_wall,2)}</b></div>
   <div class="gexLevelRow"><i class="gexSwatch spot"></i><span>Spot / Regime</span><b>${spot==null?'—':'$'+fmt(spot,2)}</b></div>`;
 const top=(p.levels||rows).slice().sort((a,b)=>Math.abs(Number(b.net_gex)||0)-Math.abs(Number(a.net_gex)||0)).slice(0,5),mx=Math.max(1,...top.map(x=>Math.abs(Number(x.net_gex)||0)));
 const lg=document.getElementById('gexLargest');if(lg)lg.innerHTML=top.map(x=>{const n=Number(x.net_gex)||0,w=Math.max(5,Math.abs(n)/mx*100);return `<div class="gexLargestRow"><b>$${fmt(x.strike,0)}</b><div class="gexMiniBar"><span class="${n>=0?'positive':'negative'}" style="width:${w}%"></span></div><b class="${n>=0?'positive':'negative'}">${gexSigned(n)}</b></div>`}).join('');
 const le=document.getElementById('gexLegend');if(le)le.innerHTML=`
   <div class="gexLegendRow"><i class="gexLegendBox"></i><span>Call GEX (modeled support)</span></div>
   <div class="gexLegendRow"><i class="gexLegendBox put"></i><span>Put GEX (modeled pressure)</span></div>
   <div class="gexLegendRow"><i class="gexLegendLine"></i><span>Spot Price</span></div>
   <div class="gexLegendRow"><i class="gexLegendLine flip"></i><span>Gamma Flip</span></div>
   <div class="gexLegendRow"><i class="gexLegendLine call"></i><span>Call Wall</span></div>
   <div class="gexLegendRow"><i class="gexLegendLine put"></i><span>Put Wall</span></div>`;
}
function formatChainSnapshot(ts){
 if(!ts)return {text:"Chain snapshot: —",cls:""};
 const d=new Date(ts); if(Number.isNaN(d.getTime()))return {text:"Chain snapshot: —",cls:""};
 const mins=Math.max(0,Math.floor((Date.now()-d.getTime())/60000));
 const clock=d.toLocaleTimeString([], {hour:"numeric",minute:"2-digit"});
 const age=mins<1?"just now":mins===1?"1 min ago":`${mins} min ago`;
 return {text:`Chain snapshot: ${clock} · ${age}`,cls:mins<=5?"fresh":mins<=15?"aging":"stale"};
}
function renderChainFreshness(ts,isStale=false,refreshError=null){
 const el=document.getElementById("chainFreshness"); if(!el)return;
 const f=formatChainSnapshot(ts); el.className=`chainFreshness ${isStale?"stale":f.cls}`;
 el.textContent=f.text+(isStale?" · showing last good snapshot":"");
 el.title=refreshError?`Latest refresh failed: ${refreshError}`:"Alpaca chain snapshot fetch time. Options data may be cached for up to 10 minutes.";
}

function buildConfluence(ticker,spot,p){
 const levels=[]; const va=valueAcceptanceMap[ticker]; const st=stratSignalMap[ticker];
 if(va){levels.push({v:va.vah,n:"VAH",bias:"bullish"},{v:va.poc,n:"POC",bias:"neutral"},{v:va.val,n:"VAL",bias:"bearish"});}
 (st?.frames||[]).forEach(f=>{if(Number.isFinite(Number(f.up_trigger)))levels.push({v:Number(f.up_trigger),n:`${f.timeframe} 2U`,bias:"bullish"});if(Number.isFinite(Number(f.down_trigger)))levels.push({v:Number(f.down_trigger),n:`${f.timeframe} 2D`,bias:"bearish"});});
 if(p){if(p.call_wall!=null)levels.push({v:Number(p.call_wall),n:"Call wall",bias:"bullish"});if(p.put_wall!=null)levels.push({v:Number(p.put_wall),n:"Put wall",bias:"bearish"});if(p.modeled_flip!=null)levels.push({v:Number(p.modeled_flip),n:"Gamma flip",bias:"neutral"});}
 const tol=Math.max(.5,(spot||100)*.006);

 // Bounded clustering: a level only joins an existing cluster if doing so keeps
 // the cluster's TOTAL SPAN within tolerance, checked against the cluster's
 // actual min/max — not a shifting running average. The prior running-average
 // approach let a chain of sequentially-spaced levels drift to several multiples
 // of tol before splitting, which quietly widened what "confluence" meant.
 function clusterLevels(subset){
   const sorted=subset.filter(x=>Number.isFinite(x.v)).sort((a,b)=>a.v-b.v);
   const clusters=[];
   sorted.forEach(x=>{
     let target=null;
     for(const c of clusters){
       if(Math.max(c.max,x.v)-Math.min(c.min,x.v)<=tol){target=c;break;}
     }
     if(target){target.items.push(x);target.min=Math.min(target.min,x.v);target.max=Math.max(target.max,x.v);}
     else clusters.push({min:x.v,max:x.v,items:[x]});
   });
   return clusters.filter(c=>c.items.length>=2).sort((a,b)=>b.items.length-a.items.length)[0]||null;
 }

 // Cluster bullish-side and bearish-side levels separately. A call wall and a
 // down-trigger sitting near each other isn't real confluence — it's a
 // resistance marker and a support marker overlapping, which is closer to a
 // chop/indecision signal than a confident directional zone. Neutral levels
 // (POC, gamma flip) can support either side since they aren't directional.
 const bullish=clusterLevels(levels.filter(x=>x.bias==="bullish"||x.bias==="neutral"));
 const bearish=clusterLevels(levels.filter(x=>x.bias==="bearish"||x.bias==="neutral"));
 return {bullish,bearish};
}
async function renderTradeThesis(ticker,spot,p){
 if(!ticker)return; ticker=String(ticker).toUpperCase(); const x=liveStockData.find(r=>r.ticker===ticker),va=valueAcceptanceMap[ticker],vm=valueMigrationMap[ticker],st=stratSignalMap[ticker];
 let score=5,reasons=[]; const f=x?.fast||x||{},t=x?.trend||{};
 if(["Leading","Improving"].includes(f.quadrant)){score+=1;reasons.push(`${f.quadrant} fast RRG`)} if(f.tail_trajectory==="Rotating In"){score+=1;reasons.push("fast tail NE")}
 if(["Leading","Improving"].includes(t.quadrant)){score+=.7;reasons.push(`${t.quadrant} trend RRG`)} if(va?.direction==="bullish"){score+=.7;reasons.push(va.state)} if(va?.direction==="bearish")score-=.7;
 if(vm?.direction==="bullish"){score+=.6;reasons.push("value migrating higher")} if(vm?.direction==="bearish")score-=.6;
 if(st?.continuity==="bullish"){score+=.6;reasons.push("bullish STRAT FTC")} if(st?.continuity==="bearish")score-=.6;
 score=Math.max(0,Math.min(10,score)); const bias=score>=7?"BULLISH":score<=3.5?"BEARISH":"MIXED";
 const zones=buildConfluence(ticker,spot,p),zone=document.getElementById("confluenceZone");
 if(zone){
   const fmtZone=(c,label,cls)=>c?`<div class="confluenceRow ${cls}"><b>${label} CONFLUENCE · $${fmt(Math.min(...c.items.map(z=>z.v)),2)}–$${fmt(Math.max(...c.items.map(z=>z.v)),2)}</b> · ${c.items.map(z=>z.n).join(" + ")}</div>`:"";
   const html=fmtZone(zones.bullish,"BULLISH","bullish")+fmtZone(zones.bearish,"BEARISH","bearish");
   zone.innerHTML=html||"No multi-factor level cluster detected yet.";
 }
 const b=document.getElementById("thesisBias"),txt=document.getElementById("thesisText"); if(b)b.textContent=`${bias} · ${score.toFixed(1)}/10`; if(txt)txt.innerHTML=`<b>${ticker}</b> · ${reasons.join(" · ")||"insufficient confirmation"}${vm?` · ${vm.state}`:""}. GEX window: ${activeOptionsData?.gex_window||"0-30"}.`;
 const signature=[f.quadrant||"na",f.tail_trajectory||"na",t.quadrant||"na",va?.direction||"na",vm?.direction||"na",st?.continuity||"na",p?.gamma_regime||"na"].join("|");
 try{await fetch("/api/setup-snapshot",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticker,spot,bias,score,signature,raw:{fast:f,trend:t,value:va,value_migration:vm,strat:st,positioning:{call_wall:p?.call_wall,put_wall:p?.put_wall,modeled_flip:p?.modeled_flip,net_gex:p?.net_gex,gamma_regime:p?.gamma_regime},gex_window:activeOptionsData?.gex_window}})}); const hr=await fetch(`/api/setup-history/${encodeURIComponent(ticker)}?signature=${encodeURIComponent(signature)}`),hj=await hr.json(); const he=document.getElementById("historicalSetup"); if(he&&hj.ok){const r5=hj.returns?.["5"];he.innerHTML=`<b>HISTORICAL SETUP</b> · ${hj.count} captured matches${r5?.n?` · 5D ${r5.win_rate}% positive · median ${r5.median>=0?"+":""}${r5.median}%`:" · collecting forward outcomes"}${hj.median_mfe_10d!=null?` · 10D MFE ${hj.median_mfe_10d>=0?"+":""}${hj.median_mfe_10d}%`:""}`;}}
 catch(e){console.warn("setup history",e)}
}

function renderPositioning(p,spot){
 const sec=document.getElementById("positioningSection"),sum=document.getElementById("positioningSummary");
 if(!sec||!sum)return;if(!p||!p.available){sec.style.display="none";return}sec.style.display="block";
 const net=Number(p.net_gex)||0,regimeCls=net>=0?"good":"bad";
 const dist=(v)=>spot&&v!=null?`${(v/spot-1)*100>=0?'+':''}${fmt((v/spot-1)*100,1)}% vs spot`:"";
 const top=(p.levels||[]).slice().sort((a,b)=>Math.abs(Number(b.net_gex)||0)-Math.abs(Number(a.net_gex)||0)).slice(0,4);
 sum.innerHTML=`
 <div class="metricCard callWall"><div class="tiny">CALL WALL</div><div class="subLabel">Upside Ceiling</div><div class="big">${p.call_wall==null?"—":"$"+fmt(p.call_wall,2)}</div><div class="tiny">${dist(p.call_wall)}</div></div>
 <div class="metricCard flipCard"><div class="tiny">GAMMA FLIP</div><div class="subLabel">Transition Zone</div><div class="big">${p.modeled_flip==null?"—":"$"+fmt(p.modeled_flip,2)}</div><div class="tiny">${dist(p.modeled_flip)}</div></div>
 <div class="metricCard putWall"><div class="tiny">PUT WALL</div><div class="subLabel">Downside Floor</div><div class="big">${p.put_wall==null?"—":"$"+fmt(p.put_wall,2)}</div><div class="tiny">${dist(p.put_wall)}</div></div>
 <div class="metricCard spotCard ${regimeCls}"><div class="tiny">SPOT / REGIME</div><div class="subLabel">Current Price</div><div class="big">${spot==null?"—":"$"+fmt(spot,2)}</div><div class="tiny ${net>=0?'positive':'negative'}">${p.gamma_regime||"—"}</div></div>
 <div class="metricCard netGex ${net<0?'negative':''}"><div class="tiny">NET GEX</div><div class="subLabel">Call − Put</div><div class="big">${gexSigned(net)}</div><div class="tiny">Modeled exposure</div></div>
 <div class="metricCard exposureCard"><div class="tiny">LARGEST EXPOSURES</div><div class="subLabel">By strike · net GEX</div><div class="exposureMini">${top.map(x=>`<div><b>$${fmt(x.strike,0)}</b> <span class="${x.net_gex>=0?'pos':'neg'}">${gexSigned(x.net_gex)}</span> <em>${x.net_gex>=0?'Call':'Put'}</em></div>`).join('')||'—'}</div></div>`;
 selectedGammaStrike=null;renderGexRail(p,spot);drawGammaLandscape(p,spot);renderTradeThesis(activeOptionsData?.ticker,spot,p);
}
let activeFlowData=null;
function renderFlow(x){
 const sec=document.getElementById("flowSection"),sum=document.getElementById("flowSummary"),rows=document.getElementById("flowRows"),disc=document.getElementById("flowDisclosure"),un=document.getElementById("unusualFlow"),st=document.getElementById("flowStatus");
 if(!sec||!sum)return;sec.style.display="block";if(st)st.textContent=`${x.ticker||""} · ${x.feed||""} · v${x.engine_version||"21"} · ${x.contracts_sampled||0}/${x.eligible_contracts||0} candidates · ${x.candidate_coverage_pct==null?"—":fmt(x.candidate_coverage_pct,1)+"%"} coverage`;
 const cp=x.institutional_call_pct==null?0:x.institutional_call_pct,pp=x.institutional_put_pct==null?0:x.institutional_put_pct;
 const conf=x.coverage_confidence||"Unknown", confCls=conf==="High"?"good":conf==="Medium"?"warn":"bad";
 sum.innerHTML=`<div class="metricCard"><div class="tiny">INSTITUTIONAL EVENT PREMIUM</div><div class="big">${moneyShort(x.institutional_premium)}</div><div class="tiny">${x.institutional_events||0} clustered events · ${x.high_relevance_events||0} high relevance</div></div>
 <div class="metricCard"><div class="tiny">CANDIDATE COVERAGE</div><div class="big">${x.candidate_coverage_pct==null?"—":fmt(x.candidate_coverage_pct,1)+"%"}</div><div class="tiny">${x.contracts_sampled||0}/${x.eligible_contracts||0} candidates · ${x.activity_coverage_pct==null?"—":fmt(x.activity_coverage_pct,1)+"%"} est. activity</div></div>
 <div class="metricCard ${confCls}"><div class="tiny">FLOW CONFIDENCE</div><div class="big">${conf}</div><div class="tiny">coverage confidence, not directional confidence</div></div>
 <div class="metricCard"><div class="tiny">CONTRACT MIX · CALL / PUT</div><div class="big">${fmt(cp,0)}% / ${fmt(pp,0)}%</div><div class="flowSplit"><span style="width:${cp}%"></span><span style="width:${pp}%"></span></div><div class="tiny">${moneyShort(x.institutional_call_premium)} calls · ${moneyShort(x.institutional_put_premium)} puts</div></div>
 <div class="metricCard ${String(x.direction_status||"").includes("Unavailable")?"warn":""}"><div class="tiny">DIRECTION</div><div class="big">${x.direction_status||"Unconfirmed"}</div><div class="tiny">${x.direction_reason||"requires contemporaneous NBBO / aggressor classification"}</div></div>
 <div class="metricCard"><div class="tiny">LARGEST EVENT</div><div class="big">${x.largest?.length?moneyShort(x.largest[0].premium):"—"}</div><div class="tiny">${x.largest?.length?`${formatOptionDate(x.largest[0].expiration)} $${fmt(x.largest[0].strike,0)} ${optionTypeWord(x.largest[0].type)} · ${x.largest[0].prints||1} prints`:"No qualifying event"}</div></div>
 <div class="metricCard"><div class="tiny">RAW SAMPLE</div><div class="big">${moneyShort(x.gross_premium)}</div><div class="tiny">${x.all_prints||0} prints · context only</div></div>`;
 if(disc)disc.innerHTML=`<b>Important:</b> ${x.note||""}`;
 if(rows)rows.innerHTML=(x.largest||[]).length?(x.largest||[]).map(r=>`<tr><td>${readableContractHTML(r,x.ticker)}<div class="tiny">${r.relevance||""} relevance · score ${r.institutional_score==null?"—":fmt(r.institutional_score,0)} · ${r.prints||1} prints</div></td><td>$${fmt(r.price,2)}</td><td>${r.size}</td><td><b>${moneyShort(r.premium)}</b></td><td>${r.timestamp?new Date(r.timestamp).toLocaleTimeString([], {hour:"numeric",minute:"2-digit",second:"2-digit"}):"—"}</td></tr>`).join(""):'<tr><td colspan="5" class="note">No qualifying institutional events in the current sample.</td></tr>';
 if(un){const a=(x.unusual||[]).slice(0,8);un.innerHTML=a.length?`<b>Volume/OI anomalies:</b> `+a.map(r=>`${formatOptionDate(r.expiration)} $${fmt(r.strike,0)} ${optionTypeShort(r.type)} · ${r.vol_oi==null?"new OI":fmt(r.vol_oi,1)+"× OI"} · ${moneyShort(r.premium)}`).join(" &nbsp; | &nbsp; "):""}
}
async function loadFlowTicker(ticker,force=false){
 const st=document.getElementById("flowStatus"),sec=document.getElementById("flowSection");if(sec)sec.style.display="block";if(st)st.textContent=`Loading ${ticker} flow…`;
 try{
   const j=await safeTickerFetchJson("/api/flow",ticker,force?{refresh:1}:{});
   activeFlowData=j;renderFlow(j);renderHeatMap();const fb=document.getElementById("refreshFlow");if(fb)fb.textContent="Refresh flow"
 }catch(e){
   activeFlowData=null;
   if(st)st.innerHTML=`<span class="warn">${e.message}</span><div class="tiny" style="margin-top:5px">GEX, chain snapshots, STRAT, and options screening remain available. Use FlowMS for directional flow while this Alpaca entitlement is unavailable.</div>`;
 }
}

function renderOptionsPanel(){
 const sum=document.getElementById("optionsSummary"),body=document.getElementById("optionsRows"),st=document.getElementById("optionsStatus"),under=document.getElementById("optionsUnderlying");
 if(!activeOptionsData){
   sum.innerHTML="";
   if(under)under.textContent="";
   return
 }
 const x=activeOptionsData;st.textContent=`${x.ticker} · ${x.feed||""}`; renderPositioning(x.positioning,x.spot); renderChainFreshness(x.chain_updated_at,x.stale,x.refresh_error);
 if(under)under.innerHTML=`<span class="tiny">Current price</span> <b>${x.ticker} $${fmt(x.spot,2)}</b>`;
 sum.innerHTML=`<div class="card"><div class="tiny">CURRENT PRICE</div><b>${x.ticker} · $${fmt(x.spot,2)}</b></div>
 <div class="card"><div class="tiny">LIQUIDITY</div><b>${x.liquidity}</b><div class="tiny">${x.liquid_contracts} liquid · ${x.tradable_contracts} tradable</div></div>
 <div class="card"><div class="tiny">IV RELATIVE VALUE</div><b>${x.atm_iv==null?"—":fmt(x.atm_iv,1)+"%"}</b><div class="tiny">${x.iv_state} · IV/RV ${x.iv_rv_ratio==null?"—":fmt(x.iv_rv_ratio,2)}</div></div>
 <div class="card"><div class="tiny">20D REALIZED VOL</div><b>${x.rv20==null?"—":fmt(x.rv20,1)+"%"}</b><div class="tiny">IV/RV ${x.iv_rv_ratio==null?"—":fmt(x.iv_rv_ratio,2)}</div></div>`;
 const typ=document.getElementById("optTypeFilter").value,lf=document.getElementById("optLiquidityFilter").value,rank={Thin:0,Tradable:1,Liquid:2};
 const rows=(x.contracts||[])
   .filter(r=>(typ==="all"||String(r.type||"").toLowerCase()===typ)&&(lf==="all"||(lf==="Tradable"&&rank[r.liquidity]>=1)||(lf==="Liquid"&&r.liquidity==="Liquid")))
   .sort((a,b)=>String(a.expiration||"").localeCompare(String(b.expiration||"")) || (Number(a.strike||0)-Number(b.strike||0)) || String(a.type||"").localeCompare(String(b.type||"")))
   .slice(0,120);
 body.innerHTML=rows.length?rows.map(r=>`<tr>
 <td>${readableContractHTML(r,x.ticker)}</td>
 <td><b>${dteFromExpiration(r.expiration)??"—"}</b><div class="tiny">${formatOptionDate(r.expiration)}</div></td>
 <td><b>${r.mid==null?"—":"$"+fmt(r.mid,2)}</b></td>
 <td>${r.bid==null?"—":"$"+fmt(r.bid,2)}</td>
 <td>${r.ask==null?"—":"$"+fmt(r.ask,2)}</td>
 <td>${r.spread_pct==null?"—":fmt(r.spread_pct,1)+"%"}</td>
 <td>${r.volume??0}</td>
 <td>${r.open_interest??0}</td>
 <td>${r.iv==null?"—":fmt(r.iv,1)+"%"}</td>
 <td>${r.delta==null?"—":fmt(r.delta,2)}</td>
 <td>${optionBadgeHTML({liquidity:r.liquidity,execution_label:r.execution_label,iv_state:""})}</td>
 </tr>`).join(""):'<tr><td colspan="11" class="note">No contracts match these filters.</td></tr>';
}
async function loadOptionsTicker(ticker,opts={}){
 ticker=normalizeStockTicker(ticker);
 if(!isSafeStockTicker(ticker))return;
 if(opts.scroll!==false)focusOptionsPanel();
 const st=document.getElementById("optionsStatus");
 if(alpacaConfigured===false){
   st.innerHTML='<span class="error">Connect Alpaca first using the blue button above, then add the API key + secret in Render.</span>';
   return;
 }
 st.textContent=`Loading ${ticker} options…`;
 try{
   const gw=document.getElementById("gexWindow")?.value||"0-30";
   const j=await safeTickerFetchJson("/api/options",ticker,{gex_window:gw,dte_min:7,dte_max:35});
   activeOptionsData=j;optionScanMap[ticker]=j;
   renderTopSetups();
   const wi=liveWatchlist.findIndex(x=>liveWatchKey(x.ticker)===liveWatchKey(ticker));
   if(wi>=0){
     if(liveWatchlist[wi].added_price==null)liveWatchlist[wi].added_price=j.spot??null;
     liveWatchlist[wi].current_price=j.spot??liveWatchlist[wi].current_price??null;
     liveWatchlist[wi].iv_state=j.iv_state||liveWatchlist[wi].iv_state||null;
     liveWatchlist[wi].liquidity=j.liquidity||liveWatchlist[wi].liquidity||null;
     const lx=liveStockData.find(r=>r.ticker===ticker);
     if(lx){
       liveWatchlist[wi].stage=rotationStage(lx).label;
       liveWatchlist[wi].stage_level=rotationStage(lx).level;
       liveWatchlist[wi].tail=effectiveTailSignal(lx)||lx.tail_trajectory||liveWatchlist[wi].tail;
       liveWatchlist[wi].opportunity=opportunityScore(lx);
     }
     try{localStorage.setItem(LIVE_WATCHLIST_KEY,JSON.stringify(liveWatchlist))}catch(e){}
     renderLiveWatchlist();
   }
   renderOptionsPanel();renderLiveStocks();
   const fs=document.getElementById("flowSection"),fst=document.getElementById("flowStatus"),fb=document.getElementById("refreshFlow");
   if(fs)fs.style.display="block";if(fst)fst.textContent=`${ticker} · flow deferred for faster loading`;if(fb)fb.textContent="Load flow";
 }catch(e){console.error("Options request failed",ticker,e);st.innerHTML=`<span class="error">${ticker} options: ${e?.message||e}</span>`}
}
async function scanVisibleOptions(){
 focusOptionsPanel();
 const symbols=filteredLiveStocks().map(x=>x.ticker),st=document.getElementById("optionsStatus"),btn=document.getElementById("scanOptions");
 if(alpacaConfigured===false){
   st.innerHTML='<span class="error">Connect Alpaca first using the blue button above, then add the API key + secret in Render.</span>';
   return;
 }
 if(!symbols.length){st.textContent="No live tickers to scan.";return}
 st.textContent=`Scanning all ${symbols.length} filtered tickers…`;btn.disabled=true;
 try{
   const r=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols})}),j=await r.json();
   if(!r.ok||!j.ok)throw Error(j.error||"Options scan failed");
   (j.results||[]).forEach(x=>{if(x.ticker)optionScanMap[x.ticker]=x});
   renderOptionsScanResults(j.results||[]);
   st.textContent=`Options scan complete · ${j.results?.length||0} tickers`;
   renderLiveStocks();
 }catch(e){st.innerHTML=`<span class="error">${e.message}</span>`}finally{btn.disabled=false}
}


let stratRequestSeq=0;
const valueAcceptanceMap={};
const valueMigrationMap={};
const stratSignalMap={};

function classifyValueAcceptance(payload){
 const bars=payload?.bars||[];
 const vis=payload?.visible_profiles||{};
 const tf=(payload?.timeframe||previewTimeframe||"1d").toLowerCase();
 if(!bars.length)return null;

 function dkey(r){
   const d=new Date(String(r.date).includes("T")?r.date:`${r.date}T00:00:00`);
   if(Number.isNaN(d.getTime()))return "";
   return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
 }
 function wkey(r){
   const d=new Date(String(r.date).includes("T")?r.date:`${r.date}T00:00:00`);
   if(Number.isNaN(d.getTime()))return "";
   const u=new Date(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()));
   const day=u.getUTCDay()||7;u.setUTCDate(u.getUTCDate()+4-day);
   const ys=new Date(Date.UTC(u.getUTCFullYear(),0,1));
   const wk=Math.ceil((((u-ys)/86400000)+1)/7);
   return `${u.getUTCFullYear()}-W${String(wk).padStart(2,"0")}`;
 }

 let reference=null,referenceLabel="Prior session";
 if(tf==="1w"){
   const weeks=vis.weeks||[];
   const curKey=wkey(bars[bars.length-1]);
   const idx=weeks.findIndex(x=>x.week===curKey);
   reference=(idx>0?weeks[idx-1]:weeks.length>1?weeks[weeks.length-2]:weeks[0])?.profile||null;
   referenceLabel="Prior week";
 }else{
   const sessions=vis.sessions||[];
   const curKey=dkey(bars[bars.length-1]);
   const idx=sessions.findIndex(x=>x.date===curKey);
   reference=(idx>0?sessions[idx-1]:sessions.length>1?sessions[sessions.length-2]:sessions[0])?.profile||null;
 }
 if(!reference)return null;

 const vah=Number(reference.vah),poc=Number(reference.poc),val=Number(reference.val);
 if(![vah,poc,val].every(Number.isFinite))return null;

 const last=bars[bars.length-1],prev=bars.length>1?bars[bars.length-2]:null;
 const close=Number(last.close),high=Number(last.high??last.close),low=Number(last.low??last.close);
 const prevClose=prev?Number(prev.close):NaN;
 if(!Number.isFinite(close))return null;

 // Volume confirmation: a quiet-volume close outside value is weaker evidence
 // of real acceptance than an expansion-volume close. Compare this bar's volume
 // against the trailing 20-bar average (excluding this bar). When volume data
 // isn't available, treat confirmation as unknown rather than blocking CONFIRMED
 // outright — this only ever downgrades a result when we positively know volume
 // was light, never when we simply lack the data.
 const priorVols=bars.slice(0,-1).slice(-20).map(x=>Number(x.volume)).filter(Number.isFinite);
 const avgVol=priorVols.length?priorVols.reduce((s,x)=>s+x,0)/priorVols.length:null;
 const curVol=Number(last.volume);
 const volKnown=avgVol!=null&&avgVol>0&&Number.isFinite(curVol);
 const volConfirmed=volKnown?(curVol>=avgVol*1.2):null;
 const volNote=volKnown?(volConfirmed?" on above-average volume":" on below-average volume"):"";

 let state="Inside value",kind="neutral",strength="NEUTRAL",direction="neutral",score=0,detail=`Price remains inside ${referenceLabel.toLowerCase()} value.`;
 const priorAbove=Number.isFinite(prevClose)&&prevClose>vah;
 const priorBelow=Number.isFinite(prevClose)&&prevClose<val;

 if(close>vah){
   direction="bullish";
   const structural=priorAbove || low>=vah;
   if(structural && volConfirmed!==false){
     state="Accepted above VAH";kind="bullish";strength="CONFIRMED";score=1;
     detail=`Break above ${referenceLabel.toLowerCase()} VAH is holding outside value — bullish acceptance${volNote}.`;
   }else if(structural){
     state="Accepted above VAH (light volume)";kind="developing";strength="DEVELOPING";score=.75;
     detail=`Price is holding outside ${referenceLabel.toLowerCase()} value, but on below-average volume — acceptance is less convincing without real participation.`;
   }else{
     state="Breaking above VAH";kind="developing";strength="DEVELOPING";score=.5;
     detail=`Closed above VAH, but acceptance still needs another hold/close outside value.`;
   }
 }else if(close<val){
   direction="bearish";
   const structural=priorBelow || high<=val;
   if(structural && volConfirmed!==false){
     state="Accepted below VAL";kind="bearish";strength="CONFIRMED";score=1;
     detail=`Break below ${referenceLabel.toLowerCase()} VAL is holding outside value — bearish acceptance${volNote}.`;
   }else if(structural){
     state="Accepted below VAL (light volume)";kind="developing";strength="DEVELOPING";score=.75;
     detail=`Price is holding outside ${referenceLabel.toLowerCase()} value, but on below-average volume — acceptance is less convincing without real participation.`;
   }else{
     state="Breaking below VAL";kind="developing";strength="DEVELOPING";score=.5;
     detail=`Closed below VAL, but acceptance still needs another hold/close outside value.`;
   }
 }else if(high>vah && close<=vah){
   state="VAH rejection";kind="warning";strength="REJECTION";direction="bearish";score=-.5;
   detail=`Price auctioned above VAH but closed back inside value — failed upside auction.`;
 }else if(low<val && close>=val){
   state="VAL rejection";kind="warning";strength="REJECTION";direction="bullish";score=-.5;
   detail=`Price auctioned below VAL but closed back inside value — failed downside auction.`;
 }

 return {state,kind,strength,direction,score,vah,poc,val,close,referenceLabel,vol_confirmed:volConfirmed};
}

function renderValueAcceptance(sig){
 const card=document.getElementById("valueAcceptanceCard"),state=document.getElementById("valueAcceptanceState"),
       pill=document.getElementById("valueAcceptancePill"),detail=document.getElementById("valueAcceptanceDetail"),
       refLabel=document.getElementById("valueAcceptanceRefLabel");
 if(!card||!state||!pill||!detail)return;
 const vah=document.getElementById("analysisVah"),poc=document.getElementById("analysisPoc"),val=document.getElementById("analysisVal");
 if(!sig){
   card.className="valueAcceptanceCard neutral";state.textContent="Profile unavailable";pill.className="vaPill neutral";pill.textContent="—";
   detail.textContent="Needs a completed prior-session/prior-week profile.";
   if(refLabel)refLabel.textContent="Reference: —";
   if(vah)vah.textContent="—";if(poc)poc.textContent="—";if(val)val.textContent="—";return;
 }
 card.className=`valueAcceptanceCard ${sig.kind}`;
 state.textContent=sig.state;
 pill.className=`vaPill ${sig.kind}`;
 pill.textContent=sig.strength;
 detail.textContent=sig.detail;
 // Explicit session label: this card intentionally compares price against the
 // last COMPLETED reference session/week, not the latest one shown in the
 // Chart Preview legend above — those are deliberately different reference
 // points and were previously indistinguishable without reading the detail text.
 if(refLabel)refLabel.textContent=`Reference: ${sig.referenceLabel||"—"}`;
 if(vah)vah.textContent=`$${sig.vah.toFixed(2)}`;
 if(poc)poc.textContent=`$${sig.poc.toFixed(2)}`;
 if(val)val.textContent=`$${sig.val.toFixed(2)}`;
}

function stratScenarioClass(frame){
 if(frame?.direction==="bullish")return "bullish";
 if(frame?.direction==="bearish")return "bearish";
 return "neutral";
}
function renderStrat(data){
 const box=document.getElementById("stratFrames"),status=document.getElementById("stratStatus"),cont=document.getElementById("stratContinuity");
 if(!box||!status||!cont)return;
 const frames=data?.frames||[];
 cont.className=`stratContinuity ${data?.continuity||"mixed"}`;
 cont.innerHTML=data?.continuity==="bullish"?`${data.bullish_count}/4 BULLISH ${glossTerm("FTC")}`:data?.continuity==="bearish"?`${data.bearish_count}/4 BEARISH ${glossTerm("FTC")}`:`${data?.bullish_count||0}↑ / ${data?.bearish_count||0}↓ MIXED`;
 status.textContent=`${data.ticker} · multi-timeframe price-action confirmation`;
 box.innerHTML=frames.map(f=>{
   const cls=stratScenarioClass(f),arrow=cls==="bullish"?"↑":cls==="bearish"?"↓":"↔";
   const up=Number(f.up_trigger),dn=Number(f.down_trigger);
   return `<div class="stratFrame"><div class="stratFrameTop"><span class="stratTf">${f.timeframe}</span><span class="stratScenario ${cls}">${f.scenario||"—"} ${arrow}</span></div><div class="stratPattern">${f.pattern||"—"}</div><div class="stratTrigger"><div class="up">UP TRIGGER<b>${Number.isFinite(up)?`$${up.toFixed(2)}`:"—"}</b></div><div class="down">DOWN TRIGGER<b>${Number.isFinite(dn)?`$${dn.toFixed(2)}`:"—"}</b></div></div><div class="stratFTC">FTC: ${(f.ftc||"neutral").toUpperCase()}</div></div>`;
 }).join("");
}
async function loadStrat(ticker){
 ticker=normalizeStockTicker(ticker);
 if(!isSafeStockTicker(ticker))return;
 const seq=++stratRequestSeq,status=document.getElementById("stratStatus"),box=document.getElementById("stratFrames"),cont=document.getElementById("stratContinuity");
 if(!ticker)return;
 if(status)status.textContent=`Loading ${ticker} STRAT…`;
 if(box)box.innerHTML="";
 if(cont){cont.className="stratContinuity";cont.textContent="…";}
 try{
   const j=await safeTickerFetchJson("/api/strat",ticker);
   if(seq!==stratRequestSeq)return;
   stratSignalMap[String(ticker).toUpperCase()]=j;
   renderStrat(j);
   renderTopSetups();
 }catch(e){console.error("STRAT request failed",ticker,e);if(status)status.innerHTML=`<span class="error">STRAT unavailable for ${ticker}: ${e?.message||e}</span>`;}
}

function drawPricePreview(payload){
 const c=document.getElementById("pricePreviewChart"),ctx=c?.getContext("2d");
 if(!c||!ctx)return;
 const rows=payload?.bars||[],W=c.width,H=c.height;
 ctx.clearRect(0,0,W,H);
 const bg=ctx.createLinearGradient(0,0,0,H);bg.addColorStop(0,"#081119");bg.addColorStop(1,"#070d13");
 ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
 if(!rows.length){ctx.fillStyle="#7f8c9d";ctx.font="12px sans-serif";ctx.fillText("Select a ticker to load candles",24,34);return}

 const pad={l:62,r:76,t:32,b:54},volH=84,volGap=14;
 const priceBottom=H-pad.b-volH-volGap;
 const plotW=W-pad.l-pad.r;
 const X=i=>pad.l+(i+.5)*plotW/rows.length;
 const spacing=plotW/rows.length;

 const profiles=payload?.volume_profiles||{};
 const visibleProfiles=payload?.visible_profiles||{};
 let singleVp=null;
 if(previewVPMode==="session")singleVp=profiles.session;
 else if(previewVPMode==="previous")singleVp=profiles.previous;

 // Map session profiles to YYYY-MM-DD for quick lookup.
 const sessionMap=new Map((visibleProfiles.sessions||[]).map(x=>[x.date,x.profile]));
 const weekMap=new Map((visibleProfiles.weeks||[]).map(x=>[x.week,x.profile]));

 function rowDateKey(r){
   const d=new Date(String(r.date).includes("T")?r.date:`${r.date}T00:00:00`);
   if(Number.isNaN(d.getTime()))return "";
   return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
 }
 function weekKey(r){
   const d=new Date(String(r.date).includes("T")?r.date:`${r.date}T00:00:00`);
   if(Number.isNaN(d.getTime()))return "";
   const u=new Date(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()));
   const day=u.getUTCDay()||7;u.setUTCDate(u.getUTCDate()+4-day);
   const yearStart=new Date(Date.UTC(u.getUTCFullYear(),0,1));
   const week=Math.ceil((((u-yearStart)/86400000)+1)/7);
   return `${u.getUTCFullYear()}-W${String(week).padStart(2,"0")}`;
 }

 // Price scale uses the displayed candles. Per-session profiles align exactly
 // to the candle price axis, which is the key difference from the prior build.
 const highs=rows.map(r=>Number(r.high??r.close)).filter(Number.isFinite);
 const lows=rows.map(r=>Number(r.low??r.close)).filter(Number.isFinite);
 let lo=Math.min(...lows),hi=Math.max(...highs),range=Math.max(.01,hi-lo);
 lo-=range*.06;hi+=range*.06;
 const Y=v=>pad.t+(hi-v)/(hi-lo)*(priceBottom-pad.t);

 // More price points for easier node reading.
 ctx.font="9px ui-monospace, SFMono-Regular, Menlo, monospace";
 const gridCount=11;
 for(let k=0;k<gridCount;k++){
   const y=pad.t+k*(priceBottom-pad.t)/(gridCount-1);
   const level=hi-k*(hi-lo)/(gridCount-1);
   ctx.strokeStyle=k%2===0?"#1b2a36":"#13212b";ctx.lineWidth=1;
   ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(W-pad.r,y);ctx.stroke();
   ctx.fillStyle="#8392a3";ctx.textAlign="right";ctx.fillText(level.toFixed(2),pad.l-8,y+3);
 }

 // Time separators.
 let lastKey="";
 rows.forEach((r,i)=>{
   const d=new Date(String(r.date).includes("T")?r.date:`${r.date}T00:00:00`);
   if(Number.isNaN(d.getTime()))return;
   const intraday=previewTimeframe==="1h"||previewTimeframe==="4h";
   const key=intraday?rowDateKey(r):`${d.getFullYear()}-${d.getMonth()}`;
   if(key!==lastKey){
     if(i>0){
       const x=X(i)-spacing*.5;
       ctx.strokeStyle="#10202a";ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,H-pad.b);ctx.stroke();
       ctx.fillStyle="#718094";ctx.textAlign="center";
       ctx.fillText(intraday?d.toLocaleDateString(undefined,{month:"short",day:"numeric"}):d.toLocaleString(undefined,{month:"short"}),x,H-16);
     }
     lastKey=key;
   }
 });

 // -------------------- Per-session SVP --------------------
 if(previewVPMode==="auto"){
   ctx.save();
   const isWeekly=previewTimeframe==="1w";

   if(isWeekly){
     rows.forEach((r,i)=>{
       const vp=weekMap.get(weekKey(r));
       if(!vp?.bins?.length)return;
       const bins=vp.bins.filter(b=>Number.isFinite(Number(b.price))&&Number.isFinite(Number(b.volume)));
       const maxV=Math.max(1,...bins.map(b=>Number(b.volume)||0));
       const center=X(i),left=center-spacing*.40,right=center+spacing*.40;
       bins.forEach(b=>{
         const p=Number(b.price);if(p<lo||p>hi)return;
         const w=(Number(b.volume)||0)/maxV*(right-left);
         const rowPx=Math.max(1.6,Number(vp.row_size||.01)*(priceBottom-pad.t)/(hi-lo));
         const inVA=p>=Number(vp.val)&&p<=Number(vp.vah);
         const isPoc=Math.abs(p-Number(vp.poc))<=Number(vp.row_size||.01);
         ctx.fillStyle=isPoc?"rgba(245,158,11,.88)":inVA?"rgba(64,126,219,.48)":"rgba(69,93,119,.27)";
         ctx.fillRect(right-w,Y(p)-rowPx/2,w,rowPx);
       });
     });
   } else {
     // Find contiguous blocks belonging to each RTH session. Daily has one
     // candle per block; 1H/4H have several candles per block.
     let i=0;
     while(i<rows.length){
       const key=rowDateKey(rows[i]);
       let j=i;
       while(j+1<rows.length && rowDateKey(rows[j+1])===key)j++;
       const vp=sessionMap.get(key);
       if(vp?.bins?.length){
         const bins=vp.bins.filter(b=>Number.isFinite(Number(b.price))&&Number.isFinite(Number(b.volume)));
         const maxV=Math.max(1,...bins.map(b=>Number(b.volume)||0));
         const blockLeft=X(i)-spacing*.42;
         const blockRight=X(j)+spacing*.42;

         // Daily: keep the profile beside the candle. Intraday: use the right
         // half of that session's block so the session auction remains visible.
         const profLeft=previewTimeframe==="1d"
           ? X(i)+Math.min(3,spacing*.06)
           : blockLeft+(blockRight-blockLeft)*.46;
         const profRight=previewTimeframe==="1d"
           ? X(i)+spacing*.47
           : blockRight;

         bins.forEach(b=>{
           const p=Number(b.price);if(p<lo||p>hi)return;
           const w=(Number(b.volume)||0)/maxV*Math.max(4,profRight-profLeft);
           const rowPx=Math.max(1.8,Number(vp.row_size||.01)*(priceBottom-pad.t)/(hi-lo));
           const inVA=p>=Number(vp.val)&&p<=Number(vp.vah);
           const isPoc=Math.abs(p-Number(vp.poc))<=Number(vp.row_size||.01);
           ctx.fillStyle=isPoc?"rgba(245,158,11,.94)":inVA?"rgba(72,139,235,.64)":"rgba(76,103,132,.34)";
           ctx.fillRect(profRight-w,Y(p)-rowPx/2,w,Math.max(2.2,rowPx));
         });

         // Tiny POC mark for each completed session.
         const poc=Number(vp.poc);
         if(Number.isFinite(poc)&&poc>=lo&&poc<=hi){
           ctx.strokeStyle="rgba(245,158,11,.78)";ctx.lineWidth=1.15;
           ctx.beginPath();ctx.moveTo(profLeft,Y(poc));ctx.lineTo(profRight,Y(poc));ctx.stroke();
         }
       }
       i=j+1;
     }
   }
   ctx.restore();
 }

 // -------------------- Candles + volume --------------------
 const maxVol=Math.max(1,...rows.map(r=>Number(r.volume||0)));
 const bw=Math.max(2,Math.min(11,spacing*.34));
 rows.forEach((r,i)=>{
   const x=X(i),o=Number(r.open??r.close),cl=Number(r.close),up=cl>=o;
   const bull="#18c98b",bear="#f04b4b";
   ctx.strokeStyle=up?bull:bear;ctx.fillStyle=up?bull:bear;ctx.lineWidth=1.15;
   if(r.high!=null&&r.low!=null){
     ctx.beginPath();ctx.moveTo(x,Y(Number(r.high)));ctx.lineTo(x,Y(Number(r.low)));ctx.stroke();
   }
   const yo=Y(o),yc=Y(cl),top=Math.min(yo,yc),h=Math.max(2,Math.abs(yc-yo));
   ctx.fillRect(x-bw/2,top,bw,h);
   const vh=Number(r.volume||0)/maxVol*volH;
   ctx.globalAlpha=.50;ctx.fillRect(x-bw/2,H-pad.b-vh,bw,vh);ctx.globalAlpha=1;
 });
 ctx.strokeStyle="#1a2935";ctx.beginPath();ctx.moveTo(pad.l,H-pad.b-volH-volGap/2);ctx.lineTo(W-pad.r,H-pad.b-volH-volGap/2);ctx.stroke();

 // -------------------- Single current/previous-session mode --------------------
 if(singleVp?.bins?.length && previewVPMode!=="auto"){
   const bins=singleVp.bins.filter(b=>Number.isFinite(Number(b.price))&&Number.isFinite(Number(b.volume)));
   const maxV=Math.max(1,...bins.map(b=>Number(b.volume)||0));
   const xRight=W-pad.r-4,xLeft=xRight-Math.round(W*.28);
   ctx.save();
   ctx.fillStyle="rgba(7,16,24,.52)";ctx.fillRect(xLeft-8,pad.t,xRight-xLeft+12,priceBottom-pad.t);
   bins.forEach(b=>{
     const p=Number(b.price);if(p<lo||p>hi)return;
     const w=(Number(b.volume)||0)/maxV*(xRight-xLeft-8);
     const rowPx=Math.max(2.2,Number(singleVp.row_size||.01)*(priceBottom-pad.t)/(hi-lo));
     const inVA=p>=Number(singleVp.val)&&p<=Number(singleVp.vah);
     const isPoc=Math.abs(p-Number(singleVp.poc))<=Number(singleVp.row_size||.01);
     ctx.fillStyle=isPoc?"rgba(245,158,11,.92)":inVA?"rgba(68,132,232,.68)":"rgba(60,90,124,.42)";
     ctx.fillRect(xRight-w,Y(p)-rowPx/2,w,rowPx);
   });
   function rail(value,color,label,dash=[],extend=false){
     const n=Number(value);if(!Number.isFinite(n)||n<lo||n>hi)return;
     const y=Y(n);ctx.setLineDash(dash);ctx.strokeStyle=color;ctx.lineWidth=1.3;
     ctx.beginPath();ctx.moveTo(extend?pad.l:xLeft,y);ctx.lineTo(xRight,y);ctx.stroke();ctx.setLineDash([]);
     ctx.fillStyle=color;ctx.textAlign="right";ctx.font="bold 9px ui-monospace, SFMono-Regular, Menlo, monospace";
     ctx.fillText(`${label} ${n.toFixed(2)}`,xRight-4,y-3);
   }
   rail(singleVp.vah,"#a78bfa","VAH",[5,4],false);
   rail(singleVp.poc,"#f59e0b","POC",[],true);
   rail(singleVp.val,"#60a5fa","VAL",[5,4],false);
   ctx.restore();
 }

 // Latest profile values used by header badges when Per Session is active.
 let activeLatest=null,activeLatestDate=null;
 if(previewVPMode==="auto"){
   if(previewTimeframe==="1w"){
     const last=rows[rows.length-1],wk=weekKey(last);activeLatest=weekMap.get(wk)||null;if(activeLatest)activeLatestDate=wk;
   }else{
     for(let i=rows.length-1;i>=0&&!activeLatest;i--){const dk=rowDateKey(rows[i]);const found=sessionMap.get(dk);if(found){activeLatest=found;activeLatestDate=dk;}}
   }
 }else if(previewVPMode==="session"){
   const sessions=visibleProfiles.sessions||[];if(sessions.length)activeLatestDate=sessions[sessions.length-1].date;
 }else if(previewVPMode==="previous"){
   const sessions=visibleProfiles.sessions||[];if(sessions.length>=2)activeLatestDate=sessions[sessions.length-2].date;
 }

 // Current price badge.
 const lastPx=Number(rows[rows.length-1].close),firstPx=Number(rows[0].close),chg=(lastPx/firstPx-1)*100;
 if(Number.isFinite(lastPx)){
   const py=Y(lastPx);ctx.save();ctx.font="bold 10px ui-monospace, SFMono-Regular, Menlo, monospace";
   const label=`$${lastPx.toFixed(2)}`,tw=ctx.measureText(label).width+12;
   // Keep the badge in the dedicated right-axis gutter so it never covers the final candle/profile.
   const bx=Math.min(W-tw-6, W-pad.r+10),by=Math.max(pad.t,Math.min(priceBottom-18,py-9));
   ctx.fillStyle=chg>=0?"#d9fbe8":"#ffe0e0";ctx.strokeStyle=chg>=0?"#2f9e6d":"#b94a4a";
   if(ctx.roundRect){ctx.beginPath();ctx.roundRect(bx,by,tw,18,4);ctx.fill();ctx.stroke()}else{ctx.fillRect(bx,by,tw,18);ctx.strokeRect(bx,by,tw,18)}
   ctx.fillStyle=chg>=0?"#0f5132":"#7f1d1d";ctx.textAlign="center";ctx.fillText(label,bx+tw/2,by+12);
   ctx.setLineDash([3,3]);ctx.strokeStyle="rgba(226,232,240,.28)";ctx.lineWidth=1;
   ctx.beginPath();ctx.moveTo(X(rows.length-1)+bw*.8,py);ctx.lineTo(bx-4,py);ctx.stroke();ctx.setLineDash([]);
   ctx.restore();
 }
 ctx.font="bold 11px ui-monospace, SFMono-Regular, Menlo, monospace";ctx.fillStyle=chg>=0?"#7ee2ad":"#f38b8b";ctx.textAlign="left";
 ctx.fillText(`${lastPx.toFixed(2)}  ${chg>=0?"+":""}${chg.toFixed(2)}%`,pad.l,H-16);

 // Expose the last visible per-session profile for the top badges/stats.
 payload._activeSessionProfile=activeLatest||singleVp||null;
 payload._activeSessionDate=activeLatestDate;
}


function drawBasicPricePreview(payload){
 // Minimal Safari-safe fallback. If an advanced SVP/canvas feature fails, the user
 // still gets a readable OHLC chart instead of a blank panel.
 const c=document.getElementById("pricePreviewChart"),ctx=c?.getContext("2d");
 if(!c||!ctx)return;
 const rows=(payload?.bars||[]).filter(r=>Number.isFinite(Number(r.close)));
 const W=c.width,H=c.height;ctx.clearRect(0,0,W,H);ctx.fillStyle="#071018";ctx.fillRect(0,0,W,H);
 if(!rows.length){ctx.fillStyle="#94a3b8";ctx.font="12px sans-serif";ctx.fillText("Chart data unavailable",24,32);return;}
 const pad={l:62,r:34,t:28,b:42},highs=rows.map(r=>Number(r.high??r.close)),lows=rows.map(r=>Number(r.low??r.close));
 let lo=Math.min(...lows),hi=Math.max(...highs),rg=Math.max(.01,hi-lo);lo-=rg*.05;hi+=rg*.05;
 const X=i=>pad.l+(i+.5)*(W-pad.l-pad.r)/rows.length,Y=v=>pad.t+(hi-v)/(hi-lo)*(H-pad.t-pad.b),sp=(W-pad.l-pad.r)/rows.length,bw=Math.max(1.5,Math.min(8,sp*.45));
 rows.forEach((r,i)=>{const o=Number(r.open??r.close),cl=Number(r.close),h=Number(r.high??cl),l=Number(r.low??cl),up=cl>=o,x=X(i);ctx.strokeStyle=ctx.fillStyle=up?"#18c98b":"#f04b4b";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,Y(h));ctx.lineTo(x,Y(l));ctx.stroke();ctx.fillRect(x-bw/2,Math.min(Y(o),Y(cl)),bw,Math.max(1,Math.abs(Y(o)-Y(cl))));});
 ctx.fillStyle="#94a3b8";ctx.font="11px sans-serif";ctx.textAlign="left";ctx.fillText("Basic chart fallback · advanced volume-profile rendering unavailable",pad.l,H-14);
}

function updatePreviewVPStatus(){
 const el=document.getElementById("previewVPStatus"); if(!el)return;
 ["vpAuto","vpOff","vpSession","vpPrevious"].forEach(id=>document.getElementById(id)?.classList.remove("active"));
 document.getElementById(previewVPMode==="auto"?"vpAuto":previewVPMode==="off"?"vpOff":previewVPMode==="session"?"vpSession":"vpPrevious")?.classList.add("active");
 ["tf1H","tf4H","tf1D","tf1W"].forEach(id=>document.getElementById(id)?.classList.remove("active"));
 document.getElementById(previewTimeframe==="1h"?"tf1H":previewTimeframe==="4h"?"tf4H":previewTimeframe==="1w"?"tf1W":"tf1D")?.classList.add("active");

 if(previewVPMode==="off"){el.textContent="Volume Profile: Off";return;}

 if(previewVPMode==="auto"){
   const v=previewPayload?.visible_profiles||{};
   const count=previewTimeframe==="1w"?(v.weeks||[]).length:(v.sessions||[]).length;
   const src=v.source||"lower timeframe";
   el.textContent=`Per-session SVP · ${count} ${previewTimeframe==="1w"?"weekly":"RTH session"} profiles · ${src} source · 68% value area`;
   return;
 }

 const p=previewPayload?.volume_profiles||{};
 const vp=previewVPMode==="session"?p.session:p.previous;
 const label=previewVPMode==="session"?"Session":"Previous Session";
 if(vp){
   el.textContent=`${label} · POC $${Number(vp.poc).toFixed(2)} · VAH $${Number(vp.vah).toFixed(2)} · VAL $${Number(vp.val).toFixed(2)} · ${vp.source||"Alpaca"}`;
 }else{
   el.textContent=`${label} VP unavailable${p.error?` · ${p.error}`:""}`;
 }
}
function setPreviewVPMode(mode){
 previewVPMode=mode;
 if(previewPayload)drawPricePreview(previewPayload);
 updatePreviewVPStatus();
}

function setPreviewTimeframe(tf){
 previewTimeframe=tf;
 ["tf1H","tf4H","tf1D","tf1W"].forEach(id=>document.getElementById(id)?.classList.remove("active"));
 document.getElementById(tf==="1h"?"tf1H":tf==="4h"?"tf4H":tf==="1w"?"tf1W":"tf1D")?.classList.add("active");
 const st=document.getElementById("previewStatus");if(st)st.textContent=`Loading ${tf.toUpperCase()}…`;
 if(previewTicker)loadChartPreview(previewTicker,previewPeriod);
}

async function loadChartPreview(ticker,period=previewPeriod){
 ticker=normalizeStockTicker(ticker);
 if(!isSafeStockTicker(ticker))return false;
 if(!ticker)return;
 previewTicker=ticker;previewPeriod=period;
 const seq=++previewRequestSeq,st=document.getElementById("previewStatus"),title=document.getElementById("previewTitle");
 if(title)title.textContent=`${ticker} · Chart Preview`;
 if(st)st.textContent=`Loading ${previewTimeframe.toUpperCase()} · ${period.toUpperCase()}…`;
 document.getElementById("preview1M")?.classList.toggle("active",period==="1m");
 document.getElementById("preview3M")?.classList.toggle("active",period==="3m");
 document.getElementById("preview6M")?.classList.toggle("active",period==="6m");
 try{
   const j=await safeTickerFetchJson("/api/chart-preview",ticker,{period,timeframe:previewTimeframe},{ttl:30000});
   if(seq!==previewRequestSeq)return;
   previewPayload=j;previewTimeframe=(j.timeframe||previewTimeframe).toLowerCase();
   try{drawPricePreview(j);}catch(renderErr){console.error("Advanced chart render failed",ticker,renderErr);drawBasicPricePreview(j);}
   const valueSig=classifyValueAcceptance(j);
   if(ticker){valueAcceptanceMap[String(ticker).toUpperCase()]=valueSig;valueMigrationMap[String(ticker).toUpperCase()]=j.value_migration||null;}
   renderValueAcceptance(valueSig);
   renderTopSetups();
   const bars=j.bars||[],last=bars[bars.length-1],first=bars[0];
   const lp=document.getElementById("previewLastPrice"),meta=document.getElementById("previewMeta");
   if(lp&&last){const ch=first&&Number(first.close)?(Number(last.close)/Number(first.close)-1)*100:0;lp.textContent=`$${Number(last.close).toFixed(2)}  ${ch>=0?"+":""}${ch.toFixed(2)}%`;lp.style.color=ch>=0?"#7ee2ad":"#f38b8b";}
   if(meta)meta.textContent=`${previewTimeframe.toUpperCase()} candles · ${period.toUpperCase()} range · ${bars.length} bars`;
   if(st)st.textContent=`${previewTimeframe.toUpperCase()} · ${period.toUpperCase()} · ${bars.length} bars`;
   updatePreviewVPStatus();
   const p=j.volume_profiles||{};
   let activeVp=j._activeSessionProfile||null;
   if(previewVPMode==="session")activeVp=p.session;
   else if(previewVPMode==="previous")activeVp=p.previous;

   const topVah=document.getElementById("vpVahTop"),topPoc=document.getElementById("vpPocTop"),topVal=document.getElementById("vpValTop"),sessLabel=document.getElementById("vpSessionLabel");
   if(topVah)topVah.textContent=activeVp?`$${Number(activeVp.vah).toFixed(2)}`:"—";
   if(topPoc)topPoc.textContent=activeVp?`$${Number(activeVp.poc).toFixed(2)}`:"—";
   if(topVal)topVal.textContent=activeVp?`$${Number(activeVp.val).toFixed(2)}`:"—";
   if(sessLabel){
     const modeName=previewVPMode==="previous"?"Previous session":previewVPMode==="session"?"Session":"Latest available session";
     sessLabel.textContent=activeVp?`Session: ${modeName}${j._activeSessionDate?` (${j._activeSessionDate})`:""} — may differ from the Value Acceptance card's prior-session reference`:"Session: —";
   }

   if(activeVp?.bins?.length){
     const prices=activeVp.bins.map(b=>Number(b.price)).filter(Number.isFinite);
     const pl=Math.min(...prices),ph=Math.max(...prices),pr=ph-pl,pm=(ph+pl)/2;
     const a=document.getElementById("statSessionRange"),b=document.getElementById("statSessionRangePct"),c=document.getElementById("statSessionVol");
     if(a)a.textContent=`$${pl.toFixed(2)} – $${ph.toFixed(2)}`;
     if(b)b.textContent=`${pr.toFixed(2)} (${pm?((pr/pm)*100).toFixed(2):"0.00"}%)`;
     if(c)c.textContent=fmtCompact(activeVp.total_volume||0);
   }

   if(bars.length){
     const highs=bars.map(x=>Number(x.high)).filter(Number.isFinite),lows=bars.map(x=>Number(x.low)).filter(Number.isFinite),vols=bars.map(x=>Number(x.volume)||0);
     if(highs.length&&lows.length){
       const vl=Math.min(...lows),vh=Math.max(...highs),vr=vh-vl,vm=(vh+vl)/2;
       const a=document.getElementById("statVisibleRange"),b=document.getElementById("statVisibleRangePct");
       if(a)a.textContent=`$${vl.toFixed(2)} – $${vh.toFixed(2)}`;
       if(b)b.textContent=`${vr.toFixed(2)} (${vm?((vr/vm)*100).toFixed(2):"0.00"}%)`;
     }
     const last20=vols.slice(-20),avg20=last20.length?last20.reduce((s,x)=>s+x,0)/last20.length:0;
     const av=document.getElementById("statAvgVol"),vv=document.getElementById("statVsAvgVol");
     if(av)av.textContent=fmtCompact(avg20);
     if(vv&&avg20){const cur=vols[vols.length-1]||0,pct=(cur/avg20-1)*100;vv.textContent=`${pct>=0?"+":""}${pct.toFixed(1)}% vs avg`;vv.style.color=pct>=0?"#4ade80":"#fb7185";}
   }
 }catch(e){
   if(seq!==previewRequestSeq)return;
   console.error("Chart preview request failed",ticker,e);if(st)st.innerHTML=`<span class="error">${ticker} chart: ${e?.message||e}</span>`;
   drawPricePreview({bars:[]});
 }
}

function heatTone(score){return `h${Math.max(0,Math.min(10,Math.round(score||0)))}`;}
function _rrgEndpoint(src){
 const pts=src?.tail||[]; const p=pts.length?pts[pts.length-1]:null;
 return p&&Number.isFinite(Number(p.x))&&Number.isFinite(Number(p.y))?{x:Number(p.x),y:Number(p.y)}:null;
}
function _meanStd(vals){
 const a=vals.filter(Number.isFinite); if(!a.length)return {mean:100,sd:1};
 const mean=a.reduce((s,v)=>s+v,0)/a.length;
 const variance=a.reduce((s,v)=>s+(v-mean)*(v-mean),0)/Math.max(1,a.length);
 return {mean,sd:Math.max(.18,Math.sqrt(variance))};
}
function sectorHeatScore(x){
 // True relative composite: compare the current Fast/Trend RS and momentum
 // coordinates with peers in the same universe. The old implementation was
 // a coarse stage counter, which could display 0.0 for a sector that was
 // literally in the Leading quadrant.
 const peers=(sectorData||[]).filter(r=>r?.group===x?.group);
 const universe=peers.length>=4?peers:(sectorData||[]);
 const rows=universe.map(r=>({r,f:_rrgEndpoint(r.fast||r),t:_rrgEndpoint(r.trend||{})}));
 const fx=_meanStd(rows.map(o=>o.f?.x)),fy=_meanStd(rows.map(o=>o.f?.y));
 const tx=_meanStd(rows.map(o=>o.t?.x)),ty=_meanStd(rows.map(o=>o.t?.y));
 const f=_rrgEndpoint(x.fast||x),t=_rrgEndpoint(x.trend||{});
 if(!f&&!t)return 5;
 const z=(v,st)=>Number.isFinite(v)?(v-st.mean)/st.sd:0;
 let composite=.35*z(f?.x,fx)+.25*z(f?.y,fy)+.25*z(t?.x,tx)+.15*z(t?.y,ty);
 // Small absolute quadrant anchor prevents "best of a weak group" from
 // looking strong solely because of cross-sectional standardization.
 const qv=q=>({Leading:1,Improving:.45,Weakening:-.45,Lagging:-1}[q]||0);
 composite=.78*composite+.22*(.6*qv((x.fast||x)?.quadrant)+.4*qv(x.trend?.quadrant));
 return Math.max(0,Math.min(10,5+1.8*composite));
}
function heatTagsFor(x,isStock=false){
 const f=x.fast||x||{},t=x.trend||{}; const tags=[];
 if(f.quadrant)tags.push(f.quadrant); if(f.tail_trajectory)tags.push(f.tail_trajectory);
 if(t.quadrant&&t.quadrant!==f.quadrant)tags.push(`Trend ${t.quadrant}`);
 if(isStock){const o=optionScanMap[x.ticker];if(o?.liquidity)tags.push(o.liquidity);if(o?.iv_state)tags.push(o.iv_state);}
 return tags.slice(0,4).map(v=>`<span class="heatTag">${v}</span>`).join("");
}
function renderHeatMap(){
 const sg=document.getElementById("sectorHeatGrid"),tg=document.getElementById("stockHeatGrid"),title=document.getElementById("stockHeatTitle"); if(!sg||!tg)return;
 const gf=document.getElementById("heatGroupFilter")?.value||"core";
 let sectors=(sectorData||[]).filter(x=>gf==="all"||(gf==="core"&&x.group==="Core Sector")||(gf==="industry"&&x.group==="Industry / Theme"));
 sectors=[...sectors].sort((a,b)=>sectorHeatScore(b)-sectorHeatScore(a));
 sg.innerHTML=sectors.length?sectors.map(x=>{const sc=sectorHeatScore(x),st=rotationStage(x);return `<div class="heatTile ${heatTone(sc)} ${currentSector===x.ticker?'selected':''}" data-heat-sector="${x.ticker}"><div class="heatHead"><div><div class="heatTicker">${x.ticker}</div><div class="tiny">${x.name||x.group||''}</div></div><div class="heatScore">${sc.toFixed(1)}</div></div><div class="heatMeta">${st.label} · Fast ${x.fast?.quadrant||'—'} · Trend ${x.trend?.quadrant||'—'}</div><div class="heatTags">${heatTagsFor(x,false)}</div></div>`}).join(""):'<div class="note">Refresh market data to build the sector heat map.</div>';
 document.querySelectorAll("[data-heat-sector]").forEach(el=>el.addEventListener("click",async()=>{
   const t=el.dataset.heatSector,hs=document.getElementById("heatStatus");
   try{await selectSector(t,{source:"heat",scrollToStocks:true});}
   catch(e){if(hs)hs.innerHTML=`<span class="error">${(e&&e.message)?e.message:String(e)}</span>`;}
 }));
 const stocks=filteredLiveStocks?filteredLiveStocks():(liveStockData||[]); if(title)title.textContent=currentSector?`${currentSector} · Stock Map`:'Stock Map';
 tg.innerHTML=stocks.length?[...stocks].sort((a,b)=>opportunityScore(b)-opportunityScore(a)).map(x=>{const sc=opportunityScore(x),o=optionScanMap[x.ticker],flow=(activeFlowData?.ticker===x.ticker)?activeFlowData:null;let meta=`${rotationStage(x).label}`;if(o?.liquidity)meta+=` · ${o.liquidity}`;if(flow)meta+=` · Flow ${moneyShort(flow.institutional_premium||0)}`;const va=valueAcceptanceMap[x.ticker];if(va)meta+=` · ${va.state}`;return `<div class="heatTile ${heatTone(sc)}" data-heat-stock="${x.ticker}"><div class="heatHead"><div><div class="heatTicker">${x.ticker}</div><div class="tiny">${x.fast?.quadrant||x.quadrant||'—'}</div></div><div class="heatScore">${sc.toFixed(1)}</div></div><div class="heatMeta">${meta}</div><div class="heatTags">${heatTagsFor(x,true)}</div></div>`}).join(""):'<div class="note">Click a sector/group tile above to load its stock opportunity map.</div>';
 document.querySelectorAll("[data-heat-stock]").forEach(el=>el.addEventListener("click",async()=>{
 const ticker=normalizeStockTicker(el.dataset.heatStock);
 const hs=document.getElementById("heatStatus");
 document.querySelectorAll("[data-heat-stock]").forEach(n=>n.classList.toggle("selected",normalizeStockTicker(n.dataset.heatStock)===ticker));
 if(hs)hs.textContent=`Opening ${ticker} chart + positioning…`;
 activateViewById("rotation");
 await openSectorStockTicker(ticker,{scroll:true});
 if(hs)hs.textContent=`${ticker} loaded`;
}));
}
const premiumSupportMap=window.premiumSupportMap||(window.premiumSupportMap={});
function premiumDirectionFor(x){
 const va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker];
 return (va?.direction&&va.direction!=="neutral")?va.direction:((strat?.continuity==="bullish"||strat?.continuity==="bearish")?strat.continuity:null);
}
function sleepMs(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function fetchPremiumSupportReliable(ticker,direction,attempts=3){
 const delays=[0,1200,3500];let lastErr=null;
 for(let i=0;i<attempts;i++){
   if(delays[i])await sleepMs(delays[i]);
   try{
     const url=`/api/premium-support/${encodeURIComponent(ticker)}?direction=${encodeURIComponent(direction)}`;
     const r=await fetch(url,{headers:{"Accept":"application/json"},cache:"no-store"});
     const raw=await r.text();let j={};
     try{j=raw?JSON.parse(raw):{}}catch(_e){throw new Error(`Unreadable premium response (${r.status})`)}
     if(r.ok&&j.ok)return j;
     const msg=j?.error||`HTTP ${r.status}`;
     if(![429,502,503,504].includes(r.status))throw new Error(msg);
     lastErr=new Error(msg);
   }catch(e){lastErr=e}
 }
 throw lastErr||new Error("Premium support request failed");
}
async function rehydrateMissingPremiumSupport(rows){
 const missing=(rows||[]).filter(x=>{
   const p=premiumSupportMap[x.ticker];
   return !p || p.retryable===true;
 }).slice(0,10);
 if(!missing.length)return;
 for(let n=0;n<missing.length;n+=2){
   await Promise.all(missing.slice(n,n+2).map(async x=>{
     const direction=premiumDirectionFor(x);if(!direction)return;
     try{
       premiumSupportMap[x.ticker]=await fetchPremiumSupportReliable(x.ticker,direction,2);
     }catch(e){premiumSupportMap[x.ticker]={available:false,retryable:true,direction,reason:`Temporary request failure · ${e.message}`};}
   }));
 }
 renderTopSetups();
}
function topSetupEvaluation(x){
 const reasons=[],f=x.fast||x,t=x.trend||{},opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],strat=stratSignalMap[x.ticker],premium=premiumSupportMap[x.ticker];
 let raw=0,premiumAdjustment=0;
 if(x?._earlyPriceSignal){raw+=x._earlyPriceSignal.score||0;reasons.push([x._earlyPriceSignal.kind,"instGood"]);}

 const parent=x._parentGroup||null;
 if(parent){
   const pf=parent.fast||parent,pt=parent.trend||{};
   const pFastIn=(pf?.tail_trajectory ? pf.tail_trajectory==="Rotating In" : (pf?.rs_up===true&&pf?.mom_up===true));
   const pTrendIn=(pt?.tail_trajectory ? pt.tail_trajectory==="Rotating In" : (pt?.rs_up===true&&pt?.mom_up===true));
   const pGood=["Leading","Improving"].includes(pf?.quadrant)&&(["Leading","Improving"].includes(pt?.quadrant)||pTrendIn);
   if(pGood||pFastIn){raw+=10;reasons.push([`${x._parentTicker||"Group"} supportive`,"good"]);}
   else {raw-=6;reasons.push([`${x._parentTicker||"Group"} mixed`,"warn"]);}
 }

 const fq=String(f?.quadrant||""),tq=String(t?.quadrant||"");
 const fIn=(f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory ? t.tail_trajectory==="Rotating In" : (t?.rs_up===true&&t?.mom_up===true));
 const fOut=(f?.tail_trajectory ? f.tail_trajectory==="Rotating Out" : (f?.rs_up===false&&f?.mom_up===false));
 const tOut=(t?.tail_trajectory ? t.tail_trajectory==="Rotating Out" : (t?.rs_up===false&&t?.mom_up===false));
 const fGood=["Improving","Leading"].includes(fq);
 const tGood=["Improving","Leading"].includes(tq);

 // Trajectory is weighted more heavily than the static quadrant label.
 // FULL = both horizons are already favorable and not rolling over.
 // EARLY = Fast is favorable/rotating in while Trend is still Lagging/Weakening
 //         but its tail is turning NE. This is intentionally allowed.
 let align="NONE";
 if(fGood&&tGood&&fIn&&!tOut) align="FULL";
 else if(fIn && (fGood||fq==="Lagging") && tIn && !tOut) align="EARLY";
 else if(fGood&&tGood&&!fOut&&!tOut) align="FULL";

 if(align==="FULL"){raw+=35;reasons.push(["Full RRG alignment","good"]);}
 else if(align==="EARLY"){raw+=32;reasons.push(["Early RRG alignment","good"]);}
 else if(fIn&&!fOut){raw+=18;reasons.push(["Fast rotation only","warn"]);}

 if(fIn){raw+=8;reasons.push(["Fast tail NE","good"]);}
 if(tIn){raw+=8;reasons.push(["Trend tail NE","good"]);}
 if(fOut){raw-=12;reasons.push(["Fast rotating out","warn"]);}
 if(tOut){raw-=15;reasons.push(["Trend rotating out","warn"]);}

 // Static quadrant contributes, but less than trajectory.
 if(fGood)raw+=5;
 if(tGood)raw+=5;
 else if(tIn)reasons.push([`${tq||"Trend"} turning NE`,"good"]);

 // Options: Liquid OR Tradable qualify.
 const liq=opt?.liquidity;
 if(liq==="Liquid"){raw+=12;reasons.push(["Options liquid","good"]);}
 else if(liq==="Tradable"){raw+=9;reasons.push(["Options tradable","good"]);}
 if(opt?.iv_state==="Cheap / Crushed"){raw+=4;reasons.push(["IV attractive","good"]);}
 else if(opt?.iv_state==="Normal")raw+=2;
 else if(opt?.iv_state==="Juiced"){raw-=5;reasons.push(["IV juiced","warn"]);}

 // Contract-level premium support/compression: independent confirmation from the option itself.
 const pc=premium?.best_contract,ps=Number(pc?.premium_support_score);
 if(Number.isFinite(ps)){
   if(ps>=80){premiumAdjustment=6;raw+=premiumAdjustment;reasons.push([`Premium entry attractive · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=65){premiumAdjustment=4;raw+=premiumAdjustment;reasons.push([`Premium near support · ${ps.toFixed(0)}`,"good"]);}
   else if(ps>=50){premiumAdjustment=2;raw+=premiumAdjustment;reasons.push([`Premium entry fair · ${ps.toFixed(0)}`,"warn"]);}
   else if(pc?.state==="AWAY FROM SUPPORT"){premiumAdjustment=-2;raw+=premiumAdjustment;reasons.push(["Premium extended vs support","warn"]);}
 }

 // Value acceptance.
 if(va?.strength==="CONFIRMED"){raw+=13;reasons.push([va.state,"good"]);}
 else if(va?.strength==="DEVELOPING"){raw+=7;reasons.push([va.state,"warn"]);}
 else if(va?.strength==="REJECTION"){raw-=10;reasons.push([va.state,"warn"]);}

 // STRAT.
 let stratPass=false;
 if(strat){
   stratPass=strat.continuity==="bullish"||strat.continuity==="bearish";
   if(stratPass){raw+=9;reasons.push([`${strat.continuity==="bullish"?"Bullish":"Bearish"} STRAT`,"good"]);}
   else reasons.push(["STRAT mixed","warn"]);
 }else reasons.push(["STRAT pending","warn"]);

 // Directional agreement between STRAT and value.
 if(stratPass&&va?.direction&&va.direction!=="neutral"){
   if(strat.continuity===va.direction){raw+=5;reasons.push(["STRAT + value agree","good"]);}
   else {raw-=15;reasons.push(["STRAT/value conflict","warn"]);}
 }

 // GEX confluence: is dealer positioning reinforcing or fighting this setup's
 // direction? Negative/amplifying gamma tends to extend moves; positive/dampening
 // gamma tends to pin them. Room to the nearest wall in the trade direction matters
 // too — a wall sitting right in the path of the expected move caps the upside.
 const pos=opt?.positioning;
 // Only feed a confirmed direction (value acceptance or STRAT) into GEX
 // confluence scoring — bare RRG tail rotation is the weakest directional
 // signal in this function and shouldn't drive a wall/gamma adjustment.
 const tradeDir=(va?.direction&&va.direction!=="neutral")?va.direction:(stratPass?strat.continuity:null);
 if(pos?.available&&tradeDir){
   if(pos.gamma_regime==="Negative / amplifying"){raw+=6;reasons.push(["Negative gamma (amplifying)","good"]);}
   else if(pos.gamma_regime==="Positive / dampening"){raw-=4;reasons.push(["Positive gamma (dampening)","warn"]);}
   const spot=Number(opt.spot),wall=tradeDir==="bullish"?Number(pos.call_wall):Number(pos.put_wall);
   if(Number.isFinite(spot)&&spot>0&&Number.isFinite(wall)){
     const roomPct=tradeDir==="bullish"?((wall-spot)/spot*100):((spot-wall)/spot*100);
     if(roomPct>3){raw+=6;reasons.push(["Room to next wall","good"]);}
     else if(roomPct<=1){raw-=6;reasons.push(["Near gamma wall","warn"]);}
   }
 }

 // Extension is a caution, not an automatic rejection. If price is in a
 // validated continuation phase, preserve the setup and let execution/entry
 // quality decide whether to chase, wait for a base, or enter on a hold.
 const mom=Number(f.rs_momentum??f.momentum),phase=x?._earlyPriceSignal?.phase;
 if(Number.isFinite(mom)&&mom>105){
   if(phase==="CONTINUATION"){raw-=2;reasons.push(["Continuation already underway","good"]);}
   else {raw-=7;reasons.push(["Extended · wait for entry","warn"]);}
 }

 const score=Math.max(0,Math.min(100,Math.round(raw)));
 // Qualification intentionally ignores premium location. Premium can improve or
 // worsen the displayed/ranking score, but it cannot decide whether the
 // underlying setup is allowed to appear.
 const qualificationScore=Math.max(0,Math.min(100,Math.round(raw-premiumAdjustment)));

 // RRG gate is now trajectory based. A Lagging Trend quadrant is allowed when
 // its tail is rotating NE; a Trend rotating out is not.
 const rrgPass=(align==="FULL"||align==="EARLY");
 const ctx=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null;
 const structureOk=ctx?.structure?.plan_valid!==false;
 const hardPass=rrgPass && !fOut && !tOut &&
   (liq==="Liquid"||liq==="Tradable") &&
   va?.strength!=="REJECTION" && structureOk;

 const gateFailures=[];
 if(!rrgPass)gateFailures.push("RRG not aligned");
 if(fOut||tOut)gateFailures.push("Tail rotating out");
 if(!(liq==="Liquid"||liq==="Tradable"))gateFailures.push("Options not liquid/tradable");
 if(va?.strength==="REJECTION")gateFailures.push("Value acceptance rejected");
 if(!structureOk)gateFailures.push(ctx?.structure?.plan_error||"Invalid trade-plan structure");
 if(hardPass&&qualificationScore<45)gateFailures.push(`Underlying setup ${qualificationScore}/100 below the 45 qualification bar`);

 return {score,qualificationScore,reasons,va,stratPass,hardPass,alignment:align,premiumSupport:premium,gateFailures};
}
function groupTrajectoryPass(g){
 const f=g?.fast||g||{},t=g?.trend||{};
 const fIn=(f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory ? t.tail_trajectory==="Rotating In" : (t?.rs_up===true&&t?.mom_up===true));
 const fOut=(f?.tail_trajectory ? f.tail_trajectory==="Rotating Out" : (f?.rs_up===false&&f?.mom_up===false));
 const tOut=(t?.tail_trajectory ? t.tail_trajectory==="Rotating Out" : (t?.rs_up===false&&t?.mom_up===false));
 const fGood=["Leading","Improving"].includes(f?.quadrant),tGood=["Leading","Improving"].includes(t?.quadrant);
 return !fOut&&!tOut && ((fGood&&tGood)||(fIn&&tIn)||(fGood&&tIn));
}
function stockTrajectoryPrefilter(x){
 const f=x?.fast||x||{},t=x?.trend||{};
 const fIn=(f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory ? t.tail_trajectory==="Rotating In" : (t?.rs_up===true&&t?.mom_up===true));
 const fOut=(f?.tail_trajectory ? f.tail_trajectory==="Rotating Out" : (f?.rs_up===false&&f?.mom_up===false));
 const tOut=(t?.tail_trajectory ? t.tail_trajectory==="Rotating Out" : (t?.rs_up===false&&t?.mom_up===false));
 const fGood=["Leading","Improving"].includes(f?.quadrant),tGood=["Leading","Improving"].includes(t?.quadrant);
 return !fOut&&!tOut && ((fGood&&tGood)||(fIn&&tIn)||(fGood&&tIn)||(fIn&&tGood));
}
function preliminaryRRGScore(x){
 const f=x.fast||x,t=x.trend||{};let s=0;
 if(["Leading","Improving"].includes(f?.quadrant))s+=4;
 if(["Leading","Improving"].includes(t?.quadrant))s+=3;
 if((f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up&&f?.mom_up)))s+=3;
 if((t?.tail_trajectory ? t.tail_trajectory==="Rotating In" : (t?.rs_up&&t?.mom_up)))s+=3;
 s+=Math.max(0,Math.min(4,Number(x._parentHeat||0)/2.5));
 return s;
}

function v262DailyReversalSignal(payload){
 const b=(payload?.bars||[]).filter(x=>Number.isFinite(Number(x.close))&&Number.isFinite(Number(x.open))&&Number.isFinite(Number(x.high))&&Number.isFinite(Number(x.low)));
 if(b.length<6)return null;
 const a=b[b.length-3],d1=b[b.length-2],d2=b[b.length-1];
 const aLow=Number(a.low),d1Low=Number(d1.low),d1Close=Number(d1.close),d1Open=Number(d1.open),d2Close=Number(d2.close),d2Open=Number(d2.open),d2High=Number(d2.high);
 const failed2d=d1Low<aLow && d1Close>aLow;
 const green1=d1Close>d1Open,green2=d2Close>d2Open;
 const followThrough=d2Close>d1Close || d2High>Number(d1.high);
 if(failed2d&&green1&&green2&&followThrough)return {kind:'FAILED 2D + 2 GREEN',phase:'EARLY',score:6,detail:'Failed daily 2-down reclaimed the prior low, followed by two green daily bars'};
 if(failed2d&&green1)return {kind:'FAILED 2D REVERSAL',phase:'EARLY',score:4,detail:'Daily 2-down failed and reclaimed the prior low'};
 // Do not throw out a move just because it already started. A clean multi-day
 // breakout with closes near the highs is a continuation candidate, not a late miss.
 const prior=b.slice(-7,-2),priorHigh=Math.max(...prior.map(x=>Number(x.high))),priorCloseHigh=Math.max(...prior.map(x=>Number(x.close)));
 const range2=Math.max(.01,Number(d2.high)-Number(d2.low)),closeNearHigh=(Number(d2.high)-d2Close)/range2<=.30;
 const rising=green1&&green2&&d2Close>d1Close;
 const breakout=rising&&d2Close>priorCloseHigh&&d2High>=priorHigh;
 const vols=b.slice(-12).map(x=>Number(x.volume)).filter(Number.isFinite),curVol=Number(d2.volume),priorVols=vols.slice(0,-1),avgVol=priorVols.length?priorVols.reduce((a,v)=>a+v,0)/priorVols.length:null;
 const volExpansion=avgVol&&Number.isFinite(curVol)?curVol>=avgVol*1.15:false;
 if(breakout&&closeNearHigh)return {kind:volExpansion?'VOLUME CONTINUATION':'CONTINUATION BREAKOUT',phase:'CONTINUATION',score:volExpansion?6:5,detail:`Two green daily bars with a fresh breakout${volExpansion?' on expanding volume':''}`};
 if(rising&&closeNearHigh)return {kind:'TREND CONTINUATION',phase:'CONTINUATION',score:3,detail:'Two green daily bars with higher close and strong close location'};
 return null;
}
function v262EarlyMoveScore(x){
 const sig=x?._earlyPriceSignal;let s=preliminaryRRGScore(x);
 if(sig)s+=Number(sig.score||0);
 const f=x?.fast||x||{},t=x?.trend||{};
 const fIn=(f?.tail_trajectory?f.tail_trajectory==='Rotating In':(f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory?t.tail_trajectory==='Rotating In':(t?.rs_up===true&&t?.mom_up===true));
 if(fIn)s+=2;if(tIn)s+=1;
 return s;
}
function v263OpportunityLane(x){
 const sig=x?._earlyPriceSignal||null,va=valueAcceptanceMap[x?.ticker],st=stratSignalMap[x?.ticker];
 const f=x?.fast||x||{},t=x?.trend||{};
 const fIn=(f?.tail_trajectory?f.tail_trajectory==='Rotating In':(f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory?t.tail_trajectory==='Rotating In':(t?.rs_up===true&&t?.mom_up===true));
 if(sig?.phase==='EARLY')return {lane:'EARLY',detail:sig.kind};
 if(sig?.phase==='CONTINUATION')return {lane:'CONTINUATION',detail:sig.kind};
 const confirmed=(va?.strength==='CONFIRMED')&&(st?.continuity==='bullish'||st?.continuity==='bearish')&&(fIn||tIn);
 if(confirmed)return {lane:'CONFIRMED',detail:'Value + STRAT + rotation aligned'};
 return {lane:'DEVELOPING',detail:'Setup building'};
}
function v263LaneRank(x){return ({EARLY:4,CONTINUATION:3,CONFIRMED:2,DEVELOPING:1}[v263OpportunityLane(x).lane]||0)}
async function runAutomaticTopSetups(force=false){
 // On mobile, suppress only background/automatic scans. An explicit user tap
 // (force=true) is allowed now that the Render service has more headroom.
 const isMobile=!!(window.matchMedia&&window.matchMedia("(max-width: 760px)").matches);
 if(isMobile&&!force){
   const st=document.getElementById("topSetupStatus")||document.getElementById("topSetupsStatus");
   if(st)st.textContent="Top Setups ready · tap Load Top Setups to scan.";
   return;
 }
 const st=document.getElementById("topSetupsStatus");
 if(automaticTopSetupsRunning)return;
 if(!force && globalTopSetupData.length && Date.now()-automaticTopSetupsLastRun<5*60*1000){
   renderTopSetups();return;
 }
 if(!sectorData?.length){if(st)st.textContent="Waiting for market data";return;}

 automaticTopSetupsRunning=true;globalTopSetupData=[];
 if(st)st.textContent="Scanning all sectors + themes…";
 renderTopSetups();

 try{
   // Every sector/theme is considered at Layer 1. Only supportive groups move
   // into the more expensive holdings/options/chart stages.
   const groups=(sectorData||[]).filter(g=>["Core Sector","Industry / Theme"].includes(g.group));
   const supportive=groups.filter(g=>groupTrajectoryPass(g)||strongestLaggingSectors(Math.max(3,groups.length)).some(z=>z.ticker===g.ticker)).sort((a,b)=>sectorHeatScore(b)-sectorHeatScore(a));
   if(st)st.textContent=`Layer 1 · ${supportive.length}/${groups.length} supportive groups`;

   const pool=[];
   // Fetch holdings without changing currentSector/UI selection.
   for(let n=0;n<supportive.length;n+=2){
     const batch=supportive.slice(n,n+2);
     const results=await Promise.all(batch.map(async g=>{
       try{
         const key=cacheKeySector(g.ticker,"20");
         if(clientCache.sectors.has(key))return {g,j:clientCache.sectors.get(key)};
         const r=await fetch(`/api/sector/${encodeURIComponent(g.ticker)}?limit=20`,{headers:{"Accept":"application/json"}});
         const j=await r.json();if(!r.ok||!j.ok)return null;
         clientCache.sectors.set(key,j);return {g,j};
       }catch(e){return null}
     }));
     results.filter(Boolean).forEach(({g,j})=>{
       (j.results||[]).forEach(x=>{
         const f=x?.fast||x||{},t=x?.trend||{};
         const fIn=(f?.tail_trajectory?f.tail_trajectory==="Rotating In":(f?.rs_up===true&&f?.mom_up===true));
         const tIn=(t?.tail_trajectory?t.tail_trajectory==="Rotating In":(t?.rs_up===true&&t?.mom_up===true));
         if(!stockTrajectoryPrefilter(x)&&!fIn&&!tIn)return;
         pool.push({...x,_parentTicker:g.ticker,_parentGroup:g,_parentHeat:sectorHeatScore(g)});
       });
     });
     if(st)st.textContent=`Layer 2 · scanned ${Math.min(n+2,supportive.length)}/${supportive.length} supportive groups`;
   }

   // Deduplicate overlapping ETF holdings; keep the strongest parent-group context.
   const dedupe=new Map();
   pool.forEach(x=>{
     const old=dedupe.get(x.ticker);
     if(!old || Number(x._parentHeat||0)>Number(old._parentHeat||0))dedupe.set(x.ticker,x);
   });
   let candidates=[...dedupe.values()].sort((a,b)=>preliminaryRRGScore(b)-preliminaryRRGScore(a)).slice(0,90);
   if(!candidates.length)throw Error("No stocks passed the market-wide RRG trajectory gate.");

   if(st)st.textContent=`Layer 2.5 · checking early daily reversals on ${candidates.length} candidates`;
   for(let n=0;n<candidates.length;n+=6){
     await Promise.all(candidates.slice(n,n+6).map(async x=>{
       try{
         const r=await fetch(`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`),j=await r.json();
         if(r.ok&&j.ok)x._earlyPriceSignal=v262DailyReversalSignal(j);
       }catch(e){}
     }));
   }
   candidates=candidates.sort((a,b)=>v262EarlyMoveScore(b)-v262EarlyMoveScore(a)).slice(0,60);

   if(st)st.textContent=`Layer 3 · checking options on ${candidates.length} RRG candidates`;
   const or=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols:candidates.map(x=>x.ticker)})});
   const oj=await or.json();
   if(or.ok&&oj.ok)(oj.results||[]).forEach(o=>{if(o?.ticker&&o.ok!==false)optionScanMap[o.ticker]=o});

   candidates=candidates.filter(x=>["Liquid","Tradable"].includes(optionScanMap[x.ticker]?.liquidity));
   if(!candidates.length)throw Error("No RRG candidates passed the Liquid / Tradable options gate.");
   // Persisted for the Early Turn Watch scanner: preliminaryRRGScore actively
   // deprioritizes Lagging-quadrant names, so they rarely survive into the
   // top-16 finalists below even though "still Lagging but the tail just
   // turned NE" is exactly the earlier, more speculative signal that scan
   // wants. Reuse this already-fetched pool instead of a second network pass.
   window.allSupportiveCandidates=candidates;

   // Only the best inexpensive candidates get chart/VP + STRAT resolution.
   const finalists=candidates.sort((a,b)=>{
     const ao=optionScanMap[a.ticker],bo=optionScanMap[b.ticker];
     const aq=(ao?.liquidity==="Liquid"?2:1)+(ao?.iv_state==="Cheap / Crushed"?2:ao?.iv_state==="Normal"?1:0);
     const bq=(bo?.liquidity==="Liquid"?2:1)+(bo?.iv_state==="Cheap / Crushed"?2:bo?.iv_state==="Normal"?1:0);
     return (v262EarlyMoveScore(b)+bq+v263LaneRank(b)*.6)-(v262EarlyMoveScore(a)+aq+v263LaneRank(a)*.6);
   }).slice(0,16);

   if(st)st.textContent=`Layer 4 · resolving STRAT + value on ${finalists.length} finalists`;
   for(let n=0;n<finalists.length;n+=3){
     const batch=finalists.slice(n,n+3);
     await Promise.all(batch.map(async x=>{
       try{
         const [cr,sr]=await Promise.all([
           fetch(`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`),
           fetch(`/api/strat/${encodeURIComponent(x.ticker)}`)
         ]);
         const cj=await cr.json(),sj=await sr.json();
         if(cr.ok&&cj.ok)valueAcceptanceMap[x.ticker]=classifyValueAcceptance(cj);
         if(sr.ok&&sj.ok)stratSignalMap[x.ticker]=sj;
       }catch(e){}
     }));
   }

   // Layer 5: analyze the option premium itself for final directional candidates.
   if(st)st.textContent=`Layer 5 · checking premium support on ${finalists.length} finalists`;
   for(let n=0;n<finalists.length;n+=3){
     const batch=finalists.slice(n,n+3);
     await Promise.all(batch.map(async x=>{
       const direction=premiumDirectionFor(x);
       if(!direction)return;
       try{
         premiumSupportMap[x.ticker]=await fetchPremiumSupportReliable(x.ticker,direction,3);
       }catch(e){
         premiumSupportMap[x.ticker]={available:false,retryable:true,direction,reason:`Temporary request failure · ${e.message}`};
         console.warn("premium support",x.ticker,e);
       }
     }));
   }

   globalTopSetupData=finalists;
   automaticTopSetupsLastRun=Date.now();
   setTimeout(()=>rehydrateMissingPremiumSupport(finalists),1800);
   if(st)st.textContent=`Market-wide scan complete · ${groups.length} groups considered · ${finalists.length} finalists`;
 }catch(e){
   globalTopSetupData=[];
   if(st)st.textContent=`Top Setup scan: ${e.message}`;
 }finally{
   automaticTopSetupsRunning=false;
   renderTopSetups();
 }
}

function openTopSetupDeepDive(ticker,parentTicker=null,target="chart"){
 const sym=String(ticker||"").trim().toUpperCase();
 if(!sym)return;
 const status=document.getElementById("topSetupsStatus");
 if(status)status.textContent=`Opening ${sym}…`;

 try{
   if(target==="gex"){
     const inp=document.getElementById("gexTickerInput");
     if(inp)inp.value=sym;
     activateViewById("gexpage");
     mountGexPage();
     window.scrollTo(0,0);
   }else{
     activateViewById("rotation");
     const el=target==="options"
       ?document.getElementById("optionsPanel")
       :(document.getElementById("stockDeepDiveAnchor")||document.getElementById("pricePreviewChart"));
     safeScrollIntoView(el);
   }
 }catch(navErr){
   console.warn("Immediate navigation warning",navErr);
 }

 // Load each module independently; navigation never waits for these calls.
 Promise.resolve(loadChartPreview(sym)).catch(e=>console.warn("Chart load failed",e));
 Promise.resolve(loadStrat(sym)).catch(e=>console.warn("STRAT load failed",e));
 if(alpacaConfigured!==false){
   Promise.resolve(loadOptionsTicker(sym,{scroll:false})).catch(e=>console.warn("Options load failed",e));
 }

 // Load parent context last, and never let it prevent the ticker from opening.
 if(parentTicker){
   const parent=String(parentTicker||"").trim().toUpperCase();
   if(parent && /^[A-Z0-9.^-]{1,12}$/.test(parent)){
     setTimeout(()=>{
       try{
         currentSector=parent;
         updateSelectedSectorCard(parent);
         const sel=document.getElementById("coreSectorSelect");
         if(sel){
           for(const o of sel.options){if(o.value===parent){sel.value=parent;break;}}
         }
         Promise.resolve(loadSector(false,false)).catch(e=>console.warn("Parent context failed",e));
       }catch(e){console.warn("Parent context failed",e)}
     },0);
   }
 }

 if(target==="gex"){
   setTimeout(()=>{try{mountGexPage();safeScrollIntoView(document.getElementById("positioningSection"))}catch(e){}},250);
 }else if(target==="options"){
   setTimeout(()=>{safeScrollIntoView(document.getElementById("optionsPanel"))},100);
 }else{
   setTimeout(()=>{safeScrollIntoView(document.getElementById("stockDeepDiveAnchor")||document.getElementById("pricePreviewChart"))},100);
 }
 if(status)status.textContent=`${sym} opened`;
}


function setupCompleteness(x,e){
 const c=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null,opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],st=stratSignalMap[x.ticker];
 const structure=c?.structure||{};
 const planOk=structure.plan_valid!==false && Number.isFinite(Number(structure.trigger)) && Number.isFinite(Number(structure.invalidation)) && Number.isFinite(Number(structure.target2));
 const checks={RRG:!!(e?.alignment&&e.alignment!=="NONE"),Value:!!va,STRAT:!!st,Options:!!opt,Context:!!c,TradePlan:planOk,Catalyst:!!(c?.catalyst&&c.catalyst.risk!=="Unknown"),Macro:!!c?.macro_risk};
 const missing=Object.entries(checks).filter(([,v])=>!v).map(([k])=>k);return {complete:missing.length===0,missing};
}
function strongestLaggingSectors(n=3){
 // Sector-level equivalent of earlyTurnQualifies: still reads Lagging, but
 // ranked by how strongly its tail is curling toward Improving relative to
 // peers (sectorHeatScore is already a peer-relative RS-Ratio/Momentum
 // composite). This is the "IGV has the strongest tail of any sector and is
 // heading into Improving" read — a sector-wide rotation signal, distinct
 // from any single stock's own RRG position, and specifically NOT required
 // to already pass groupTrajectoryPass (that gate requires both tails in,
 // which a sector still Lagging on one horizon may not yet satisfy).
 const groups=(sectorData||[]).filter(g=>["Core Sector","Industry / Theme"].includes(g.group));
 return groups.filter(g=>{
   const f=g?.fast||g||{};
   const fIn=(f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up===true&&f?.mom_up===true));
   return String(f?.quadrant||"")==="Lagging" && fIn;
 }).sort((a,b)=>sectorHeatScore(b)-sectorHeatScore(a)).slice(0,n);
}
function earlyTurnQualifies(x){
 const f=x.fast||x,t=x.trend||{};
 const fq=String(f?.quadrant||""),tq=String(t?.quadrant||"");
 const fIn=(f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up===true&&f?.mom_up===true));
 const tIn=(t?.tail_trajectory ? t.tail_trajectory==="Rotating In" : (t?.rs_up===true&&t?.mom_up===true));
 const fOut=(f?.tail_trajectory ? f.tail_trajectory==="Rotating Out" : (f?.rs_up===false&&f?.mom_up===false));
 const tOut=(t?.tail_trajectory ? t.tail_trajectory==="Rotating Out" : (t?.rs_up===false&&t?.mom_up===false));
 // Still reads Lagging on at least one horizon, but that horizon's own tail
 // has turned NE (RS-Ratio and RS-Momentum both rising) — the earliest
 // possible "catch it before the crowd" signal. Deliberately not required to
 // pass Top Setups' Full/Early alignment gate, since that gate specifically
 // requires an already-favorable quadrant — this list exists to catch the
 // turn a step before that confirmation.
 const lagTurning=(fq==="Lagging"&&fIn)||(tq==="Lagging"&&tIn);
 if(!lagTurning)return false;
 // Avoid duplicating Top Setups: if this candidate already earns Full/Early
 // alignment it belongs there, not in a "still lagging" list.
 const fGood=["Improving","Leading"].includes(fq),tGood=["Improving","Leading"].includes(tq);
 const rrgPass=(fGood&&tGood&&fIn&&!tOut)||(fIn&&(fGood||fq==="Lagging")&&tIn&&!tOut)||(fGood&&tGood&&!fOut&&!tOut);
 return !rrgPass;
}
function earlyTurnScore(x){
 const opt=optionScanMap[x.ticker];let s=preliminaryRRGScore(x);
 if(opt?.liquidity==="Liquid")s+=2;
 if(opt?.iv_state==="Cheap / Crushed")s+=2;
 return s;
}
async function runSpeculativeSignals(){
 const st=document.getElementById("speculativeSignalsStatus");
 if(st)st.textContent="Scanning RRG early-turn candidates and sampled large-print activity…";
 await Promise.all([runEarlyTurnWatch(),runInstitutionalRadar()]);
 const earlyCount=earlyTurnWatchData.length;
 const instCount=(institutionalRadarResults||[]).filter(x=>x.ok).length;
 if(st)st.textContent=`${earlyCount} early-turn watch${earlyCount===1?"":"es"} · ${instCount} large-print candidate${instCount===1?"":"s"} across all supportive sectors`;
}
// Auto-collapse this panel once Top Setups actually has qualified picks —
// no reason to keep an unconfirmed/speculative panel expanded and demanding
// attention on a day the main scan already found something solid.
function updateSpeculativeSignalsVisibility(qualifiedCount){
 const el=document.getElementById("speculativeSignalsPanel");
 if(!el)return;
 el.setAttribute("open","");
}
let earlyTurnWatchData=[],earlyTurnWatchRunning=false,earlyTurnSectorContext=null;
async function runEarlyTurnWatch(){
 const st=document.getElementById("speculativeSignalsStatus");
 if(earlyTurnWatchRunning)return;
 earlyTurnWatchRunning=true;
 earlyTurnSectorContext=null;
 const results=[];

 // --- Stock-led: candidates whose OWN tail is turning NE from Lagging. ---
 const stockPool=(window.allSupportiveCandidates||[]).filter(earlyTurnQualifies);
 const stockShortlist=stockPool.sort((a,b)=>earlyTurnScore(b)-earlyTurnScore(a)).slice(0,8);

 // --- Sector-led: the single strongest Lagging-but-turning sector's top
 // holdings, regardless of each stock's own RRG state — this matches the
 // real trade thesis of "the sector itself is the signal" (e.g. IGV turning
 // toward Improving, then picking a liquid name from within it like CRM).
 let sectorShortlist=[];
 const topLaggingSector=strongestLaggingSectors(1)[0]||null;
 if(topLaggingSector){
   earlyTurnSectorContext=topLaggingSector;
   if(st)st.textContent=`Sector signal: ${topLaggingSector.ticker} is the strongest Lagging sector turning toward Improving — checking its top holdings…`;
   try{
     const key=cacheKeySector(topLaggingSector.ticker,"10");
     let sj=clientCache.sectors.has(key)?clientCache.sectors.get(key):null;
     if(!sj){
       const sr=await fetch(`/api/sector/${encodeURIComponent(topLaggingSector.ticker)}?limit=10`);
       sj=await sr.json();
       if(sr.ok&&sj.ok)clientCache.sectors.set(key,sj);
     }
     if(sj?.ok){
       const holdings=(sj.results||[]).filter(h=>h.weight!=null).sort((a,b)=>Number(b.weight)-Number(a.weight)).slice(0,8);
       if(holdings.length){
         const or=await fetch("/api/options-scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbols:holdings.map(h=>h.ticker)})});
         const oj=await or.json();
         if(or.ok&&oj.ok)(oj.results||[]).forEach(o=>{if(o?.ticker&&o.ok!==false)optionScanMap[o.ticker]=o});
         sectorShortlist=holdings.filter(h=>["Liquid","Tradable"].includes(optionScanMap[h.ticker]?.liquidity))
           .map(h=>({...h,_parentTicker:topLaggingSector.ticker}));
       }
     }
   }catch(e){}
 }

 const shortlist=[...stockShortlist.map(x=>({x,source:"stock"})),...sectorShortlist.map(x=>({x,source:"sector"}))];
 if(!shortlist.length){
   if(st)st.textContent="No Lagging-with-turning-tail candidates in the last scan. Run Top Setups first.";
   earlyTurnWatchData=[];earlyTurnWatchRunning=false;renderEarlyTurnWatch();return;
 }
 if(st)st.textContent=`Adding premium entry context to ${shortlist.length} early-turn candidates…`;
 for(let n=0;n<shortlist.length;n+=3){
   const batch=shortlist.slice(n,n+3);
   await Promise.all(batch.map(async({x,source})=>{
     const f=x.fast||x,t=x.trend||{};
     const fq=String(f?.quadrant||""),tq=String(t?.quadrant||"");
     const fIn=(f?.tail_trajectory ? f.tail_trajectory==="Rotating In" : (f?.rs_up===true&&f?.mom_up===true));
     const tIn=(t?.tail_trajectory ? t.tail_trajectory==="Rotating In" : (t?.rs_up===true&&t?.mom_up===true));
     try{
       const r=await fetch(`/api/premium-support/${encodeURIComponent(x.ticker)}?direction=bullish`);
       const j=await r.json();
       const pc=(r.ok&&j.ok)?j.best_contract:null;
       results.push({x,pc,fq,tq,fIn,tIn,source});
     }catch(e){results.push({x,pc:null,fq,tq,fIn,tIn,source})}
   }));
 }
 earlyTurnWatchData=results.sort((a,b)=>earlyTurnScore(b.x)-earlyTurnScore(a.x));
 earlyTurnWatchRunning=false;
 if(st)st.textContent=earlyTurnWatchData.length?`${earlyTurnWatchData.length} early-turn watch${earlyTurnWatchData.length===1?"":"es"}`:"No early-turn candidates currently meet the RRG/sector-turn criteria.";
 renderEarlyTurnWatch();
}
function renderEarlyTurnWatch(){
 const g=document.getElementById("earlyTurnGrid");if(!g)return;
 const sectorNote=earlyTurnSectorContext?`<div class="tiny" style="margin-bottom:8px;color:#7dd3fc">Sector signal: <b>${earlyTurnSectorContext.ticker}</b> is the strongest Lagging sector currently turning toward Improving.</div>`:"";
 if(!earlyTurnWatchData.length){
   g.innerHTML=`${sectorNote}<div class="topSetupsEmpty">No qualifying early-turn candidates right now. This list is intentionally speculative — it looks for names (or whole sectors) still reading Lagging whose RRG tail has just turned NE. Premium support is shown only as entry-quality context and is not required. Run "Check early turns" after Top Setups has scanned.</div>`;
   return;
 }
 g.innerHTML=sectorNote+earlyTurnWatchData.map(({x,pc,fq,tq,fIn,source})=>{
   const tailNote=source==="sector"?`Held in ${x._parentTicker} (sector turning)`:((fq==="Lagging"&&fIn)?"Fast tail turning NE from Lagging":"Trend tail turning NE from Lagging");
   const sourceLabel=source==="sector"?"SECTOR-LED":"STOCK-LED";
   const premiumStateClass=pc?({"REVERSAL CONFIRMED":"instGood","AT SUPPORT":"instGood","NEAR SUPPORT":"instWarn","AWAY FROM SUPPORT":"instBad"}[pc.state]||""):"";
   const premiumLine=pc?`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM ENTRY · <b>${pc.expiration} $${Number(pc.strike).toFixed(0)} ${String(pc.type||"").toLowerCase().startsWith("p")?"P":"C"} · $${Number(pc.mid||0).toFixed(2)} · <span class="${premiumStateClass}">${pc.state}</span></b><div class="tiny">Entry-quality overlay · support $${Number(pc.support_low).toFixed(2)}–$${Number(pc.support_high).toFixed(2)} · score ${Number(pc.premium_support_score||0).toFixed(0)}/100</div></div>`:`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM ENTRY · <b>not required for signal</b></div>`;
   return `<div class="topSetupCard" data-early-turn="${x.ticker}">
     <div class="topSetupHead"><div><div class="topSetupTicker">${x.ticker}</div><div class="topSetupStatus">${sourceLabel} · ${tailNote}${source!=="sector"&&x._parentTicker?` · ${x._parentTicker}`:""}</div></div></div>
${premiumLine}
     <div class="tiny" style="margin-top:6px;color:#8092a4">Speculative — ${source==="sector"?"the sector, not necessarily this stock, is the confirmed signal":"has not yet met the Full/Early RRG alignment bar used for Top Setups"}. Confirmation may still fail.</div>
   </div>`;
 }).join("");
}
function renderTopSetups(){
 const g=document.getElementById("topSetupsGrid"),st=document.getElementById("topSetupsStatus");if(!g)return;
 const source=(globalTopSetupData&&globalTopSetupData.length)?globalTopSetupData:[];
 // Show a useful shortlist rather than only the top two. Keep the hard quality
 // gate, but surface up to six qualified names so the trader can compare setups.
 const evaluated=source.map(x=>({x,e:topSetupEvaluation(x)}));
 const qualified=evaluated.filter(z=>z.e.hardPass&&z.e.qualificationScore>=45).sort((a,b)=>b.e.score-a.e.score);
 // Premium support is an entry-quality overlay, not a setup gate. A strong
 // underlying setup remains visible even when its option premium is not near
 // a historical floor. Liquidity/Tradable status remains a hard contract gate.
 const usingPremiumWatch=false;
 updateSpeculativeSignalsVisibility(qualified.length);
 const rows=qualified.slice(0,6);
 if(st)st.textContent=rows.length?`${rows.length} candidate${rows.length===1?"":"s"} · qualified on underlying setup · premium is entry quality` : "No A-quality setup currently";
 if(!rows.length){
   const msg=automaticTopSetupsRunning?"Scanning all supportive sectors / themes…":"No market-wide A-quality setup currently. Premium state does not determine qualification.";
   // True worst case: nothing qualifies and no premium-support watch exists either.
   // Show the nearest misses by raw score so it's clear whether this is a
   // genuinely quiet market or something is actually broken.
   const nearest=evaluated.sort((a,b)=>b.e.score-a.e.score).slice(0,5);
   const nearestHTML=nearest.length?`<div class="nearestMisses"><div class="tiny" style="margin-top:10px;color:#7f97a8">NEAREST MISSES · why they didn't qualify</div>${
     nearest.map(({x,e})=>`<div class="nearestMissRow"><b>${x.ticker}</b> <span class="tiny">setup ${e.qualificationScore}/100 · ranked ${e.score}/100</span><div class="tiny" style="color:#c98a3a">${(e.gateFailures||[]).join(" · ")||"—"}</div></div>`).join("")
   }</div>`:"";
   g.innerHTML=`<div class="topSetupsEmpty">${msg}</div>${nearestHTML}`;return
 }
 g.innerHTML=rows.map(({x,e},i)=>{
 const va=e.va,complete=setupCompleteness(x,e),label=usingPremiumWatch?"PREMIUM SUPPORT WATCH":(e.score>=80&&va?.strength==="CONFIRMED"&&e.stratPass&&complete.complete?"A+ SETUP":"A-QUALITY WATCH"),alignmentLabel=e.alignment==="EARLY"?"EARLY ALIGNMENT":"FULL ALIGNMENT";
 const pc=e.premiumSupport?.best_contract;
 const premiumStateClass=pc?({"REVERSAL CONFIRMED":"instGood","AT SUPPORT":"instGood","NEAR SUPPORT":"instWarn","CHEAP / UNPROVEN":"instWarn","AWAY FROM SUPPORT":"instBad"}[pc.state]||""):"";
 const floorNote=pc?(pc.floor_reliable===false?"floor unreliable (<2 historical touches)":`${pc.distance_from_support_pct==null?"—":Number(pc.distance_from_support_pct).toFixed(1)+"%"} above floor · ${pc.support_touches||0} tests`):"";
 const premiumHTML=pc?`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>${pc.expiration} $${Number(pc.strike).toFixed(0)} ${String(pc.type||"").toLowerCase().startsWith("p")?"P":"C"} · $${Number(pc.mid||0).toFixed(2)} · <span class="${premiumStateClass}">${pc.state}</span></b><div class="tiny">support $${Number(pc.support_low).toFixed(2)}–$${Number(pc.support_high).toFixed(2)} · ${floorNote} · prior high $${Number(pc.prior_20d_high||0).toFixed(2)} (${pc.prior_expansion_multiple==null?"—":Number(pc.prior_expansion_multiple).toFixed(1)+"×"}) · score ${Number(pc.premium_support_score||0).toFixed(0)}/100</div></div>`:(e.premiumSupport?`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>history unavailable</b><div class="tiny">${e.premiumSupport.reason||"No qualifying historical contract"}</div></div>`:`<div class="topSetupTrigger" style="margin-top:7px">PREMIUM · <b>not evaluated</b></div>`);
 // Trigger must be actionable from CURRENT price. A historical VAH/VAL that price
 // has already cleared by a meaningful amount is context, not a fresh entry trigger.
 const opt=optionScanMap[x.ticker]||{};
 const ctx=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null;
 const structure=ctx?.structure||{};
 const spot=Number(opt.spot??va?.close??structure.spot);
 const vah=Number(va?.vah),val=Number(va?.val);
 let trigger="Load chart for VAH / VAL";
 if(structure.plan_valid===false) trigger="Trade plan invalidated by level-integrity check · re-resolve structure";
 if(va && structure.plan_valid!==false){
   if(va.direction==="bullish"&&Number.isFinite(vah)){
     const ext=Number.isFinite(spot)&&spot>0?(spot-vah)/vah*100:null;
     trigger=ext!=null&&ext>1.0
       ?`Already ${ext.toFixed(1)}% above VAH $${vah.toFixed(2)} · wait for retest/hold or new base`
       :`Hold above VAH $${vah.toFixed(2)}`;
   }else if(va.direction==="bearish"&&Number.isFinite(val)){
     const ext=Number.isFinite(spot)&&spot>0?(val-spot)/val*100:null;
     trigger=ext!=null&&ext>1.0
       ?`Already ${ext.toFixed(1)}% below VAL $${val.toFixed(2)} · wait for retest/rejection or new base`
       :`Hold below VAL $${val.toFixed(2)}`;
   }else if(Number.isFinite(vah)&&Number.isFinite(val)){
     trigger=`Watch VAH $${vah.toFixed(2)} / VAL $${val.toFixed(2)}`;
   }
 }
 return `<div class="topSetupCard ${label==="A+ SETUP"?"aPlus":""}" data-top-setup="${x.ticker}"><div class="topSetupHead"><div><div class="topSetupTicker">${i===0?"★ ":""}${x.ticker}</div><div class="topSetupStatus">${label} · ${alignmentLabel}${x._parentTicker?` · ${x._parentTicker}`:""}</div></div><div class="topSetupScore">${e.score}/100</div></div><div class="topSetupReasons">${e.reasons.slice(0,6).map(r=>`<span class="${r[1]}">${r[0]}</span>`).join("")}</div>${premiumHTML}<div class="topSetupTrigger">TRIGGER · <b>${trigger}</b>${complete.complete?'':`<div class="tiny instWarn">Incomplete: ${complete.missing.join(', ')}</div>`}</div>
<div class="topSetupActions">
  <button class="topSetupAction primaryDive" data-top-open="${x.ticker}" data-parent="${x._parentTicker||""}">Open setup</button>
  <button class="topSetupAction gexDive" data-top-gex="${x.ticker}" data-parent="${x._parentTicker||""}">GEX</button>
  <button class="topSetupAction optionsDive" data-top-options="${x.ticker}" data-parent="${x._parentTicker||""}">Options</button>
</div></div>`}).join("");


 const open=(ticker,parent,target)=>{
   try{ openTopSetupDeepDive(ticker,parent,target); }
   catch(e){
     console.error("Top Setup navigation failed",e);
     const s=document.getElementById("topSetupsStatus");
     if(s)s.textContent=`Open failed: ${e?.message||e}`;
   }
 };
 g.querySelectorAll("[data-top-open]").forEach(btn=>btn.onclick=e=>{e.preventDefault();e.stopPropagation();open(btn.dataset.topOpen,btn.dataset.parent||null,"chart")});
 g.querySelectorAll("[data-top-gex]").forEach(btn=>btn.onclick=e=>{e.preventDefault();e.stopPropagation();open(btn.dataset.topGex,btn.dataset.parent||null,"gex")});
 g.querySelectorAll("[data-top-options]").forEach(btn=>btn.onclick=e=>{e.preventDefault();e.stopPropagation();open(btn.dataset.topOptions,btn.dataset.parent||null,"options")});
 g.querySelectorAll("[data-top-setup]").forEach(card=>card.onclick=e=>{
   if(e.target && e.target.closest && e.target.closest(".topSetupAction"))return;
   const btn=card.querySelector("[data-top-open]");
   open(card.dataset.topSetup,btn?btn.dataset.parent:null,"chart");
 });
}
function opportunityScore(x){
 let score=0;
 const stage=rotationStage(x).level;
 score+=stage*2;
 const opt=optionScanMap[x.ticker];
 if(opt&&opt.ok!==false){
   score+=({Liquid:2,Tradable:1,Thin:0}[opt.liquidity]||0);
   score+=({"Cheap / Crushed":2,"Normal":1,"Elevated":0,"Juiced":-2,"Unknown":0}[opt.iv_state]||0);
 }
 const f=x.fast||x;
 if(f?.tail_trajectory==="Rotating In")score+=1;
 if(f?.tail_trajectory==="Rotating Out")score-=1;
 const va=valueAcceptanceMap[x.ticker];
 if(va)score+=Number(va.score||0);
 return Math.max(0,Math.min(10,score));
}
function opportunityHTML(x){
 const s=opportunityScore(x),stars=Math.max(1,Math.min(5,Math.ceil(s/2)));
 return `<b>${"★".repeat(stars)}${"☆".repeat(5-stars)}</b><div class="tiny">${s}/10 · rotation + options${valueAcceptanceMap[x.ticker]?` + value`:""}</div>`;
}
function renderLiveStocks(){
 const data=filteredLiveStocks();
 const stockState=rrgFocusState["stockChart"];
 if(stockState?.selected&&!data.some(x=>x.ticker===stockState.selected))stockState.selected=null;
 drawRRG("stockChart",data);
 document.getElementById("stockRows").innerHTML=data.map((x,k)=>`<tr class="clickrow liveTickerRow" data-live-ticker="${x.ticker}">
 <td>${liveBookmarkButtonHTML(x.ticker)}</td><td><b>${x.ticker}</b><div class="tiny">${tailBadge(x)}</div></td><td><b>${fmt(x.score,1)}</b></td>
 <td>${compactRRG(x.fast)}</td><td>${compactRRG(x.trend)}</td><td>${rotationStageHTML(x)}<div class="tiny">${alignBadge(x.alignment)}</div></td><td>${opportunityHTML(x)}</td>
 <td>${optionBadgeHTML(optionScanMap[x.ticker])}</td></tr>`).join("");
 document.querySelectorAll("[data-live-bookmark]").forEach(btn=>btn.addEventListener("click",evt=>{evt.stopPropagation();const ticker=btn.dataset.liveBookmark;const x=data.find(r=>r.ticker===ticker)||liveStockData.find(r=>r.ticker===ticker);if(x)toggleLiveWatch(currentLiveWatchItem(x))}));
 document.querySelectorAll("[data-live-ticker]").forEach(row=>row.addEventListener("click",evt=>{
   if(evt.target.closest("[data-live-bookmark]"))return;
   openSectorStockTicker(row.dataset.liveTicker,{scroll:true});
 }));
 refreshLiveBookmarkButtons();syncLiveRowSelection();
 renderInternalRotation();
 renderHeatMap();
 renderTopSetups();
}

function rotationStage(x){
 const f=x?.fast||x||{},t=x?.trend||{};
 const bothFast=!!(f.rs_up&&f.mom_up),bothTrend=!!(t.rs_up&&t.mom_up);
 if((f.quadrant==="Leading"||f.quadrant==="Improving")&&bothFast&&(t.quadrant==="Leading"||t.quadrant==="Improving")&&bothTrend)
   return {level:4,label:"CONFIRMED / ALIGNED"};
 if(f.quadrant==="Improving"&&bothFast)
   return {level:3,label:"CONFIRMED ROTATION"};
 if(f.mom_up&&f.rs_up)
   return {level:2,label:"EARLY ROTATION"};
 if(f.quadrant==="Lagging"&&f.mom_up)
   return {level:1,label:"EARLY TURN"};
 return {level:0,label:x?.alignment||"MIXED"};
}
function rotationStageHTML(x){
 const s=rotationStage(x);
 return `<span class="rotationStage stage${s.level}">${s.level?`${s.level}/4 · `:""}${s.label}</span>`;
}

function renderInternalRotation(){
 const el=document.getElementById("internalRotationCards"),note=document.getElementById("internalRotationNote");
 if(!el)return;
 const data=liveStockData||[];
 if(!currentSector||!data.length){
   el.innerHTML='<div class="card"><div class="tiny">NO GROUP LOADED</div><b>—</b></div>';
   if(note)note.textContent="Choose a sector / industry / theme to measure constituent participation.";
   return;
 }
 const n=data.length;
 const improving=data.filter(x=>["Improving","Leading"].includes(x.fast?.quadrant||x.quadrant)).length;
 const rotating=data.filter(x=>{const s=rotationStage(x);return s.level>=2}).length;
 const aligned=data.filter(x=>rotationStage(x).level>=4).length;
 const early=data.filter(x=>rotationStage(x).level===1).length;
 const p=v=>n?Math.round(v/n*100):0;
 el.innerHTML=`
   <div class="card"><div class="tiny">IMPROVING + LEADING</div><b>${p(improving)}%</b><div class="tiny">${improving}/${n} names</div></div>
   <div class="card"><div class="tiny">ROTATION 2+/4</div><b>${p(rotating)}%</b><div class="tiny">${rotating}/${n} names</div></div>
   <div class="card"><div class="tiny">FULLY ALIGNED</div><b>${p(aligned)}%</b><div class="tiny">${aligned}/${n} names</div></div>
   <div class="card"><div class="tiny">EARLY TURNS</div><b>${p(early)}%</b><div class="tiny">${early}/${n} names</div></div>`;
 if(note)note.textContent=`${currentSector} internal breadth · based on the ${n} holdings currently loaded.`;
}

function alignBadge(a){
 if(!a)return "—";
 const map={"FULL ALIGNMENT":"🔥","STOCK-SPECIFIC LEADER":"⚡","EARLY ROTATION":"🔄","LOSING LEADERSHIP":"⚠️","SHORT-TERM SURGE":"💥","EARLY TURN":"👀","MIXED":"•"};
 return `<span class="flag">${map[a]||""} ${a}</span>`;
}
function compactRRG(r){
 if(!r)return "—";
 return `${badge(r.quadrant)}<div class="tiny">${r.rs_up?"RS↑":"RS↓"} · ${r.mom_up?"Mom↑":"Mom↓"}</div>`;
}
function moverHTML(p){if(!p)return'<span class="mover">LOAD DETAILS</span>';return`<span class="mover m${p.label}">${p.label}</span><div class="tiny">score ${fmt(p.score,1)}/10 · ${p.behavior}</div>`}
function renderEarnings(){
 const f=document.getElementById("moverFilter").value;
 const search=(document.getElementById("earnTickerSearch")?.value||"").trim().toUpperCase();
 let arr=earnResults.filter(x=>{
   const l=(x.profile||{}).label||"UNKNOWN";
   const moverOk=f==="all"||(f==="hm"&&(l==="HIGH"||l==="MODERATE"))||(f==="high"&&l==="HIGH");
   return moverOk&&(!search||String(x.ticker||"").includes(search)||String(x.name||"").toUpperCase().includes(search));
 });
 document.getElementById("earnRows").innerHTML=arr.map((x,k)=>{
   const p=x.profile||{},r=x.rotation||{},c=x.best_contract,id=`det-${x.ticker.replace(/[^A-Z0-9]/g,"")}`;
   const exec=c?.execution_quality||"No executable OTM";
   const execClass=exec==="Wide but Active"?"execWide":(c?"execGood":"optBad");
   const contract=x.options_loading?`<span class="note">Loading OTM contracts…</span>`:(c?`<div class="peContract"><b>${c.expiration}${c.dte==null?"":` (${c.dte}D)`} · ${c.strike}${String(c.type||"").toLowerCase().startsWith("p")?"P":"C"}</b><div class="tiny">$${Number(c.mid||0).toFixed(2)} mid · ${Number(c.otm_pct||0).toFixed(1)}% OTM · Δ ${c.delta==null?"—":Number(c.delta).toFixed(2)}</div><div class="tiny ${execClass}">${exec} · spread ${c.spread_pct==null?"—":Number(c.spread_pct).toFixed(1)+"%"} · OI ${fmtCompact(c.open_interest)} · vol ${fmtCompact(c.volume)}</div><div class="tiny">Historical move coverage: ${c.expected_move_coverage==null?"—":Math.round(c.expected_move_coverage*100)+"%"}</div></div>`:`<span class="optBad">${x.options_execution||"No executable OTM contract"}</span>`);
   const flags=`${p.behavior==="CONTINUATION"?'<span class="histRunner">HISTORICAL RUNNER</span>':""}${p.behavior==="REVERSION"?'<span class="reversionFlag">TENDS TO FADE</span>':""}${x.round_trip?'<span class="givebackFlag">GAVE BACK MOVE</span>':""}`;
   const windowNote=x.drift_window_progress_pct==null?"":`<div class="tiny">Drift window: ${x.drift_window_progress_pct}% of ~${x.drift_window_sessions}D ${x.drift_window_progress_pct>=100?"(tail of move)":"elapsed"}</div>`;
   const surpriseNote=x.eps_surprise_pct==null?"":`<div class="tiny">EPS surprise: ${x.eps_surprise_pct>0?"+":""}${x.eps_surprise_pct}%</div>`;
   return `<tr class="clickrow" data-pe-open="${x.ticker}"><td>${k+1}</td><td><b>${x.ticker}</b><div class="tiny">${x.name||""}</div><div class="peScore">${Number(x.opportunity_score||0).toFixed(0)}/100</div>${flags}</td><td>${x.earnings_date}<div class="tiny">${x.calendar_days_ago}d ago · ${x.direction}</div>${surpriseNote}</td><td>${moverHTML(p)}<div class="tiny">Expected 10–14D excursion: ${fmt(x.expected_continuation_pct)}%</div><div class="tiny">${p.behavior||"—"} · ${p.n||0} events</div></td><td>${x.current?.current_move_pct==null?"—":histPct(x.current.current_move_pct)}<div class="tiny">${compactRRG(r.fast)}</div><div class="tiny">Trend: ${r.trend?`${r.trend.quadrant} · ${r.trend.rs_up?"RS↑":"RS↓"} · ${r.trend.mom_up?"Mom↑":"Mom↓"}`:"—"}</div>${windowNote}</td><td>${contract}</td><td><button class="detailBtn" data-id="${id}" data-ticker="${x.ticker}" data-event="${x.earnings_date}">History ▾</button></td></tr><tr id="${id}" class="details"><td colspan="7">${detailHTML(x)}</td></tr>`;
 }).join("");
 document.querySelectorAll(".detailBtn").forEach(b=>b.addEventListener("click",e=>{e.stopPropagation();document.getElementById(b.dataset.id)?.classList.toggle("open")}));
 document.querySelectorAll("[data-pe-open]").forEach(row=>row.addEventListener("click",e=>{
   if(e.target.closest(".detailBtn"))return;
   openTopSetupDeepDive(row.dataset.peOpen,null,"chart");
 }));
}
function detailHTML(x){
 if(x.historyLoading){
   return `<div class="note">Loading historical earnings profile…</div>`;
 }
 if(x.historyError){
   return `<div class="error">${x.historyError}</div>${x.historyDates&&x.historyDates.length?`<div class="tiny" style="margin-top:6px">Dates found: ${x.historyDates.join(", ")}</div>`:""}`;
 }
 let p=x.profile;
 if(!p)return `<div class="note">Tap Earnings history to load this ticker's historical profile.</div>`;
 let ev=p.events||[];
 return `<div class="detailgrid"><div class="metric"><div class="tiny">EVENTS USED</div><b>${p.n}</b></div><div class="metric"><div class="tiny">MEDIAN 1D EXCURSION</div><b>${fmt(p.median_exc1)}%</b></div><div class="metric"><div class="tiny">MEDIAN 5D EXCURSION</div><b>${fmt(p.median_exc5)}%</b></div><div class="metric"><div class="tiny">MEDIAN 10D EXCURSION</div><b>${fmt(p.median_exc10)}%</b></div><div class="metric"><div class="tiny">MEDIAN 14D EXCURSION</div><b>${fmt(p.median_exc14)}%</b></div><div class="metric"><div class="tiny">&gt;5% WITHIN 10D</div><b>${fmt(p.pct_gt5_10d,0)}%</b></div><div class="metric"><div class="tiny">&gt;10% WITHIN 14D</div><b>${fmt(p.pct_gt10_14d,0)}%</b></div></div><div class="tiny" style="margin:12px 0 6px">Prior completed earnings events · maximum absolute excursion from the pre-event close</div><table class="eventtable"><thead><tr><th>Date</th><th>1D</th><th>3D</th><th>5D</th><th>10D</th><th>14D</th></tr></thead><tbody>${ev.map(e=>`<tr><td>${e.date}</td><td>${fmt(e.exc1)}%</td><td>${fmt(e.exc3)}%</td><td>${fmt(e.exc5)}%</td><td>${fmt(e.exc10)}%</td><td>${fmt(e.exc14)}%</td></tr>`).join("")}</tbody></table>`;
}

async function loadHistory(ticker,eventDate,rowId){
 const item=earnResults.find(x=>x.ticker===ticker);
 if(!item)return;
 item.historyLoading=true;
 item.historyError=null;
 renderEarnings();
 const openRow=document.getElementById(rowId);
 if(openRow)openRow.classList.add("open");

 try{
   const params=new URLSearchParams({event_date:eventDate});
   const response=await fetch(`/api/earnings-history/${encodeURIComponent(ticker)}?${params.toString()}`);
   const raw=await response.text();
   let j;
   try{
      j=JSON.parse(raw);
   }catch(e){
      throw Error(`History service returned an unreadable response (${response.status}).`);
   }

   if(!response.ok || !j.ok){
      const err=new Error(j.error||`History request failed (${response.status})`);
      err.historyDates=j.dates||[];
      throw err;
   }

   item.profile=j.profile;
   item.historyLoading=false;
   item.historyError=null;
   item.historyDates=j.dates||[];
   renderEarnings();
   const det=document.getElementById(rowId);
   if(det)det.classList.add("open");

 }catch(e){
   item.historyLoading=false;
   item.historyError=e.message||"Historical profile could not be loaded.";
   item.historyDates=e.historyDates||[];
   renderEarnings();
   const det=document.getElementById(rowId);
   if(det)det.classList.add("open");
 }
}

async function hydratePostEarningsOptions(){
 const queue=earnResults.filter(x=>x.options_loading);
 let idx=0;
 async function worker(){
   while(idx<queue.length){
     const x=queue[idx++];
     try{
       const q=new URLSearchParams({direction:x.direction||"bullish",expected:String(x.expected_continuation_pct||0)});
       const r=await fetch(`/api/postearnings-option/${encodeURIComponent(x.ticker)}?${q.toString()}`);
       const raw=await r.text();let j;
       try{j=JSON.parse(raw)}catch(e){throw Error(`Options response ${r.status}`)}
       if(!r.ok||!j.ok)throw Error(j.error||"Options unavailable");
       x.best_contract=j.best_contract||null;
       x.options_execution=j.options_execution||"No executable OTM contract";
       x.options_loading=false;
     }catch(e){
       x.options_loading=false;x.options_execution="Options unavailable";
     }
     renderEarnings();
   }
 }
 await Promise.all([worker(),worker(),worker()]);
}

async function runEarnings(){
 const st=document.getElementById("estatus");
 st.textContent="Scanning all sectors/themes for recent earnings opportunities…";
 try{
   const days=document.getElementById("earnDays").value||"5";
   let response=null,raw="",j=null,lastErr=null;
   const waits=[0,2500,6000,12000];
   for(let attempt=0;attempt<waits.length;attempt++){
     if(waits[attempt]){st.textContent=`Earnings service restarted or is busy · retrying ${attempt}/${waits.length-1}…`;await new Promise(r=>setTimeout(r,waits[attempt]));}
     try{
       response=await window.fetch(`/api/postearnings-opportunities?days=${encodeURIComponent(days)}`,{method:"GET",credentials:"same-origin",headers:{"Accept":"application/json"}});
       raw=await response.text();j=null;
       try{j=raw?JSON.parse(raw):null}catch(_e){}
       if(response.ok&&j?.ok)break;
       lastErr=new Error(j?.error||`Scan failed (${response.status})`);
       if(![429,502,503,504].includes(response.status))throw lastErr;
     }catch(e){
       lastErr=e;
       if(attempt===waits.length-1)throw e;
       continue;
     }
   }
   if(!response||!response.ok||!j?.ok){
     if(response&&!j)throw Error(`Earnings service returned an unreadable response (${response.status})`);
     throw lastErr||new Error("Earnings scan failed");
   }
   earnResults=j.results||[];
   st.textContent=`${j.recent_reporters||0} recent reporters · ${j.universe||0} unique holdings scanned · showing ${earnResults.length} curated opportunities`;
   renderEarnings();
   if(j.options_deferred) hydratePostEarningsOptions();
 }catch(e){st.innerHTML=`<span class="error">${e.message}</span>`}
}

let historicalData=[];

function histPct(v){
 if(v===null||v===undefined||Number.isNaN(Number(v)))return "—";
 let n=Number(v);
 return `<span class="${n>0?"pos":n<0?"neg":""}">${n>0?"+":""}${n.toFixed(2)}%</span>`;
}

function filteredHistorical(){
 const q=document.getElementById("histQuadrantFilter")?.value||"all";
 const t=document.getElementById("histTailFilter")?.value||"all";
 const s=(document.getElementById("histTickerSearch")?.value||"").trim().toUpperCase();
 return historicalData.filter(x=>{
   const qok=q==="all"||x.quadrant===q;
   const tok=t==="all"||effectiveTailSignal(x)===t;
   const sok=!s||String(x.ticker||"").toUpperCase().includes(s)||String(x.name||"").toUpperCase().includes(s);
   return qok&&tok&&sok;
 });
}

function renderHistorical(){
 const data=filteredHistorical();

 // If selected ticker is filtered out, clear focus.
 const histState=rrgFocusState["historyChart"];
 if(histState?.selected && !data.some(x=>x.ticker===histState.selected)){
   histState.selected=null;
 }

 drawRRG("historyChart",data);
 document.getElementById("histRows").innerHTML=data.map((x,k)=>{
   let f=x.forward||{};
   return `<tr class="clickrow histTickerRow" data-hist-ticker="${x.ticker}"><td>${k+1}</td><td><b>${x.ticker}</b><div class="tiny">${x.name||""}</div><div class="tiny">${tailBadge(x)}</div></td><td>${compactRRG(x.fast)}</td><td>${compactRRG(x.trend)}</td><td>${histPct(f["1"])}</td><td>${histPct(f["5"])}</td><td>${histPct(f["10"])}</td><td>${histPct(f["20"])}</td></tr>`;
 }).join("");

 document.querySelectorAll("[data-hist-ticker]").forEach(row=>row.addEventListener("click",()=>{
   const ticker=row.dataset.histTicker;
   toggleRRGFocus("historyChart",ticker);
 }));

 syncHistoricalRowSelection();
}

function applyHistoricalPayload(j,fromCache=false){
 historicalData=j.results||[];
 document.getElementById("histDate").value=j.asof;
 document.getElementById("histTitle").textContent=`${j.asof} · ${j.source}`;
 const st=document.getElementById("histStatus");
 const detail=(j.mode==="stocks")
   ?`${j.holdings_as_screened} of ${j.holdings_total} holdings · benchmark ${j.benchmark}`
   :`${historicalData.length} groups · benchmark ${j.benchmark}`;
 st.textContent=(fromCache?"Cached · ":"")+detail;
 const caveatEl=document.getElementById("histCaveat");
 if(caveatEl)caveatEl.textContent=j.caveat||"";
 renderHistorical();
}

async function loadHistorical(force=false){
 const st=document.getElementById("histStatus");
 const mode=document.getElementById("histMode").value;
 const etf=document.getElementById("histETF").value;
 const date=document.getElementById("histDate").value;
 const limit=document.getElementById("histLimit").value;
 if(!date){st.innerHTML='<span class="error">Choose a date.</span>';return}
 const key=cacheKeyHistory(mode,etf,date,limit);
 if(!force&&clientCache.historical.has(key)){applyHistoricalPayload(clientCache.historical.get(key),true);return}
 st.textContent="Reconstructing point-in-time RRG…";
 try{
   const params=new URLSearchParams({mode,etf,date,limit});
   const r=await fetch(`/api/historical-rrg?${params.toString()}`);
   const raw=await r.text();
   let j;
   try{j=JSON.parse(raw)}catch(e){throw Error(`Unreadable historical response (${r.status})`)}
   if(!r.ok||!j.ok)throw Error(j.error||"Historical RRG failed");
   clientCache.historical.set(key,j);
   clientCache.historical.set(cacheKeyHistory(mode,etf,j.asof,limit),j);
   applyHistoricalPayload(j,false);
 }catch(e){
   st.innerHTML=`<span class="error">${e.message}</span>`;
 }
}

function shiftHistDate(days){
 const el=document.getElementById("histDate");
 if(!el.value)return;
 let d=new Date(el.value+"T12:00:00");
 d.setDate(d.getDate()+days);
 el.value=d.toISOString().slice(0,10);
 loadHistorical();
}

document.getElementById("runHistory").addEventListener("click",loadHistorical);
document.getElementById("histQuadrantFilter").addEventListener("change",renderHistorical);
document.getElementById("histTailFilter").addEventListener("change",renderHistorical);
document.getElementById("histTickerSearch").addEventListener("input",renderHistorical);
document.getElementById("histPrev").addEventListener("click",()=>shiftHistDate(-1));
document.getElementById("histNext").addEventListener("click",()=>shiftHistDate(1));
document.getElementById("histMode").addEventListener("change",()=>{
 const groups=document.getElementById("histMode").value==="groups";
 document.getElementById("histETF").disabled=groups;
 document.getElementById("histLimit").disabled=groups;
 document.getElementById("histLimitLabel").style.opacity=groups?.45:1;
 loadHistorical();
});
document.getElementById("histETF").addEventListener("change",loadHistorical);
document.getElementById("histLimit").addEventListener("change",loadHistorical);
document.getElementById("histETF").disabled=true;
document.getElementById("histLimit").disabled=true;
document.getElementById("histLimitLabel").style.opacity=.45;
document.getElementById("histDate").value=new Date().toISOString().slice(0,10);


document.getElementById("heatGroupFilter")?.addEventListener("change",renderHeatMap);
document.getElementById("refreshHeat")?.addEventListener("click",async()=>{const st=document.getElementById("heatStatus");if(st)st.textContent="Refreshing market map…";await loadMarket(true);if(currentSector)await loadSector(true);renderHeatMap();if(st)st.textContent="Updated";});
document.getElementById("gammaLandscape")?.addEventListener("click",inspectGammaLandscape);
document.getElementById("gexTickerLoad")?.addEventListener("click",loadGexPageTicker);
document.getElementById("gexTickerInput")?.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();loadGexPageTicker();}});


let gexHomeParent=null,gexHomeNext=null;
function restoreGexSection(){
  const sec=document.getElementById("positioningSection");
  if(!sec||!gexHomeParent||sec.parentElement===gexHomeParent)return;
  if(gexHomeNext&&gexHomeNext.parentNode===gexHomeParent)gexHomeParent.insertBefore(sec,gexHomeNext);else gexHomeParent.appendChild(sec);
}
function mountGexPage(){
  const sec=document.getElementById("positioningSection"),host=document.getElementById("gexPageHost"),hint=document.getElementById("gexPageHint"),inp=document.getElementById("gexTickerInput");
  if(!sec||!host)return;
  if(!gexHomeParent){gexHomeParent=sec.parentElement;gexHomeNext=sec.nextSibling;}
  host.appendChild(sec);
  if(activeOptionsData?.ticker){sec.style.display="block";if(inp)inp.value=activeOptionsData.ticker;if(hint)hint.style.display="none";}else{sec.style.display="none";if(hint)hint.style.display="block";}
}
async function loadGexPageTicker(){
  const inp=document.getElementById("gexTickerInput"),t=(inp?.value||"").trim().toUpperCase(),hint=document.getElementById("gexPageHint");
  if(!t){if(hint){hint.style.display="block";hint.textContent="Enter a ticker to load its modeled GEX landscape.";}return;}
  if(hint){hint.style.display="block";hint.textContent=`Loading ${t} GEX landscape…`;}
  await loadChartPreview(t,previewPeriod||"1m");
  await loadOptionsTicker(t,{scroll:false});
  mountGexPage();
  if(hint)hint.style.display="none";
}

function jumpToRotationTarget(id){
  activateViewById("rotation");
  setTimeout(()=>document.getElementById(id)?.scrollIntoView({behavior:"smooth",block:"start"}),70);
}
document.getElementById("navOptions")?.addEventListener("click",()=>jumpToRotationTarget("optionsPanel"));


document.getElementById("navWatch")?.addEventListener("click",()=>jumpToRotationTarget("watchlistPanel"));
let earningsAutoOpened=false;
document.getElementById("navEarnings")?.addEventListener("click",evt=>{
 evt.preventDefault();
 activateViewById("earnings");
 window.scrollTo(0,0);
 const rows=document.getElementById("earnRows");
 if(!earningsAutoOpened && (!rows||!rows.children.length)){
   earningsAutoOpened=true;
   Promise.resolve(runEarnings()).catch(e=>{
     const st=document.getElementById("estatus");
     if(st)st.textContent=`Earnings load failed: ${e?.message||e}`;
   });
 }
});


document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",evt=>{
 if(b.id==="navEarnings")return; // explicit handler above owns Earnings navigation
 evt.preventDefault();
 activateViewById(b.dataset.view);
 window.scrollTo(0,0);
}));
document.getElementById("groupFilter").addEventListener("change",renderGroups);document.getElementById("macroBasketFilter").addEventListener("change",renderGroups);document.getElementById("coreSectorSelect").addEventListener("change",(e)=>{
 if(e.target.value)selectSector(e.target.value,{source:"dropdown"});
});document.getElementById("refreshMarket").addEventListener("click",()=>loadMarket(true));document.getElementById("liveHoldingsLimit").addEventListener("change",loadSector);document.getElementById("liveQuadrantFilter").addEventListener("change",renderLiveStocks);document.getElementById("liveTailFilter").addEventListener("change",renderLiveStocks);document.getElementById("preview1M").addEventListener("click",()=>{if(previewTicker)loadChartPreview(previewTicker,"1m")});
document.getElementById("preview3M").addEventListener("click",()=>{if(previewTicker)loadChartPreview(previewTicker,"3m")});
document.getElementById("preview6M").addEventListener("click",()=>{if(previewTicker)loadChartPreview(previewTicker,"6m")});
document.getElementById("vpAuto")?.addEventListener("click",()=>setPreviewVPMode("auto"));
document.getElementById("vpOff")?.addEventListener("click",()=>setPreviewVPMode("off"));
document.getElementById("vpSession")?.addEventListener("click",()=>setPreviewVPMode("session"));
document.getElementById("vpPrevious")?.addEventListener("click",()=>setPreviewVPMode("previous"));
document.getElementById("tf1H")?.addEventListener("click",()=>setPreviewTimeframe("1h"));
document.getElementById("tf4H")?.addEventListener("click",()=>setPreviewTimeframe("4h"));
document.getElementById("tf1D")?.addEventListener("click",()=>setPreviewTimeframe("1d"));
document.getElementById("tf1W")?.addEventListener("click",()=>setPreviewTimeframe("1w"));
document.getElementById("liveTickerSearch").addEventListener("input",handleLiveTickerSearch);
document.getElementById("liveTickerSearch").addEventListener("keydown",async(e)=>{
 if(e.key==="Enter"){
   e.preventDefault();
   clearTimeout(liveSearchTimer);
   await ensureLiveSearchUniverse();
 }
});document.getElementById("refreshSector").addEventListener("click",()=>loadSector(true));
document.getElementById("scanOptions").addEventListener("click",scanVisibleOptions);
document.getElementById("optTypeFilter").addEventListener("change",renderOptionsPanel);
document.getElementById("optLiquidityFilter").addEventListener("change",renderOptionsPanel);
document.getElementById("refreshFlow")?.addEventListener("click",()=>{if(activeOptionsData?.ticker){const force=!!(activeFlowData&&activeFlowData.ticker===activeOptionsData.ticker);loadFlowTicker(activeOptionsData.ticker,force);}});
document.getElementById("refreshLiveWatchlist").addEventListener("click",refreshLiveWatchlistData);
document.getElementById("clearLiveWatchlist").addEventListener("click",async()=>{
 const tickers=liveWatchlist.map(x=>x.ticker);liveWatchlist=[];saveLiveWatchlist();
 await Promise.all(tickers.map(t=>fetch(`/api/watchlist/${encodeURIComponent(t)}`,{method:"DELETE"}).catch(()=>null)));
 syncWatchlistFromServer();
});document.getElementById("runEarnings").addEventListener("click",runEarnings);document.getElementById("moverFilter").addEventListener("change",renderEarnings);document.getElementById("earnTickerSearch").addEventListener("input",renderEarnings);document.getElementById("rrgFastBtn")?.addEventListener("click",()=>setSectorRRGMode("fast"));
document.getElementById("rrgTrendBtn")?.addEventListener("click",()=>setSectorRRGMode("trend"));
document.getElementById("dashboardSectorSelect")?.addEventListener("change",async e=>{if(e.target.value){toggleRRGFocus("sectorChart",e.target.value);await selectSector(e.target.value,{source:"dashboard"})}});
document.querySelectorAll("#sectorQuadPills .filterPill").forEach(btn=>btn.addEventListener("click",()=>{sectorQuadrantFilter=btn.dataset.q||"all";document.querySelectorAll("#sectorQuadPills .filterPill").forEach(x=>x.classList.toggle("active",x===btn));renderGroups();}));
document.getElementById("dashHeatComposite")?.addEventListener("click",()=>{dashboardHeatMode="composite";document.getElementById("dashHeatComposite")?.classList.add("active");document.getElementById("dashHeatFast")?.classList.remove("active");document.getElementById("dashHeatTrend")?.classList.remove("active");renderDashboardHeat();});
document.getElementById("dashHeatFast")?.addEventListener("click",()=>{dashboardHeatMode="fast";document.getElementById("dashHeatFast")?.classList.add("active");document.getElementById("dashHeatComposite")?.classList.remove("active");document.getElementById("dashHeatTrend")?.classList.remove("active");renderDashboardHeat();});
document.getElementById("dashHeatTrend")?.addEventListener("click",()=>{dashboardHeatMode="trend";document.getElementById("dashHeatTrend")?.classList.add("active");document.getElementById("dashHeatFast")?.classList.remove("active");document.getElementById("dashHeatComposite")?.classList.remove("active");renderDashboardHeat();});
document.getElementById("dashRefreshMarket")?.addEventListener("click",()=>loadMarket(true));
document.getElementById("loadTopSetups")?.addEventListener("click",async()=>{
 const b=document.getElementById("loadTopSetups");
 if(b)b.disabled=true;
 try{await runAutomaticTopSetups(true)}finally{if(b)b.disabled=false}
});

function activateViewById(id){
 const wanted=String(id||"");
 document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("active",String(x.dataset.view||"")===wanted));
 document.querySelectorAll(".view").forEach(x=>x.classList.toggle("active",x.id===wanted));
 if(wanted==="heatmap")renderHeatMap();
 if(wanted==="gexpage")mountGexPage();else restoreGexSection();
}

/* v24.2 responsive RRG redraw */
let _rrgResizeTimer=null;
window.addEventListener("resize",()=>{
 clearTimeout(_rrgResizeTimer);
 _rrgResizeTimer=setTimeout(()=>{
   ["sectorChart","stockChart","historyChart"].forEach(id=>{
     const st=rrgFocusState[id];
     if(st?.rows?.length)drawRRG(id,st.rows,st.selected);
   });
 },120);
});

loadLiveWatchlist();renderLiveWatchlist();checkAlpacaStatus();loadMarket(false);
document.getElementById("gexWindow")?.addEventListener("change",()=>{const t=activeOptionsData?.ticker||previewTicker;if(t)loadOptionsTicker(t,{scroll:false});});

// -------------------- v24 Institutional Decision Layer --------------------
const institutionalContextMap={};let activeInstitutionalTicker=null;
function instFmt(v,d=1){const n=Number(v);return Number.isFinite(n)?`${n>0?"+":""}${n.toFixed(d)}%`:"—"}function instMoney(v){const n=Number(v);return Number.isFinite(n)?`$${n.toFixed(2)}`:"—"}
function ensureInstitutionalPanel(){let el=document.getElementById("institutionalDecisionPanel");if(el)return el;el=document.createElement("div");el.id="institutionalDecisionPanel";el.className="panel instDecisionPanel";el.innerHTML='<div class="note">Institutional decision layer loads with the selected ticker.</div>';const anchor=document.getElementById("stockDeepDiveAnchor")||document.getElementById("pricePreviewChart");if(anchor&&anchor.parentNode)anchor.parentNode.insertBefore(el,anchor);return el}
function flowEvidenceFor(ticker){const x=(activeFlowData?.ticker===ticker)?activeFlowData:null;if(!x)return {label:"Pending",score:null,detail:"Load options/flow"};const cp=Number(x.institutional_call_pct),pp=Number(x.institutional_put_pct),cov=Number(x.activity_coverage_pct),high=Number(x.high_relevance_events||0);let score=0;if(Number.isFinite(cov))score+=Math.min(45,cov*.45);score+=Math.min(35,high*7);if((x.institutional_events||0)>0)score+=20;const mix=Number.isFinite(cp)&&cp>=65?"Call-contract concentration":Number.isFinite(pp)&&pp>=65?"Put-contract concentration":"Balanced contract mix";return {label:mix,score:Math.round(Math.min(100,score)),detail:`${Number.isFinite(cp)?cp.toFixed(0)+'% calls / '+pp.toFixed(0)+'% puts · ':''}${x.coverage_confidence||"?"} coverage · ${high} high relevance · ${x.direction_available?"direction classified":"direction unknown"}`}}
function gexImplicationFor(ticker,ctx){const o=(activeOptionsData?.ticker===ticker)?activeOptionsData:optionScanMap[ticker],p=o?.positioning;if(!p?.available)return {label:"Pending",detail:"Load GEX/options positioning"};const dir=ctx?.structure?.direction||"neutral",reg=String(p.gamma_regime||"");let label=reg.includes("Negative")?"Amplifying regime":reg.includes("Positive")?"Dampening regime":"Mixed gamma";if(!["bullish","bearish"].includes(dir))return {label,detail:`${reg||"Dealer gamma available"} · no directional structure confirmed`,room:null};const spot=Number(o?.spot||ctx?.structure?.spot),wall=dir==="bearish"?Number(p.put_wall):Number(p.call_wall);let room=null;if(Number.isFinite(spot)&&spot>0&&Number.isFinite(wall))room=dir==="bearish"?(spot-wall)/spot*100:(wall-spot)/spot*100;let detail=reg||"Dealer gamma unavailable";if(room!=null)detail+=` · ${room.toFixed(1)}% room to ${dir==="bearish"?"put":"call"} wall`;if(reg.includes("Positive")&&room!=null&&room>=0&&room<=1.5)detail+=" · breakout headwind";else if(reg.includes("Negative")&&room!=null&&room>2)detail+=" · continuation can accelerate";return {label,detail,room}}
function histExpectancyLabel(h){if(!h||!h.count)return {label:"Exact setup N=0",detail:"Building a clean signature-specific sample"};const r=h.returns?.["5"]||{};return {label:`Exact setup N=${h.count}`,detail:`5D win ${r.win_rate==null?"—":r.win_rate+"%"} · median ${r.median==null?"—":r.median+"%"}`}}
function expectancyFactor(h){const r=h?.returns?.["5"]||{},n=Number(r.n||0),wr=Number(r.win_rate),med=Number(r.median);if(n<5)return 5;if(wr>=65&&med>0)return 10;if(wr>=55&&med>0)return 8;if(wr>=50&&med>=0)return 6;if(wr<45||med<0)return 3;return 5}
function factorBreakdownFor(x,b,c){const rs=c?.relative_strength||{},p=c?.rotation_persistence,cat=c?.catalyst||{},hist=c?.historical_expectancy||{},flow=flowEvidenceFor(x.ticker),gx=gexImplicationFor(x.ticker,c),r5=rs["5"]||{},r20=rs["20"]||{};return [["Rotation",b.alignment==="FULL"?10:b.alignment==="EARLY"?9:5],["Market RS",r20.vs_spy>0?10:r5.vs_spy>0?7:3],["Sector RS",r20.vs_parent>0?10:r5.vs_parent>0?7:c?.parent?3:5],["Persistence",p==null?5:Math.round(p/10)],["Structure",c?.structure?.trend_strength==null?5:Math.min(10,c.structure.trend_strength*2.5)],["Flow",flow.score==null?5:Math.round(flow.score/10)],["GEX",gx.detail.includes("headwind")?3:gx.detail.includes("accelerate")?9:6],["Execution",optionScanMap[x.ticker]?.liquidity==="Liquid"?10:optionScanMap[x.ticker]?.liquidity==="Tradable"?8:4],["Expectancy",expectancyFactor(hist)],["Catalyst",cat.days_to_earnings==null?5:cat.days_to_earnings<=3?1:cat.days_to_earnings<=10?5:9]]}
async function loadInstitutionalContext(ticker,parent=null,quiet=false){
 ticker=normalizeStockTicker(ticker);if(!ticker)return null;activeInstitutionalTicker=ticker;
 const el=ensureInstitutionalPanel();if(el&&!quiet)el.innerHTML=`<div class="note">Loading ${ticker} institutional decision layer…</div>`;
 try{
   const j=await safeTickerFetchJson("/api/institutional-context",ticker,parent?{parent}: {},{ttl:60000});
   institutionalContextMap[ticker]=j;if(activeInstitutionalTicker===ticker)renderInstitutionalContext(ticker);renderTopSetups();return j
 }catch(e){if(el&&!quiet)el.innerHTML=`<span class="warn">Institutional layer: ${e.message}</span>`;return null}
}
function renderInstitutionalContext(ticker){const c=institutionalContextMap[ticker],el=ensureInstitutionalPanel();if(!c||!el)return;const rs=c.relative_strength||{},s=c.structure||{},cat=c.catalyst||{},macro=c.macro_risk||{},flow=flowEvidenceFor(ticker),gx=gexImplicationFor(ticker,c),hist=histExpectancyLabel(c.historical_expectancy),r5=rs["5"]||{},r10=rs["10"]||{},r20=rs["20"]||{},ct=cat.next_earnings?`${cat.next_earnings} · ${cat.days_to_earnings}d`:'No confirmed earnings date available',cc=cat.days_to_earnings==null?'instWarn':cat.days_to_earnings<=3?'instBad':cat.days_to_earnings<=10?'instWarn':'instGood';el.innerHTML=`<div class="instDecisionHead"><div><h3>${ticker} · Institutional Decision Layer</h3><div class="tiny">Observe → Rank → Explain → Execute → Invalidate → Measure</div></div><span class="horizon">${c.horizon||"—"}</span></div><div class="instGrid"><div class="instCard"><div class="k">ROTATION PERSISTENCE</div><div class="v ${Number(c.rotation_persistence)>=67?'instGood':''}">${c.rotation_persistence==null?'—':c.rotation_persistence+'/100'}</div><div class="d">5/10/20D relative leadership${c.triple_relative_strength?' · triple RS confirmed':''}</div></div><div class="instCard"><div class="k">RELATIVE STRENGTH</div><div class="v">${c.parent?`${instFmt(r20.vs_parent)} vs ${c.parent}`:instFmt(r20.vs_spy)}</div><div class="d">20D vs SPY ${instFmt(r20.vs_spy)} · 5D ${instFmt(r5.vs_spy)}</div></div><div class="instCard"><div class="k">FLOW EVIDENCE</div><div class="v">${flow.label}</div><div class="d">${flow.detail}</div></div><div class="instCard"><div class="k">GEX TRADE EFFECT</div><div class="v">${gx.label}</div><div class="d">${gx.detail}</div></div><div class="instCard"><div class="k">CATALYST RISK</div><div class="v ${cc}">${cat.risk||"Unknown"}</div><div class="d">${ct}</div></div><div class="instCard"><div class="k">MACRO RISK</div><div class="v ${macro.risk==="HIGH"?'instBad':macro.risk==="ELEVATED"?'instWarn':''}">${macro.risk||"—"}</div><div class="d">${(macro.events||[]).slice(0,2).map(e=>`${e.days_away}d · ${e.type} ${e.time||''}`).join(' · ')||'No major event in 7D'}</div></div><div class="instCard"><div class="k">HISTORICAL EXPECTANCY</div><div class="v">${hist.label}</div><div class="d">${hist.detail}</div></div><div class="instCard"><div class="k">PRICE STRUCTURE</div><div class="v">${s.direction||"—"}</div><div class="d">Trend ${s.trend_strength??'—'}/4 · ATR ${instMoney(s.atr14)}</div></div><div class="instCard"><div class="k">R:R TO TARGET 2</div><div class="v">${s.rr_to_target2==null?'—':Number(s.rr_to_target2).toFixed(1)+'×'}</div><div class="d">Structure/volatility heuristic</div></div></div><div class="instSection"><div class="instSectionTitle">RELATIVE LEADERSHIP</div><div class="instFactors"><div class="instFactor">5D vs SPY<strong>${instFmt(r5.vs_spy)}</strong></div><div class="instFactor">10D vs SPY<strong>${instFmt(r10.vs_spy)}</strong></div><div class="instFactor">20D vs SPY<strong>${instFmt(r20.vs_spy)}</strong></div><div class="instFactor">5D vs ${c.parent||'group'}<strong>${instFmt(r5.vs_parent)}</strong></div><div class="instFactor">20D vs ${c.parent||'group'}<strong>${instFmt(r20.vs_parent)}</strong></div></div></div><div class="instSection"><div class="instSectionTitle">STRUCTURE REVIEW · EXECUTION / INVALIDATION</div><div class="instLevelGrid"><div class="instLevel"><span>TRIGGER</span><b>${instMoney(s.trigger)}</b></div><div class="instLevel"><span>CONFIRMATION</span><b>${instMoney(s.confirmation)}</b></div><div class="instLevel"><span>INVALIDATION</span><b>${instMoney(s.invalidation)}</b></div><div class="instLevel"><span>HARD FAIL</span><b>${instMoney(s.hard_fail)}</b></div><div class="instLevel"><span>TARGET 1</span><b>${instMoney(s.target1)}</b></div><div class="instLevel"><span>TARGET 2</span><b>${instMoney(s.target2)}</b></div></div><div class="tiny" style="margin-top:6px">Confirmation is trigger ±0.15 ATR. Invalidation uses recent structure + 20D mean; hard fail uses broader 20D/50D structure. Decision heuristics, not stop instructions.</div></div>`}
const _topSetupEvaluationV23=topSetupEvaluation;topSetupEvaluation=function(x){const b=_topSetupEvaluationV23(x),c=institutionalContextMap[x.ticker];if(!c)return {...b,factors:factorBreakdownFor(x,b,null)};let score=b.score,r20=c.relative_strength?.["20"]||{};if(c.triple_relative_strength)score+=6;else{if(Number(r20.vs_spy)>0)score+=3;if(Number(r20.vs_parent)>0)score+=3}if(c.rotation_persistence!=null&&Number(c.rotation_persistence)>=80)score+=4;else if(c.rotation_persistence!=null&&Number(c.rotation_persistence)<50)score-=4;if(c.catalyst?.days_to_earnings!=null&&c.catalyst.days_to_earnings<=3)score-=10;const mr=c.macro_risk?.risk||"CLEAR";if(mr==="HIGH")score-=12;else if(mr==="ELEVATED")score-=6;else if(mr==="WATCH")score-=2;return {...b,score:Math.max(0,Math.min(100,Math.round(score))),context:c,factors:factorBreakdownFor(x,b,c)}};
const _renderTopSetupsV23=renderTopSetups;renderTopSetups=function(){_renderTopSetupsV23();const g=document.getElementById("topSetupsGrid");if(!g)return;(globalTopSetupData||[]).forEach(x=>{});g.querySelectorAll('[data-top-setup]').forEach(card=>{const ticker=card.dataset.topSetup,x=(globalTopSetupData||[]).find(z=>z.ticker===ticker);if(!x)return;const e=topSetupEvaluation(x),c=e.context;if(!c)return;const s=c.structure||{},old=card.querySelector('.topSetupInstitutional');if(old)old.remove();const d=document.createElement('div');d.className='topSetupInstitutional';d.innerHTML=`<div class="topSetupInstGrid">${(e.factors||[]).map(z=>`<div class="topSetupInstMetric">${z[0]}<b>${z[1]}/10</b></div>`).join('')}</div><div class="tiny" style="margin-top:6px">${c.horizon} · trigger ${instMoney(s.trigger)} · invalidation ${instMoney(s.invalidation)} · T2 ${instMoney(s.target2)} · R:R ${s.rr_to_target2??'—'}×${c.catalyst?.days_to_earnings!=null&&c.catalyst.days_to_earnings<=10?` · <span class="instWarn">earnings ${c.catalyst.days_to_earnings}d</span>`:''}</div>`;const actions=card.querySelector('.topSetupActions');card.insertBefore(d,actions||null);const score=card.querySelector('.topSetupScore');if(score)score.textContent=`${e.score}/100`})};
const _runAutomaticTopSetupsV23=runAutomaticTopSetups;runAutomaticTopSetups=async function(force=false){await _runAutomaticTopSetupsV23(force);const rows=(globalTopSetupData||[]).slice(0,10);for(let n=0;n<rows.length;n+=3)await Promise.all(rows.slice(n,n+3).map(x=>loadInstitutionalContext(x.ticker,x._parentTicker||null,true)));renderTopSetups()};
const _openSectorStockTickerV23=openSectorStockTicker;openSectorStockTicker=async function(rawTicker,opts={}){const ticker=normalizeStockTicker(rawTicker),parent=currentSector,out=await _openSectorStockTickerV23(rawTicker,opts);setTimeout(()=>loadInstitutionalContext(ticker,parent,false),350);return out};const _openTopSetupDeepDiveV23=openTopSetupDeepDive;openTopSetupDeepDive=function(ticker,parentTicker=null,target="chart"){const out=_openTopSetupDeepDiveV23(ticker,parentTicker,target);loadInstitutionalContext(ticker,parentTicker||currentSector,false);return out};const _renderFlowV23=renderFlow;renderFlow=function(x){const out=_renderFlowV23(x);if(x?.ticker&&institutionalContextMap[x.ticker])renderInstitutionalContext(x.ticker);return out};const _renderOptionsPanelV23=renderOptionsPanel;renderOptionsPanel=function(){const out=_renderOptionsPanelV23();if(activeOptionsData?.ticker&&institutionalContextMap[activeOptionsData.ticker])renderInstitutionalContext(activeOptionsData.ticker);return out};

// v25.20: final presentation guard for structure/thesis direction mismatch.
const _renderTopSetupsV25_20=renderTopSetups;
renderTopSetups=function(){
 const out=_renderTopSetupsV25_20();
 const g=document.getElementById("topSetupsGrid");if(!g)return out;
 g.querySelectorAll('[data-top-setup]').forEach(card=>{
   const ticker=card.dataset.topSetup,x=(globalTopSetupData||[]).find(z=>z.ticker===ticker);if(!x)return;
   const e=topSetupEvaluation(x),c=e.context||((typeof institutionalContextMap!=="undefined")?institutionalContextMap[ticker]:null);if(!c)return;
   const s=c.structure||{},va=e.va,strat=(typeof stratSignalMap!=="undefined")?stratSignalMap[ticker]:null;
   const thesisDirection=(va?.direction&&va.direction!=="neutral")?va.direction:((strat?.continuity==="bullish"||strat?.continuity==="bearish")?strat.continuity:null);
   const structureDirection=s.direction&&s.direction!=="neutral"?s.direction:null;
   const directionMismatch=!!(thesisDirection&&structureDirection&&thesisDirection!==structureDirection&&s.trigger!=null);
   if(!directionMismatch)return;
   const inst=card.querySelector('.topSetupInstitutional');if(!inst)return;
   const details=inst.querySelectorAll('.tiny');const line=details.length?details[details.length-1]:null;if(!line)return;
   line.innerHTML=`<span class="instBad">⚠ Structure model reads ${String(structureDirection).toUpperCase()} while this setup's thesis is ${String(thesisDirection).toUpperCase()} — trigger/invalidation levels withheld pending alignment.</span>`;
 });
 return out;
};


// -------------------- v26.0 Attention-First Trader Board --------------------
// Keep the full analytical stack underneath, but collapse the default Top Setup
// experience into independent domains: setup quality, tradeability, risk and plan.
const V26_BUDGET_KEY="v26MaxContractCost";
function v26Budget(){
  const raw=localStorage.getItem(V26_BUDGET_KEY);const n=Number(raw);
  return Number.isFinite(n)&&n>0?n:250;
}
function v26Money(v){const n=Number(v);return Number.isFinite(n)?`$${n.toFixed(0)}`:"—"}
function v26Decision(x){
  const e=topSetupEvaluation(x),c=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null;
  const opt=optionScanMap[x.ticker]||{},pc=premiumSupportMap[x.ticker]?.best_contract||null;
  const budget=v26Budget(),mid=Number(pc?.mid),cost=Number.isFinite(mid)?mid*100:null;
  // Setup quality deliberately uses broad domains rather than adding every correlated indicator.
  const f=x.fast||x,t=x.trend||{},r20=c?.relative_strength?.["20"]||{};
  const rot=e.alignment==="FULL"?95:e.alignment==="EARLY"?88:60;
  const rs=(Number(r20.vs_spy)>0?50:20)+(Number(r20.vs_parent)>0?50:(c?.parent?20:35));
  const trend=Number(c?.structure?.trend_strength);const structure=Number.isFinite(trend)?Math.min(100,35+trend*16):65;
  const va=e.va;const confirmation=va?.strength==="CONFIRMED"?95:va?.strength==="DEVELOPING"?72:va?.strength==="REJECTION"?25:55;
  const setup=Math.round(.40*rot+.20*rs+.25*structure+.15*confirmation);
  let trade=0;
  trade+=opt.liquidity==="Liquid"?30:opt.liquidity==="Tradable"?24:8;
  if(cost!=null)trade+=cost<=budget?40:cost<=budget*1.25?20:4;else trade+=8;
  const ps=Number(pc?.premium_support_score);trade+=Number.isFinite(ps)?Math.max(3,Math.min(20,ps*.20)):8;
  trade+=opt.iv_state==="Cheap / Crushed"?10:opt.iv_state==="Normal"?7:opt.iv_state==="Juiced"?2:5;
  trade=Math.max(0,Math.min(100,Math.round(trade)));
  const s=c?.structure||{},cat=c?.catalyst||{},macro=c?.macro_risk||{};
  const risk=(cat.days_to_earnings!=null&&cat.days_to_earnings<=3)||macro.risk==="HIGH";
  let label="WATCH FOR ENTRY",cls="v26Watch";
  if(cost!=null&&cost>budget){label="GOOD SETUP · PREMIUM TOO EXPENSIVE";cls="v26Wait";}
  else if(setup>=80&&trade>=70&&!risk){label="A+ · TRADEABLE";cls="v26Go";}
  else if(setup>=72&&trade>=58&&!risk){label="A · WATCH / TRADEABLE";cls="v26Watch";}
  else if(risk){label="WAIT · EVENT RISK";cls="v26Wait";}
  else if(trade<50){label="WATCH · EXECUTION WEAK";cls="v26Wait";}
  return {e,c,opt,pc,budget,cost,setup,trade,label,cls,s,combined:Math.round(.68*setup+.32*trade)};
}
function v26EnsureControls(){
  const g=document.getElementById("topSetupsGrid");if(!g||document.getElementById("v26TraderBoard"))return;
  const board=document.createElement("div");board.id="v26TraderBoard";board.className="v26TraderBoard";
  board.innerHTML=`<div><b>2–5 MINUTE TRADER BOARD</b><div class="tiny">Underlying quality and option tradeability are scored separately. Deep confluence stays available below.</div></div><label>MAX CONTRACT <span>$</span><input id="v26BudgetInput" inputmode="numeric" type="number" min="25" step="25" value="${v26Budget()}"></label>`;
  g.parentNode.insertBefore(board,g);
  board.querySelector("#v26BudgetInput")?.addEventListener("change",e=>{const n=Math.max(25,Number(e.target.value)||250);localStorage.setItem(V26_BUDGET_KEY,String(n));e.target.value=n;renderTopSetups();});
}
function v26EnhanceTopSetups(){
  const g=document.getElementById("topSetupsGrid");if(!g)return;
  v26EnsureControls();
  const cards=[...g.querySelectorAll('[data-top-setup]')];
  const ranked=[];
  cards.forEach(card=>{
    const x=(globalTopSetupData||[]).find(z=>z.ticker===card.dataset.topSetup);if(!x)return;
    const d=v26Decision(x);ranked.push({card,d});
    card.querySelector('.v26DecisionStrip')?.remove();
    const strip=document.createElement('div');strip.className=`v26DecisionStrip ${d.cls}`;
    const contract=d.pc?`${d.pc.expiration||''} · ${Number(d.pc.strike).toFixed(0)}${String(d.pc.type||'').toLowerCase().startsWith('p')?'P':'C'} · ${d.cost==null?'—':v26Money(d.cost)+'/contract'}`:'premium loading / unavailable';
    const trigger=(d.s.trigger!=null&&Number.isFinite(Number(d.s.trigger)))?v26Money(d.s.trigger):'—',inv=(d.s.invalidation!=null&&Number.isFinite(Number(d.s.invalidation)))?v26Money(d.s.invalidation):'—';
    strip.innerHTML=`<div class="v26DecisionTop"><b>${d.label}</b><span>${d.combined}</span></div><div class="v26ScoreRow"><span>SETUP QUALITY <b>${d.setup}/100</b></span><span>TRADEABILITY <b>${d.trade}/100</b></span></div><div class="v26Plan"><span>CONTRACT <b>${contract}</b></span><span>TRIGGER <b>${trigger}</b></span><span>INVALIDATION <b>${inv}</b></span></div>`;
    const head=card.querySelector('.topSetupHead');if(head?.nextSibling)card.insertBefore(strip,head.nextSibling);else card.prepend(strip);
    const inst=card.querySelector('.topSetupInstitutional');if(inst){inst.classList.add('v26DeepEvidence');if(!inst.closest('details')){const det=document.createElement('details');det.className='v26Why';const sum=document.createElement('summary');sum.textContent='Why? · full confluence evidence';inst.parentNode.insertBefore(det,inst);det.appendChild(sum);det.appendChild(inst);}}
  });
  ranked.sort((a,b)=>b.d.combined-a.d.combined).forEach(({card})=>g.appendChild(card));
}
const _renderTopSetupsV26=renderTopSetups;
renderTopSetups=function(){const out=_renderTopSetupsV26();try{v26EnhanceTopSetups()}catch(e){console.warn('v26 board',e)}return out};
const v26Style=document.createElement('style');v26Style.textContent=`
.v26TraderBoard{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;margin:8px 0 12px;border:1px solid #27445a;border-radius:11px;background:linear-gradient(135deg,#0b1923,#0a141d)}
.v26TraderBoard>b,.v26TraderBoard b{letter-spacing:.5px}.v26TraderBoard label{display:flex;align-items:center;gap:5px;font-size:10px;font-weight:800;color:#91a4b8;white-space:nowrap}.v26TraderBoard input{width:82px;padding:7px;background:#071018;border:1px solid #31516a;color:#eaf2f8;border-radius:7px}
.v26DecisionStrip{margin:9px 0;padding:11px;border-radius:9px;border:1px solid #2a4052;background:#09141d}.v26Go{border-left:4px solid #2edb71}.v26Watch{border-left:4px solid #4aa3ff}.v26Wait{border-left:4px solid #f59e0b}.v26DecisionTop,.v26ScoreRow,.v26Plan{display:flex;align-items:center;justify-content:space-between;gap:8px}.v26DecisionTop>b{font-size:12px}.v26DecisionTop>span{font-size:18px;font-weight:850}.v26ScoreRow{margin-top:8px;justify-content:flex-start}.v26ScoreRow span{font-size:9px;color:#8195a8}.v26ScoreRow b{font-size:12px;color:#edf5fb;margin-left:4px}.v26Plan{margin-top:9px;display:grid;grid-template-columns:1.5fr 1fr 1fr}.v26Plan span{font-size:8px;color:#7890a4}.v26Plan b{display:block;color:#dfeaf2;font-size:10px;margin-top:2px}.v26Why{margin-top:7px;border-top:1px solid #1b2b38;padding-top:7px}.v26Why summary{cursor:pointer;color:#7fa5c2;font-size:10px;font-weight:750}.v26DeepEvidence{margin-top:8px}
@media(max-width:760px){.v26TraderBoard{align-items:flex-start;flex-direction:column}.v26TraderBoard label{width:100%;justify-content:space-between}.v26Plan{grid-template-columns:1fr 1fr}.v26Plan span:first-child{grid-column:1/-1}.v26ScoreRow{justify-content:space-between}.v26DecisionTop{align-items:flex-start}.v26DecisionTop>b{max-width:78%}}
`;document.head.appendChild(v26Style);
setTimeout(()=>{try{v26EnsureControls();v26EnhanceTopSetups()}catch(e){}},0);

const v263Style=document.createElement('style');v263Style.textContent='.v263Lane{display:inline-block;padding:2px 6px;border-radius:999px;font-size:9px;font-weight:900;letter-spacing:.5px;border:1px solid #36536a}.v263EARLY{color:#67e8f9;border-color:#157a8a;background:#08262c}.v263CONTINUATION{color:#fbbf24;border-color:#8a6215;background:#2a1e05}.v263CONFIRMED{color:#6ee7a7;border-color:#167146;background:#082419}.v263DEVELOPING{color:#a8b4c2}.v263PhaseNote{margin-top:8px;color:#9cb0c2}\n';document.head.appendChild(v263Style);
// v26.3: temporal opportunity lanes. Early and continuation are both valid;
// confirmed is not treated as inherently superior if the entry has already run.
const _renderTopSetupsV263=renderTopSetups;
renderTopSetups=function(){
 const out=_renderTopSetupsV263(),g=document.getElementById('topSetupsGrid');if(!g)return out;
 g.querySelectorAll('[data-top-setup]').forEach(card=>{
   const x=(globalTopSetupData||[]).find(z=>z.ticker===card.dataset.topSetup);if(!x)return;
   const lane=v263OpportunityLane(x),head=card.querySelector('.topSetupStatus');
   if(head&&!head.dataset.v263Lane){head.dataset.v263Lane='1';head.innerHTML=`<span class="v263Lane v263${lane.lane}">${lane.lane}</span> · ${head.innerHTML}`;}
   const strip=card.querySelector('.v26DecisionStrip');if(strip){
     let note=strip.querySelector('.v263PhaseNote');if(!note){note=document.createElement('div');note.className='v263PhaseNote tiny';strip.appendChild(note)}
     note.textContent=lane.lane==='EARLY'?`EARLY · ${lane.detail} · higher timing edge, lower confirmation`:lane.lane==='CONTINUATION'?`CONTINUATION · ${lane.detail} · move has started; judge entry quality, not just extension`:lane.lane==='CONFIRMED'?`CONFIRMED · ${lane.detail} · strongest validation, may require pullback/base if extended`:`DEVELOPING · ${lane.detail}`;
   }
 });
 return out;
};

</script>
"""
@app.errorhandler(500)
def internal_error(err):
    app.logger.exception("Unhandled server error: %s", err)
    return Response("Internal Server Error — check Render logs for the Python traceback.", status=500, mimetype="text/plain")

@app.get("/")
def home():
    # Important: rendering the shell performs no external network requests.
    # The version pill is substituted from APP_VERSION (the single source of
    # truth already used by /health and /api/diagnostics) rather than being a
    # second, manually-typed copy — a hand-edited literal here had drifted out
    # of sync with the real deployed version, showing a stale badge in the UI.
    page = str(HTML).replace("{{APP_VERSION_PLACEHOLDER}}", f"v{APP_VERSION}")
    shell = "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='theme-color' content='#0b0e11'><meta name='apple-mobile-web-app-capable' content='yes'><meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'><title>Market Rotation Screener</title></head><body>" + page + "</body></html>"
    return Response(shell, mimetype="text/html")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=PORT,debug=False,threaded=True)
