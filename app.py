
from flask import Flask, jsonify, request, Response, session, redirect
from concurrent.futures import ThreadPoolExecutor, as_completed
import io, math, time, traceback, os
from urllib.parse import quote
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import requests
import yfinance as yf

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
PORT = int(os.environ.get("PORT", "8765"))
SCREENER_PASSWORD = os.environ.get("SCREENER_PASSWORD", "").strip()
UW_API_TOKEN = os.environ.get("UW_API_TOKEN", "").strip()
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()

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

CACHE = {}
CACHE_TTL = 60 * 15

def cached(key, fn, ttl=CACHE_TTL):
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
    try:
        val = fn()
        CACHE[key] = (now, val)
        return val, False, None
    except Exception as e:
        if hit:
            return hit[1], True, str(e)
        raise

def dl_prices(tickers, period="3y"):
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
    if df is None or len(df) == 0:
        raise RuntimeError("Price provider returned no data" + (f": {last_err}" if last_err else "."))
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            close = df["Close"].copy()
        elif "Adj Close" in df.columns.get_level_values(0):
            close = df["Adj Close"].copy()
        else:
            raise RuntimeError("Price download did not contain a Close column.")
    else:
        col = "Close" if "Close" in df.columns else ("Adj Close" if "Adj Close" in df.columns else None)
        if not col:
            raise RuntimeError("Price download did not contain a Close column.")
        close = df[[col]].copy()
        close.columns = [tickers[0]]
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close.sort_index().dropna(how="all")

def dl_ohlc(ticker, period="3y"):
    df = yf.download(
        ticker, period=period, interval="1d", auto_adjust=True,
        progress=False, threads=False, timeout=20
    )
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.sort_index()

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


VANECK_FUNDS = {"SMH"}

def vaneck_holdings(etf):
    """Pull current VanEck holdings from the public fund page HTML."""
    etf = etf.upper()
    fund_urls = {
        "SMH":"https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/"
    }
    url = fund_urls.get(etf)
    if not url:
        raise RuntimeError(f"No VanEck holdings parser configured for {etf}.")
    headers = {
        "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
    }
    resp = requests.get(url, timeout=25, headers=headers)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    best = None
    for df in tables:
        cols = [str(c).strip().lower() for c in df.columns]
        joined = " | ".join(cols)
        if "ticker" in joined and ("net assets" in joined or "% of net assets" in joined):
            best = df
            break
    if best is None:
        # relaxed fallback: locate any table with ticker + holding
        for df in tables:
            cols = [str(c).strip().lower() for c in df.columns]
            joined = " | ".join(cols)
            if "ticker" in joined and "holding" in joined:
                best = df
                break
    if best is None:
        raise RuntimeError("Could not find the VanEck holdings table.")
    cols = {str(c).strip().lower(): c for c in best.columns}
    tcol = next((orig for low,orig in cols.items() if low == "ticker" or "ticker" in low), None)
    ncol = next((orig for low,orig in cols.items() if "holding name" in low or low == "name"), None)
    wcol = next((orig for low,orig in cols.items() if "net assets" in low or "weight" in low), None)
    out = []
    for _, row in best.iterrows():
        t = str(row.get(tcol,"")).strip().upper().replace(".","-")
        if not t or t in ("NAN","--","-USD CASH-","-") or len(t) > 12:
            continue
        name = str(row.get(ncol,t)).strip() if ncol is not None else t
        weight = None
        if wcol is not None:
            try:
                raw = str(row.get(wcol,"")).replace("%","").replace(",","").strip()
                weight = float(raw)
            except Exception:
                pass
        out.append({"ticker":t,"name":name,"weight":weight})
    if len(out) < 5:
        raise RuntimeError("VanEck holdings page returned too few usable tickers.")
    return out

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

def get_fund_holdings(etf):
    """Use issuer daily files where available; otherwise use Yahoo fund holdings."""
    etf = etf.upper()
    # Prefer issuer data because it exposes the full portfolio.
    if etf in VANECK_FUNDS:
        try:
            return vaneck_holdings(etf), "VanEck current holdings"
        except Exception:
            pass
    if etf in STATE_STREET_FUNDS:
        try:
            return state_street_holdings(etf), "State Street daily holdings"
        except Exception:
            pass
    # Generic fallback works across many issuers, typically with top holdings.
    holdings = yahoo_fund_holdings(etf)
    return holdings, "Yahoo Finance fund holdings"




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
        return out
    except Exception:
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
    resp = requests.get(url, params=params or {}, headers=headers, timeout=25)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("data", payload)

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
        return out
    except Exception:
        return {}

def discover_recent_earnings(tickers, recent_trading_days=10):
    """
    Source priority:
    1) Finnhub free earnings calendar (when FINNHUB_API_KEY is configured)
    2) Unusual Whales API (only if a paid UW token is configured)
    3) Yahoo public calendar
    4) yfinance per-ticker earnings history

    Returns {ticker: {"date": Timestamp, "time": str|None, "source": str, ...}}
    """
    now = pd.Timestamp.now().normalize()
    calendar_days = int(recent_trading_days * 1.8) + 5
    start = now - pd.Timedelta(days=calendar_days)
    wanted = set(tickers)
    found = {}

    # Primary free structured source: one Finnhub date-range request can cover the whole scan.
    if FINNHUB_API_KEY:
        for t, meta in finnhub_earnings_calendar(start, now).items():
            if t in wanted:
                found[t] = meta

    # Optional paid source if the user already has it.
    if UW_API_TOKEN:
        for d in pd.date_range(start, now, freq="D"):
            if d.weekday() >= 5:
                continue
            for t, meta in unusual_whales_day(d).items():
                if t in wanted and t not in found:
                    found[t] = meta

    # Free public fallback.
    for d in pd.date_range(start, now, freq="D"):
        if d.weekday() >= 5:
            continue
        day_map = yahoo_calendar_for_day(d)
        for t, ed in day_map.items():
            if t in wanted and t not in found:
                found[t] = {"date": ed, "time": None, "source": "Yahoo earnings calendar"}

    # Final fallback: ticker history.
    missing = [t for t in tickers if t not in found]
    if missing:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(get_earnings_dates,t,12):t for t in missing}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    dates = [d for d in fut.result() if d <= now and d >= start]
                    if dates:
                        found[t] = {
                            "date": max(dates),
                            "time": None,
                            "source": "yfinance ticker history"
                        }
                except Exception:
                    pass
    return found

def get_earnings_dates(ticker, limit=12):
    try:
        ed = yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if ed is None or len(ed)==0:
            return []
        idx = pd.to_datetime(ed.index)
        dates=[]
        for d in idx:
            try:
                if getattr(d, "tzinfo", None) is not None:
                    d = d.tz_convert(None)
            except:
                try: d=d.tz_localize(None)
                except: pass
            dates.append(pd.Timestamp(d).normalize())
        return sorted(list(dict.fromkeys(dates)), reverse=True)
    except Exception:
        return []

def event_session_index(df, earnings_date):
    idx = df.index
    d = pd.Timestamp(earnings_date).normalize()
    # First session on/after earnings date. This intentionally treats BMO/AMC the same;
    # the detail panel shows dates so a user can verify edge cases.
    pos = idx.searchsorted(d)
    if pos >= len(idx): return None
    return int(pos)

def abs_excursion(df, start_pos, days):
    if start_pos is None or start_pos <= 0 or start_pos >= len(df):
        return None
    base = float(df["Close"].iloc[start_pos-1])
    end = min(len(df), start_pos+days)
    if end <= start_pos: return None
    seg = df.iloc[start_pos:end]
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
    # Use up to last 8 completed events, avoiding the current one if insufficient future days.
    completed=[e for e in events if e["exc14"] is not None]
    hist=completed[-8:]
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
    ratios=[]
    for e in hist:
        if e["exc1"] and e["exc10"] is not None and e["exc1"]>0:
            ratios.append(e["exc10"]/e["exc1"])
    cont_ratio=float(np.median(ratios)) if ratios else 1.0

    # Simple mover score driven by typical excursion + frequency.
    m1=med("exc1") or 0
    m10=med("exc10") or 0
    score=min(10.0, 0.45*min(10,m1) + 0.35*min(10,m10/1.5) + 0.20*((pct5 or 0)/10))
    if score>=7.0: label="HIGH"
    elif score>=4.5: label="MODERATE"
    else: label="LOW"
    behavior="CONTINUATION" if cont_ratio>=1.45 else ("FAST REACTION" if cont_ratio<1.15 else "MIXED")
    return {
        "label":label,"score":round(score,1),"n":len(hist),
        "median_exc1":round(m1,2),"median_exc3":round(med("exc3") or 0,2),
        "median_exc5":round(med("exc5") or 0,2),"median_exc10":round(m10,2),
        "median_exc14":round(med("exc14") or 0,2),
        "pct_gt5_10d":round(pct5 or 0,1),"pct_gt10_14d":round(pct10 or 0,1),
        "behavior":behavior,"events":list(reversed(hist))
    }

def market_payload():
    tickers=["SPY","RSP","IWM","QQQ","^VIX","^TNX"]+list(RRG_UNIVERSE)
    prices=dl_prices(tickers,"18mo")
    if "SPY" not in prices: raise RuntimeError("SPY data unavailable.")
    internals={}
    spy=prices["SPY"].dropna()
    internals["SPY"]={"d5":pct_change(spy,5),"d20":pct_change(spy,20)}
    for t,label in [("RSP","Breadth"),("IWM","Small caps"),("QQQ","Growth")]:
        pair=prices[["SPY",t]].dropna()
        ratio=pair[t]/pair["SPY"]
        internals[t]={"d5":pct_change(ratio,5),"d20":pct_change(ratio,20),"label":label}
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
    rows=dual_rrg_rows(prices,"SPY",list(RRG_UNIVERSE),8,8)
    for r in rows:
        r["name"]=RRG_UNIVERSE.get(r["ticker"],r["ticker"])
        r["group"]="Core Sector" if r["ticker"] in SECTORS else "Industry / Theme"
        r["alignment"]=alignment_label(r.get("fast"), r.get("trend"))
    return {"asof":prices.index.max().strftime("%Y-%m-%d"),"internals":internals,"participation":participation,"sectors":rows}


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
        if supplied == SCREENER_PASSWORD:
            session["authenticated"] = True
            return redirect("/")
        error = "Incorrect password."
    return Response(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0e11"><title>Market Rotation Screener</title>
<style>
body{{margin:0;background:#0b0e11;color:#e5e7eb;font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;min-height:100vh;padding:22px}}
.box{{width:min(420px,100%);background:#12161b;border:1px solid #27303a;border-radius:16px;padding:22px}}
h1{{font-size:20px;margin:0 0 8px}}p{{color:#8b95a5}}
input,button{{width:100%;font:inherit;padding:13px;border-radius:10px;box-sizing:border-box}}
input{{background:#0b0e11;color:#fff;border:1px solid #334155;margin:10px 0}}
button{{background:#1d4ed8;color:#fff;border:0;font-weight:700}}.err{{color:#fca5a5}}

</style></head><body><div class="box"><h1>Market Rotation Screener</h1><p>Enter your screener password.</p>
<form method="post"><input type="password" name="password" autocomplete="current-password" autofocus>
<button type="submit">Open Screener</button></form><div class="err">{error}</div></div></body></html>""", mimetype="text/html")

@app.get("/api/market")
def api_market():
    try:
        force = request.args.get("refresh")=="1"
        payload, stale, refresh_error = cached_refresh_safe("market", market_payload, force=force)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":refresh_error})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/sector/<etf>")
def api_sector(etf):
    etf=etf.upper()
    if etf not in RRG_UNIVERSE:
        return jsonify({"ok":False,"error":"Choose an ETF from the Layer-1 RRG universe."}),400
    try:
        limit_raw=request.args.get("limit","30").lower()
        limit=None if limit_raw=="all" else max(5,min(100,int(limit_raw)))
        force_holdings = request.args.get("refresh")=="1"
        holding_bundle, holdings_stale, holdings_refresh_error = cached_refresh_safe(
            f"holdings:{etf}", lambda:get_fund_holdings(etf), force=force_holdings, ttl=3600
        )
        holdings, holdings_source = holding_bundle
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
    etf=etf.upper()
    try:
        recent_days=max(3,min(30,int(request.args.get("days","10"))))
        limit=max(10,min(60,int(request.args.get("limit","30"))))
        holdings,_holdings_source=cached(f"holdings:{etf}",lambda:get_fund_holdings(etf),ttl=3600)
        holdings=holdings[:limit]
        tickers=[h["ticker"] for h in holdings]
        now=pd.Timestamp.now().normalize()
        # calendar-day buffer approximates requested trading-day window
        cutoff=now-pd.Timedelta(days=int(recent_days*1.8)+4)

        recent_map = discover_recent_earnings(tickers, recent_days)

        recent=[]
        for h in holdings:
            t = h["ticker"]
            meta_recent = recent_map.get(t)
            if meta_recent is None:
                continue
            d = pd.Timestamp(meta_recent["date"]).normalize()

            # Prefer UW historical dates if available, otherwise yfinance.
            uw_hist = unusual_whales_history(t) if UW_API_TOKEN else []
            if uw_hist:
                dates = [x["date"] for x in uw_hist]
            else:
                dates = get_earnings_dates(t, 16)

            if not any(abs((pd.Timestamp(x).normalize()-d).days) <= 1 for x in dates):
                dates = [d] + dates

            recent.append((h,d,dates,meta_recent))

        if not recent:
            return jsonify({"ok":True,"sector":etf,"sector_name":SECTORS.get(etf,etf),
                            "results":[],"recent_days":recent_days,"message":"No recent earnings were found after checking both the public calendar and ticker history."})

        # RRG context for recent names
        names=[x[0]["ticker"] for x in recent]
        prices=dl_prices([etf]+names,"18mo")
        rrg={r["ticker"]:r for r in dual_rrg_rows(prices,etf,names,8,8)}

        def build(item):
            h,d,dates,event_meta=item
            prof=earnings_profile(h["ticker"],dates)
            r=rrg.get(h["ticker"],{})
            days_ago=max(0,(now-d).days)
            # Overall current screen score keeps historical mover quality separate but visible.
            current_score=float(r.get("score",0))
            return {
                "ticker":h["ticker"],"name":h["name"],"weight":h.get("weight"),
                "earnings_date":d.strftime("%Y-%m-%d"),"calendar_days_ago":days_ago,
                "earnings_time":event_meta.get("time"),
                "earnings_source":event_meta.get("source"),
                "uw_reaction":event_meta.get("reaction"),
                "uw_expected_move_perc":event_meta.get("expected_move_perc"),
                "eps_estimate":event_meta.get("eps_estimate"),
                "eps_actual":event_meta.get("eps_actual"),
                "revenue_estimate":event_meta.get("revenue_estimate"),
                "revenue_actual":event_meta.get("revenue_actual"),
                "profile":prof,"rotation":r,"current_score":current_score
            }

        results=[]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs=[ex.submit(build,x) for x in recent]
            for fut in as_completed(futs):
                try: results.append(fut.result())
                except: pass

        rank={"HIGH":3,"MODERATE":2,"LOW":1}
        results.sort(key=lambda x:(-rank.get((x["profile"] or {}).get("label","LOW"),0),
                                   -((x["profile"] or {}).get("score",0)),
                                   -x.get("current_score",0)))
        return jsonify({"ok":True,"sector":etf,"sector_name":RRG_UNIVERSE.get(etf,etf),
                        "recent_days":recent_days,"results":results})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.get("/health")
def health():
    return jsonify({"ok":True,"unusual_whales_api_configured":bool(UW_API_TOKEN),"finnhub_api_configured":bool(FINNHUB_API_KEY)})

@app.get("/api/source-status")
def source_status():
    return jsonify({
        "ok": True,
        "finnhub_api_configured": bool(FINNHUB_API_KEY),
        "unusual_whales_api_configured": bool(UW_API_TOKEN),
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

</style>
<div class="wrap">
<h1>Market Rotation Screener</h1>
<div class="sub">Cloud/mobile · Fast RRG (10/5) finds what is changing now; Trend RRG (25/12) asks whether it is persisting. Data refreshes only when you press Refresh/Scan.</div>
<div class="tabs">
<button class="tab active" data-view="rotation">Rotation Screen</button>
<button class="tab" data-view="earnings">Post-Earnings Screen</button>
</div>

<div id="rotation" class="view active">
  <div class="panel">
    <div class="row"><strong>Market & sector screen</strong><button class="primary" id="refreshMarket">Refresh market</button><span id="mstatus" class="status"></span></div>
    <div id="internals" class="cards"></div>
  </div>
  <div class="grid2">
    <div class="panel"><div class="row"><strong>Layer 1 · Groups vs SPY</strong>
<select id="groupFilter"><option value="all">All</option><option value="core">Core sectors</option><option value="industry">Industries / themes</option></select>
<span class="note">Fast RRG = 10/5 daily · Trend RRG = 25/12 daily</span></div><canvas id="sectorChart" width="900" height="540"></canvas></div>
    <div class="panel"><div class="scroll"><table><thead><tr><th>#</th><th>Sector</th><th>Fast</th><th>Trend</th><th>Alignment</th></tr></thead><tbody id="sectorRows"></tbody></table></div></div>
  </div>
  <div class="panel">
    <div class="row"><strong>Stock screen</strong>
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
      <span id="sectorTitle" class="note">Choose a sector</span>
      <label class="note">Holdings</label><select id="limit"><option>20</option><option selected>30</option><option>50</option></select>
      <button id="refreshSector">Refresh</button><span id="sstatus" class="status"></span>
    </div>
  </div>
  <div class="grid2">
    <div class="panel"><canvas id="stockChart" width="900" height="540"></canvas></div>
    <div class="panel"><div class="scroll"><table><thead><tr><th>#</th><th>Ticker</th><th>Score</th><th>Fast</th><th>Trend</th><th>Alignment</th></tr></thead><tbody id="stockRows"></tbody></table></div></div>
  </div>
</div>

<div id="earnings" class="view">
  <div class="panel">
    <div class="row"><strong>Post-Earnings Screen</strong>
      <label class="note">Sector</label>
      <select id="earnSector">""" + "".join([f'<option value="{k}">{k} · {v}</option>' for k,v in RRG_UNIVERSE.items()]) + r"""</select>
      <label class="note">Reported within</label><select id="earnDays"><option>5</option><option selected>10</option><option>14</option><option>20</option></select><span class="note">trading days (approx.)</span>
      <label class="note">Holdings scanned</label><select id="earnLimit"><option>20</option><option selected>30</option><option>50</option></select>
      <label class="note">Mover filter</label><select id="moverFilter"><option value="all">All</option><option value="hm">High + Moderate</option><option value="high">High only</option></select>
      <button class="primary" id="runEarnings">Scan earnings</button><span id="estatus" class="status"></span>
    </div>
    <div class="note" style="margin-top:9px">Earnings source priority: Finnhub (free API key) → Unusual Whales API if configured → Yahoo calendar → ticker history. Recent earnings discovery uses a calendar-level check plus ticker history. Historical mover labels use up to the last 8 completed earnings events. Expand any ticker for 1/3/5/10/14-day excursion stats.</div>
  </div>
  <div class="panel">
    <table><thead><tr><th>#</th><th>Ticker</th><th>Recent earnings</th><th>Historical mover</th><th>Rotation vs sector</th><th>Details</th></tr></thead><tbody id="earnRows"></tbody></table>
  </div>
</div>
</div>
<script>
let sectorData=[],currentSector=null,earnResults=[];
const quadColors={Leading:"#22c55e",Improving:"#38bdf8",Lagging:"#ef4444",Weakening:"#f59e0b"};
function fmt(v,n=2){return(v==null||!isFinite(v))?"—":Number(v).toFixed(n)}
function pct(v){return(v==null)?"—":(v>=0?"+":"")+fmt(v,2)+"%"}
function badge(q){return q?`<span class="badge ${q}">${q.toUpperCase()}</span>`:"—"}
function dir(r){if(!r||!r.ticker)return"—";let s=`<span class="${r.rs_up?'up':'down'}">RS-Ratio ${r.rs_up?'↑':'↓'}</span> · <span class="${r.mom_up?'up':'down'}">RS-Momentum ${r.mom_up?'↑':'↓'}</span>`;if(r.l_to_i)s+=' <span class="flag">L→I</span>';if(r.early_turn)s+=' <span class="flag">EARLY TURN</span>';return s}
function drawRRG(id,rows){
 const c=document.getElementById(id),ctx=c.getContext("2d"),W=c.width,H=c.height,p=42;ctx.clearRect(0,0,W,H);ctx.fillStyle="#0d1217";ctx.fillRect(0,0,W,H);
 if(!rows||!rows.length){ctx.fillStyle="#8b95a5";ctx.fillText("No data yet",p,p);return}
 let xs=[],ys=[];rows.forEach(r=>(r.tail||[]).forEach(pt=>{xs.push(pt.x);ys.push(pt.y)}));let xmin=Math.min(98,...xs)-.8,xmax=Math.max(102,...xs)+.8,ymin=Math.min(98,...ys)-.8,ymax=Math.max(102,...ys)+.8;
 const X=x=>p+(x-xmin)/(xmax-xmin)*(W-2*p),Y=y=>H-p-(y-ymin)/(ymax-ymin)*(H-2*p),cx=X(100),cy=Y(100);
 ctx.strokeStyle="#475569";ctx.beginPath();ctx.moveTo(cx,p);ctx.lineTo(cx,H-p);ctx.moveTo(p,cy);ctx.lineTo(W-p,cy);ctx.stroke();
 ctx.fillStyle="#64748b";ctx.font="10px sans-serif";ctx.fillText("IMPROVING",p+6,p+14);ctx.fillText("LEADING",W-p-50,p+14);ctx.fillText("LAGGING",p+6,H-p-8);ctx.fillText("WEAKENING",W-p-70,H-p-8);
 rows.forEach(r=>{let pts=r.tail||[];if(!pts.length)return;let color=quadColors[r.quadrant];ctx.strokeStyle=color;ctx.globalAlpha=.65;ctx.beginPath();pts.forEach((pt,j)=>{j?ctx.lineTo(X(pt.x),Y(pt.y)):ctx.moveTo(X(pt.x),Y(pt.y))});ctx.stroke();ctx.globalAlpha=1;let last=pts[pts.length-1];ctx.fillStyle=color;ctx.beginPath();ctx.arc(X(last.x),Y(last.y),5,0,Math.PI*2);ctx.fill();ctx.font="bold 11px sans-serif";ctx.fillText(r.ticker,X(last.x)+7,Y(last.y)-6)});
}
function filteredGroups(){
 let f=document.getElementById("groupFilter")?.value||"all";
 return sectorData.filter(x=>f==="all"||(f==="core"&&x.group==="Core Sector")||(f==="industry"&&x.group==="Industry / Theme"));
}
function renderGroups(){
 let data=filteredGroups(); drawRRG("sectorChart",data);
 document.getElementById("sectorRows").innerHTML=data.map((x,k)=>`<tr class="clickrow" data-sector="${x.ticker}"><td>${k+1}</td><td><b>${x.ticker}</b><div class="tiny">${x.name} · ${x.group}</div></td><td>${compactRRG(x.fast)}</td><td>${compactRRG(x.trend)}</td><td>${alignBadge(x.alignment)}</td></tr>`).join("");
 document.querySelectorAll("[data-sector]").forEach(el=>el.addEventListener("click",()=>{let t=el.dataset.sector;currentSector=t; document.getElementById("sectorTitle").textContent=t+" selected"; loadSector()}));
}
async function loadMarket(force=false){
 let st=document.getElementById("mstatus");st.textContent="Updating…";
 try{let r=await fetch("/api/market"+(force?"?refresh=1":""));let j=await r.json();if(!j.ok)throw Error(j.error);sectorData=j.sectors;st.textContent=j.stale?`Refresh source unavailable — showing last good data through ${j.asof}`:`Through ${j.asof}`;let i=j.internals;
 document.getElementById("internals").innerHTML=`<div class="card"><div class="tiny">SPY TREND</div><b>${pct(i.SPY.d5)}</b><div class="tiny">${pct(i.SPY.d20)} / 20d</div></div><div class="card"><div class="tiny">RSP/SPY · BREADTH</div><b>${pct(i.RSP.d5)}</b><div class="tiny">${pct(i.RSP.d20)} / 20d</div></div><div class="card"><div class="tiny">IWM/SPY</div><b>${pct(i.IWM.d5)}</b><div class="tiny">${pct(i.IWM.d20)} / 20d</div></div><div class="card"><div class="tiny">QQQ/SPY</div><b>${pct(i.QQQ.d5)}</b><div class="tiny">${pct(i.QQQ.d20)} / 20d</div></div><div class="card"><div class="tiny">PARTICIPATION</div><b>${j.participation}</b></div>`;
 renderGroups();
 }catch(e){st.innerHTML=`<span class="error">Refresh failed: ${e.message}. Existing results were kept; wait a minute and retry.</span>`}}
async function loadSector(){
 if(!currentSector)return;let st=document.getElementById("sstatus");st.textContent="Updating…";document.getElementById("sectorTitle").textContent=currentSector;
 try{let lim=document.getElementById("limit").value,r=await fetch(`/api/sector/${currentSector}?limit=${lim}`),j=await r.json();if(!j.ok)throw Error(j.error);st.textContent=(j.holdings_stale?"Holdings refresh unavailable — using last good list · ":"")+`${j.holdings_as_screened} of ${j.holdings_total} holdings · ${j.holdings_source||"source unknown"} · through ${j.asof||"—"}`;drawRRG("stockChart",j.results);document.getElementById("stockRows").innerHTML=j.results.map((x,k)=>`<tr><td>${k+1}</td><td><b>${x.ticker}</b></td><td><b>${fmt(x.score,1)}</b></td><td>${compactRRG(x.fast)}</td><td>${compactRRG(x.trend)}</td><td>${alignBadge(x.alignment)}</td></tr>`).join("")}catch(e){st.innerHTML=`<span class="error">${e.message}</span>`}}

function alignBadge(a){
 if(!a)return "—";
 const map={"FULL ALIGNMENT":"🔥","STOCK-SPECIFIC LEADER":"⚡","EARLY ROTATION":"🔄","LOSING LEADERSHIP":"⚠️","SHORT-TERM SURGE":"💥","EARLY TURN":"👀","MIXED":"•"};
 return `<span class="flag">${map[a]||""} ${a}</span>`;
}
function compactRRG(r){
 if(!r)return "—";
 return `${badge(r.quadrant)}<div class="tiny">${r.rs_up?"RS↑":"RS↓"} · ${r.mom_up?"Mom↑":"Mom↓"}</div>`;
}
function moverHTML(p){if(!p)return'<span class="mover mLOW">UNKNOWN</span>';return`<span class="mover m${p.label}">${p.label}</span><div class="tiny">score ${fmt(p.score,1)}/10 · ${p.behavior}</div>`}
function renderEarnings(){
 let f=document.getElementById("moverFilter").value,arr=earnResults.filter(x=>{let l=(x.profile||{}).label||"LOW";return f==="all"||(f==="hm"&&l!=="LOW")||(f==="high"&&l==="HIGH")});
 document.getElementById("earnRows").innerHTML=arr.map((x,k)=>{let p=x.profile,r=x.rotation||{},id=`det-${x.ticker.replace(/[^A-Z0-9]/g,"")}`;return `<tr><td>${k+1}</td><td><b>${x.ticker}</b><div class="tiny">${x.name||""}</div></td><td>${x.earnings_date}<div class="tiny">${x.earnings_time||""}${x.earnings_time?" · ":""}${x.calendar_days_ago} calendar days ago</div><div class="tiny">${x.earnings_source||""}</div></td><td>${moverHTML(p)}</td><td>${compactRRG(r.fast)}<div class="tiny">Trend: ${r.trend?`${r.trend.quadrant} · ${r.trend.rs_up?"RS↑":"RS↓"} · ${r.trend.mom_up?"Mom↑":"Mom↓"}`:"—"}</div><div class="tiny">${alignBadge(r.alignment)}</div></td><td><button class="detailBtn" data-id="${id}">Earnings history ▾</button></td></tr><tr id="${id}" class="details"><td colspan="6">${detailHTML(x)}</td></tr>`}).join("");
 document.querySelectorAll(".detailBtn").forEach(b=>b.addEventListener("click",()=>document.getElementById(b.dataset.id).classList.toggle("open")));
}
function detailHTML(x){let p=x.profile;if(!p)return'<span class="note">Not enough usable historical earnings events for a profile.</span>';let ev=p.events||[];return `<div class="detailgrid"><div class="metric"><div class="tiny">EVENTS USED</div><b>${p.n}</b></div><div class="metric"><div class="tiny">MEDIAN 1D EXCURSION</div><b>${fmt(p.median_exc1)}%</b></div><div class="metric"><div class="tiny">MEDIAN 5D EXCURSION</div><b>${fmt(p.median_exc5)}%</b></div><div class="metric"><div class="tiny">MEDIAN 10D EXCURSION</div><b>${fmt(p.median_exc10)}%</b></div><div class="metric"><div class="tiny">MEDIAN 14D EXCURSION</div><b>${fmt(p.median_exc14)}%</b></div><div class="metric"><div class="tiny">&gt;5% WITHIN 10D</div><b>${fmt(p.pct_gt5_10d,0)}%</b></div><div class="metric"><div class="tiny">&gt;10% WITHIN 14D</div><b>${fmt(p.pct_gt10_14d,0)}%</b></div></div><div class="tiny" style="margin:12px 0 6px">Prior earnings events · maximum absolute excursion from the pre-event close</div><table class="eventtable"><thead><tr><th>Date</th><th>1D</th><th>3D</th><th>5D</th><th>10D</th><th>14D</th></tr></thead><tbody>${ev.map(e=>`<tr><td>${e.date}</td><td>${fmt(e.exc1)}%</td><td>${fmt(e.exc3)}%</td><td>${fmt(e.exc5)}%</td><td>${fmt(e.exc10)}%</td><td>${fmt(e.exc14)}%</td></tr>`).join("")}</tbody></table>`}
async function runEarnings(){
 let st=document.getElementById("estatus");st.textContent="Scanning earnings dates and building history… this can take a minute.";
 try{let s=document.getElementById("earnSector").value,d=document.getElementById("earnDays").value,l=document.getElementById("earnLimit").value,r=await fetch(`/api/postearnings/${s}?days=${d}&limit=${l}`),j=await r.json();if(!j.ok)throw Error(j.error);earnResults=j.results||[];st.textContent=earnResults.length?`${earnResults.length} recent earnings names found in ${s}`:(j.message||`No recent earnings names found in ${s}`);renderEarnings()}catch(e){st.innerHTML=`<span class="error">${e.message}</span>`}}
document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));b.classList.add("active");document.getElementById(b.dataset.view).classList.add("active")}));
document.getElementById("groupFilter").addEventListener("change",renderGroups);document.getElementById("coreSectorSelect").addEventListener("change",(e)=>{if(e.target.value){currentSector=e.target.value;document.getElementById("sectorTitle").textContent=currentSector+" selected";loadSector();}});document.getElementById("refreshMarket").addEventListener("click",()=>loadMarket(true));document.getElementById("refreshSector").addEventListener("click",loadSector);document.getElementById("runEarnings").addEventListener("click",runEarnings);document.getElementById("moverFilter").addEventListener("change",renderEarnings);loadMarket(false);
</script>
"""
@app.get("/")
def home():
    return Response("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='theme-color' content='#0b0e11'><meta name='apple-mobile-web-app-capable' content='yes'><meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'><title>Market Rotation Screener</title></head><body>"+HTML+"</body></html>", mimetype="text/html")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=PORT,debug=False,threaded=True)
