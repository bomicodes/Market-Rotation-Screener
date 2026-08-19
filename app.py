
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
APP_VERSION = "22.10"
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
PORT = int(os.environ.get("PORT", "8765"))
SCREENER_PASSWORD = os.environ.get("SCREENER_PASSWORD", "").strip()
UW_API_TOKEN = os.environ.get("UW_API_TOKEN", "").strip()
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
ALPACA_API_KEY = os.environ.get("APCA_API_KEY_ID", os.environ.get("ALPACA_API_KEY", "")).strip()
ALPACA_API_SECRET = os.environ.get("APCA_API_SECRET_KEY", os.environ.get("ALPACA_API_SECRET", "")).strip()
ALPACA_TRADING_BASE_URL = os.environ.get("ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
ALPACA_OPTIONS_FEED = os.environ.get("ALPACA_OPTIONS_FEED", "indicative").strip().lower() or "indicative"
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
        raise RuntimeError("Price provider returned no usable data" + (f": {last_err}" if last_err else "."))

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
    tables = pd.read_html(io.StringIO(resp.text))
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

    # Finnhub documents skip pagination and up to 100 holdings per call.
    for skip in (0, 100, 200, 300, 400):
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

    all_rows = clean_equity_holdings(all_rows)
    if len(all_rows) < 10:
        raise RuntimeError(f"Finnhub returned only {len(all_rows)} usable holdings for {etf}.")
    return all_rows


def get_fund_holdings(etf):
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
            attempts.append(f"Invesco: only {len(h)} usable rows")
        except Exception as e:
            attempts.append(f"Invesco: {e}")

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
        raise RuntimeError(f"Could not retrieve holdings for {etf}. " + " | ".join(attempts))



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
        return out
    except Exception:
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
        return out
    except Exception:
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
    for _ in range(4):
        if token: params["page_token"]=token
        r=requests.get(url,params=params,headers=alpaca_headers(),timeout=25)
        if r.status_code in (401,403):
            raise RuntimeError(f"Alpaca {ALPACA_OPTIONS_FEED} option-chain access was rejected. Check API credentials/feed permissions.")
        if r.status_code==429: raise RuntimeError("Alpaca rate limit reached. Try again shortly.")
        r.raise_for_status()
        j=r.json() or {}
        part=j.get("snapshots") or {}
        if isinstance(part,dict): out.update(part)
        token=j.get("next_page_token")
        if not token: break
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
    if oi>=500 and vol>=100 and spread is not None and spread<=10: liq="Liquid"
    elif oi>=100 and vol>=25 and spread is not None and spread<=15: liq="Tradable"
    else: liq="Thin"
    return {
        "symbol":symbol,"type":(meta or {}).get("type"),"expiration":(meta or {}).get("expiration_date"),
        "strike":strike,"bid":bid,"ask":ask,"mid":mid,"last":last,"last_size":int(last_size),"trade_ts":t.get("t",t.get("timestamp")),"quote_ts":q.get("t",q.get("timestamp")),"volume":int(vol),"open_interest":int(oi),
        "iv":(iv*100 if iv is not None and iv<=5 else iv),"delta":_safe_float(g.get("delta")),
        "gamma":_safe_float(g.get("gamma")),"theta":_safe_float(g.get("theta")),"vega":_safe_float(g.get("vega")),
        "spread_pct":spread,"moneyness_pct":moneyness,"liquidity":liq
    }


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
                raise RuntimeError("Alpaca option-trade access was rejected. Check market-data permissions.")
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


def _cluster_institutional_events(raw, meta):
    """Cluster fragmented prints into contract-level institutional flow events.

    This is intentionally *not* directional classification. It groups nearby prints
    in the same contract when they occur within 90 seconds and at similar prices,
    which helps keep split/block executions from appearing as dozens of unrelated prints.
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
        if cur is not None and cur["symbol"]==sym and ts is not None and cur["end_dt"] is not None:
            gap=(ts-cur["end_dt"]).total_seconds()
            ref=max(cur["vwap"],.01)
            similar=abs(p-ref)/ref<=0.075
            if gap<=90 and similar:
                oldprem=cur["premium"]
                cur["premium"]+=prem; cur["size"]+=sz; cur["prints"]+=1
                cur["vwap"]=(cur["vwap"]*oldprem+p*prem)/max(cur["premium"],1)
                cur["end_dt"]=ts; cur["end_timestamp"]=t.get("t",t.get("timestamp"))
                cur["max_print"]=max(cur["max_print"],prem)
                continue
        if cur is not None: clusters.append(cur)
        r=meta.get(sym,{})
        cur={"symbol":sym,"type":r.get("type"),"expiration":r.get("expiration"),"strike":r.get("strike"),
             "start_dt":ts,"end_dt":ts,"start_timestamp":t.get("t",t.get("timestamp")),"end_timestamp":t.get("t",t.get("timestamp")),
             "vwap":p,"size":sz,"premium":prem,"prints":1,"max_print":prem,
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

def options_quality_payload(ticker):
    ticker=ticker.upper().strip()
    today=pd.Timestamp.now().normalize()
    start=today.strftime("%Y-%m-%d")
    end=(today+pd.Timedelta(days=30)).strftime("%Y-%m-%d")
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
    return {
        "ticker":ticker,"spot":round(spot,2),"dte_min":0,"dte_max":30,"feed":f"Alpaca {ALPACA_OPTIONS_FEED}",
        "chain_updated_at":datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "rv20":round(rv_pct,1) if rv_pct is not None else None,
        "atm_iv":round(atm_iv,1) if atm_iv is not None else None,
        "iv_rv_ratio":round(ratio,2) if ratio is not None else None,
        "iv_state":ivstate,"liquidity":liq,"liquid_contracts":liquid,"tradable_contracts":tradable,
        "contracts_checked":len(rows),"positioning":modeled_dealer_positioning(rows,spot),"contracts":rows[:120]
    }


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
            "results":rows
        })
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


@app.get("/api/options/<ticker>")
def api_options(ticker):
    try:
        force=request.args.get("refresh")=="1"
        payload,stale,err=cached_refresh_safe(f"options-v21-2:{ticker.upper()}",lambda:options_quality_payload(ticker),force=force,ttl=600)
        return jsonify({"ok":True,**payload,"stale":stale,"refresh_error":err})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.get("/api/flow/<ticker>")
def api_flow(ticker):
    try:
        force=request.args.get("refresh") in ("1","true","yes")
        base,_,_=cached_refresh_safe(f"options-v21-2:{ticker.upper()}",lambda:options_quality_payload(ticker),ttl=600)
        payload,stale,err=cached_refresh_safe(f"flow-v21-2:{ticker.upper()}",lambda:flow_payload(ticker,base),force=force,ttl=180)
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
                p,stale,err=cached_refresh_safe(f"options-v21-2:{sym}",lambda:options_quality_payload(sym),ttl=600)
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


@app.get("/api/chart-preview/<ticker>")
def api_chart_preview(ticker):
    ticker=ticker.upper().strip()
    try:
        period=(request.args.get("period") or "1m").lower()
        if period not in ("1m","3m","6m"):
            period="1m"
        # True chart ranges using daily candles.
        df=dl_ohlc(ticker,"1y")
        if df is None or len(df)==0:
            return jsonify({"ok":False,"error":"No price history available."}),404
        df=df.dropna(subset=["Close"]).copy()
        bars={"1m":22,"3m":66,"6m":126}.get(period,22)
        df=df.tail(bars)
        rows=[]
        for idx,row in df.iterrows():
            rows.append({
                "date":pd.Timestamp(idx).strftime("%Y-%m-%d"),
                "open":None if pd.isna(row.get("Open")) else round(float(row.get("Open")),4),
                "high":None if pd.isna(row.get("High")) else round(float(row.get("High")),4),
                "low":None if pd.isna(row.get("Low")) else round(float(row.get("Low")),4),
                "close":round(float(row.get("Close")),4),
                "volume":None if pd.isna(row.get("Volume")) else int(row.get("Volume")),
            })
        return jsonify({"ok":True,"ticker":ticker,"period":period,"bars":rows})
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
    ftc="bullish" if float(last["Close"])>float(last["Open"]) else ("bearish" if float(last["Close"])<float(last["Open"]) else "neutral")
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


@app.get("/api/strat/<ticker>")
def api_strat(ticker):
    ticker=ticker.upper().strip()
    try:
        intraday=yf.download(ticker,period="60d",interval="60m",auto_adjust=True,progress=False,threads=False,prepost=False,timeout=20)
        daily=yf.download(ticker,period="2y",interval="1d",auto_adjust=True,progress=False,threads=False,timeout=20)
        for name,df in (("intraday",intraday),("daily",daily)):
            if df is not None and len(df) and isinstance(df.columns,pd.MultiIndex):
                df.columns=[c[0] for c in df.columns]
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
        return jsonify({"ok":True,"ticker":ticker,"frames":frames,"bullish_count":bulls,"bearish_count":bears,"continuity":continuity})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


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


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "alpaca_api_configured": bool(ALPACA_API_KEY and ALPACA_API_SECRET),
        "finnhub_api_configured": bool(FINNHUB_API_KEY),
        "unusual_whales_api_configured": bool(UW_API_TOKEN),
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
.priceChartControls .previewPeriodBtn{min-width:44px;padding:6px 10px;border-radius:7px;background:#0c151e;border-color:#2a3a4b;color:#9eacbb;font-size:10px;font-weight:800}
.priceChartControls .previewPeriodBtn.active{background:#0f2740;border-color:#2563eb;color:#dbeafe;box-shadow:inset 0 0 0 1px rgba(59,130,246,.15)}
.priceChartCanvasWrap{border:1px solid #203142;border-radius:10px;background:linear-gradient(180deg,#081017,#070d13);overflow:hidden}
#pricePreviewChart{width:100%;height:390px;display:block;background:transparent}
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
.dashCol{min-width:0}.dashCol .panel{margin:0 0 12px}.dashTitle{font-size:13px;font-weight:800;letter-spacing:.25px}.dashTopline{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}.dashTopline .note{font-size:10px}
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
/* v22.11 candlestick proportion fix */
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

/* v22.11 layout + STRAT confluence */
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
@media(max-width:1050px){.priceActionGrid{grid-template-columns:1fr}.stratFrames{grid-template-columns:repeat(4,minmax(0,1fr))}.stratFoot{margin-top:10px}.rrgSelectFilters{flex-wrap:wrap}.rrgSelectFilters label{min-width:150px}.dashRight .sectorSummaryPanel{min-height:420px}}
@media(max-width:760px){#sectorChart{height:470px}.rrgFilterBar{align-items:stretch}.rrgSelectFilters{display:grid;grid-template-columns:1fr}.rrgSelectFilters label{max-width:none}.rrgInlineFilters{margin-left:0}.stratFrames{grid-template-columns:1fr 1fr}#pricePreviewChart{aspect-ratio:1080/620}}
@media(max-width:1100px){.rrgControlStack{align-items:stretch}.rrgInlineFilters{flex-wrap:wrap}.rrgInlineFilters .filterPills{justify-content:flex-start}.dashRight .sectorSummaryPanel .scroll{max-height:300px}}

</style>
<div class="wrap">
<header class="appHeader">
  <div class="brand"><div class="brandMark">↗</div><div class="brandText"><b>MARKET ROTATION SCREENER</b><span>ROTATION · POSITIONING · OPTIONS</span></div></div>
  <nav class="tabs appNav">
    <button class="tab active" data-view="rotation"><span class="navIcon">▦</span><span>Dashboard</span></button>
    <button class="tab" data-view="history"><span class="navIcon">⌁</span><span>RRG Historical</span></button>
    <button class="tab" data-view="gexpage" id="navGex"><span class="navIcon">⌗</span><span>GEX Landscape</span></button>
    <button class="tab" data-view="earnings"><span class="navIcon">◫</span><span>Earnings Movers</span></button>
    <button class="navJump" id="navOptions"><span class="navIcon">▤</span><span>Options Scanner</span></button>
    <button class="navJump" id="navWatch"><span class="navIcon">☆</span><span>Watchlist</span></button>
    <button class="tab" data-view="heatmap"><span class="navIcon">▦</span><span>Heat Map</span></button>
  </nav>
  <div class="headerMeta"><button class="headerRefresh" id="dashRefreshMarket">↻ Refresh</button><span class="versionPill">v22.11</span></div>
</header>
<div class="pageIntro"><h1>Market Rotation Screener</h1><div class="sub">Fast RRG (10/5) finds change; Trend RRG (25/12) confirms persistence.</div></div>

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
      <div class="gexTickerControls"><input id="gexTickerInput" type="search" placeholder="Ticker, e.g. NFLX" autocomplete="off"><button id="gexTickerLoad" class="primary">Load GEX</button></div>
    </div>
    <div id="gexPageHint" class="gexPageHint">Select a stock anywhere in the screener or enter a ticker above. The latest loaded ticker carries into this page automatically.</div>
  </div>
  <div id="gexPageHost"></div>
</div>

<div id="rotation" class="view active">
  <div class="dashboardGrid">
    <aside class="dashCol dashLeft">
      <div class="panel">
        <div class="dashTopline"><span class="dashTitle">MARKET OVERVIEW</span><span id="dashboardUpdated" class="note">Awaiting refresh</span></div>
        <div id="dashboardMarketOverview" class="marketOverviewGrid"></div>
      </div>
      <div class="panel">
        <div class="dashTopline"><span class="dashTitle">SECTOR ROTATION HEAT MAP</span><span class="note">Composite</span></div>
        <div class="heatModeTabs" style="margin-bottom:8px"><button id="dashHeatComposite" class="active">Composite</button><button id="dashHeatFast">Fast 10/5</button><button id="dashHeatTrend">Trend 25/12</button></div>
        <div id="dashboardHeatGrid" class="dashHeatGrid"></div>
        <div class="heatScale"></div><div class="heatScaleLabels"><span>Weak</span><span>Neutral</span><span>Strong</span></div>
      </div>
      <div class="panel">
        <div class="dashTopline"><span class="dashTitle">BREADTH & RISK</span><span id="regimeSummary" class="note">Loading…</span></div>
        <div id="dashboardBreadth" class="breadthList"></div>
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
      <div class="sideSection sectorSummaryPanel"><div class="dashTopline"><span class="dashTitle">SECTOR SUMMARY</span><span class="note">Fast + Trend</span></div><div class="scroll"><table><thead><tr><th>#</th><th>Sector</th><th>Fast</th><th>Trend</th></tr></thead><tbody id="sectorRows"></tbody></table></div></div>
    </aside>
  </div>
  <div class="legacyMarketBlock"><button class="primary" id="refreshMarket">Refresh market</button><div id="internals" class="cards"></div></div>
  <div class="rotationLower">
  <div class="panel">
    <div class="row"><strong>Stock screen</strong><span class="note">Tip: click a ticker row or chart label to focus it. All displayed tails stay visible; the others dim. Click the selected ticker again to clear. Search/filters use loaded data and cached universes repopulate instantly.</span>
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
        <div class="priceChartControls">
          <button id="preview1M" class="previewPeriodBtn active">1M</button>
          <button id="preview3M" class="previewPeriodBtn">3M</button>
          <button id="preview6M" class="previewPeriodBtn">6M</button>
        </div>
      </div>
      <div class="priceChartCanvasWrap"><canvas id="pricePreviewChart" width="1080" height="620"></canvas></div>
      <div class="priceChartFooter"><span id="previewStatus" class="status">Select a ticker to preview price.</span><span class="tiny">Daily candles + volume · 1M / 3M / 6M structure view.</span></div>
    </div>
    <aside class="panel stratPanel" id="stratPanel">
      <div class="stratHead"><div><div class="dashTitle">PRICE ACTION · STRAT</div><div class="note">1H · 4H · 1D · 1W trigger confluence</div></div><span id="stratContinuity" class="stratContinuity">—</span></div>
      <div id="stratStatus" class="tiny">Select a ticker to load STRAT scenarios.</div>
      <div id="stratFrames" class="stratFrames"></div>
      <div class="stratFoot tiny">FTC compares each current timeframe candle with its open. Trigger levels are the current bar high/low for the next directional break.</div>
    </aside>
  </div>

  <div class="panel" id="optionsPanel">
    <div class="row">
      <strong>Options · 0–30 DTE</strong>
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
      <div id="positioningSummary" class="positioningGrid gammaSummary"></div>
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
    <div class="row"><strong>Post-Earnings Screen</strong>
      <label class="note">Sector</label>
      <select id="earnSector">""" + "".join([f'<option value="{k}">{k} · {v}</option>' for k,v in RRG_UNIVERSE.items()]) + r"""</select>
      <label class="note">Reported within</label><select id="earnDays"><option>5</option><option selected>10</option><option>14</option><option>20</option></select><span class="note">trading days (approx.)</span>
      <span class="note">All ETF holdings are scanned automatically</span>
      <label class="note">Mover filter</label><select id="moverFilter"><option value="all">All</option><option value="hm">High + Moderate</option><option value="high">High only</option></select>
      <label class="note">Search</label><input id="earnTickerSearch" type="search" placeholder="Ticker / name…" autocomplete="off" style="width:120px">
      <button class="primary" id="runEarnings">Scan earnings</button><span id="estatus" class="status"></span>
    </div>
    <div class="note" style="margin-top:9px">Earnings source priority: Finnhub → Nasdaq public calendar → Yahoo calendar → limited ticker-history fallback. Recent earnings discovery uses a calendar-level check plus ticker history. Historical mover profiles are loaded on demand when you tap Earnings history. If a free source cannot provide enough completed prior events, the row will now say so explicitly instead of appearing stuck.</div>
  </div>
  <div class="panel">
    <table><thead><tr><th>#</th><th>Ticker</th><th>Recent earnings</th><th>Historical mover</th><th>Rotation vs sector</th><th>Details</th></tr></thead><tbody id="earnRows"></tbody></table>
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
<script>
let sectorData=[],currentSector=null,earnResults=[],liveStockData=[],liveSearchData=[],liveSearchSector=null,liveSearchLoading=false,sectorRequestSeq=0,previewTicker=null,previewPeriod="1m",previewRequestSeq=0;
let sectorRRGMode="fast", sectorQuadrantFilter="all", dashboardPayload=null, dashboardHeatMode="composite";
const clientCache={market:null,sectors:new Map(),historical:new Map()};
function cacheKeySector(etf,limit){return `${etf}|${limit}`}
function cacheKeyHistory(mode,etf,date,limit){return `${mode}|${etf}|${date}|${limit}`}


const LIVE_WATCHLIST_KEY="marketRotationLiveWatchlistV1";
let liveWatchlist=[];

function loadLiveWatchlist(){
 try{
   const raw=localStorage.getItem(LIVE_WATCHLIST_KEY);
   liveWatchlist=raw?JSON.parse(raw):[];
   if(!Array.isArray(liveWatchlist))liveWatchlist=[];
 }catch(e){liveWatchlist=[]}
}

function saveLiveWatchlist(){
 try{localStorage.setItem(LIVE_WATCHLIST_KEY,JSON.stringify(liveWatchlist))}catch(e){}
 renderLiveWatchlist();
 refreshLiveBookmarkButtons();
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
 if(i>=0)liveWatchlist.splice(i,1);
 else liveWatchlist.unshift(item);
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
     loadChartPreview(t);
     if(alpacaConfigured!==false)loadOptionsTicker(t,{scroll:false});
   }));
   document.querySelectorAll("[data-live-watch-remove]").forEach(btn=>btn.addEventListener("click",()=>{
     const key=liveWatchKey(btn.dataset.liveWatchRemove);
     liveWatchlist=liveWatchlist.filter(x=>liveWatchKey(x.ticker)!==key);
     saveLiveWatchlist();
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
function drawRRG(id,rows,focusTicker=undefined){
 rows=rrgRowsForChart(id,rows);
 const c=document.getElementById(id),ctx=c.getContext("2d"),W=c.width,H=c.height,p=42;
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
 rows.forEach(r=>(r.tail||[]).forEach(pt=>{xs.push(pt.x);ys.push(pt.y)}));
 let xmin=Math.min(98,...xs)-.8,xmax=Math.max(102,...xs)+.8,
     ymin=Math.min(98,...ys)-.8,ymax=Math.max(102,...ys)+.8;

 const X=x=>p+(x-xmin)/(xmax-xmin)*(W-2*p),
       Y=y=>H-p-(y-ymin)/(ymax-ymin)*(H-2*p),
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
   if(id==="stockChart"){
     loadChartPreview(ticker);
     if(alpacaConfigured!==false)loadOptionsTicker(ticker,{scroll:false});
   }
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
 if(regime){
   const score=j.risk_score==null?"—":`${Math.max(0,Math.min(4,Math.round((j.risk_score+4)/2)))}/4`;
   regime.innerHTML=`<b>${j.risk_appetite||"Mixed"}</b> · Participation: <b>${j.participation||"—"}</b> · Risk support ${score}`;
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
   const params=new URLSearchParams({limit:String(lim)});
   const url=`/api/sector/${encodeURIComponent(requestedSector)}?${params.toString()}`;
   const r=await fetch(url,{headers:{"Accept":"application/json"}});
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
   if(scrollToStocks)setTimeout(()=>document.getElementById("stockHeatTitle")?.scrollIntoView({behavior:"smooth",block:"start"}),50);
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
     const r=await fetch(`/api/sector/${currentSector}?limit=all`);
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
       box.innerHTML='<b>✓ Alpaca connected</b><span class="note">Options screening is ready. Dealer positioning + sampled flow will load with each analyzed ticker.</span>';
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

function focusOptionsPanel(){
 const panel=document.getElementById("optionsPanel");
 if(panel)panel.scrollIntoView({behavior:"smooth",block:"start"});
}

let optionScanMap={},activeOptionsData=null;

function optionBadgeHTML(x){
 if(!x)return '<span class="tiny">Not scanned</span>';
 if(x.error||x.ok===false)return '<span class="optBadge optBad">Error</span>';
 const liq=x.liquidity||"—",cls=liq==="Liquid"?"optGood":liq==="Tradable"?"optWarn":"optBad";
 return `<span class="optBadge ${cls}">${liq}</span><div class="tiny">${x.iv_state||"—"}</div>`;
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
 const minS=rows[0].strike,maxS=rows[rows.length-1].strike;
 const yForStrike=v=>pad.t+(Number(v)-minS)/(Math.max(.0001,maxS-minS))*(H-pad.t-pad.b);
 function rail(v,color,label,dash=[],side="left"){
   if(v==null||v<minS||v>maxS)return;const y=yForStrike(v);ctx.save();ctx.setLineDash(dash);ctx.strokeStyle=color;ctx.lineWidth=1.55;ctx.beginPath();ctx.moveTo(plotL,y);ctx.lineTo(plotR,y);ctx.stroke();ctx.setLineDash([]);
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
 selectedGammaStrike=null;renderGexRail(p,spot);drawGammaLandscape(p,spot);
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
 try{const r=await fetch(`/api/flow/${encodeURIComponent(ticker)}${force?"?refresh=1":""}`),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Flow request failed");activeFlowData=j;renderFlow(j);renderHeatMap()}catch(e){if(st)st.innerHTML=`<span class="error">${e.message}</span>`}
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
 <td>${optionBadgeHTML({liquidity:r.liquidity,iv_state:""})}</td>
 </tr>`).join(""):'<tr><td colspan="11" class="note">No contracts match these filters.</td></tr>';
}
async function loadOptionsTicker(ticker,opts={}){
 if(opts.scroll!==false)focusOptionsPanel();
 const st=document.getElementById("optionsStatus");
 if(alpacaConfigured===false){
   st.innerHTML='<span class="error">Connect Alpaca first using the blue button above, then add the API key + secret in Render.</span>';
   return;
 }
 st.textContent=`Loading ${ticker} options…`;
 try{
   const r=await fetch(`/api/options/${encodeURIComponent(ticker)}`),j=await r.json();
   if(!r.ok||!j.ok)throw Error(j.error||"Options request failed");
   activeOptionsData=j;optionScanMap[ticker]=j;
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
   renderOptionsPanel();renderLiveStocks();loadFlowTicker(ticker,false);
 }catch(e){st.innerHTML=`<span class="error">${e.message}</span>`}
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
 cont.textContent=data?.continuity==="bullish"?`${data.bullish_count}/4 BULLISH FTC`:data?.continuity==="bearish"?`${data.bearish_count}/4 BEARISH FTC`:`${data?.bullish_count||0}↑ / ${data?.bearish_count||0}↓ MIXED`;
 status.textContent=`${data.ticker} · multi-timeframe price-action confirmation`;
 box.innerHTML=frames.map(f=>{
   const cls=stratScenarioClass(f),arrow=cls==="bullish"?"↑":cls==="bearish"?"↓":"↔";
   const up=Number(f.up_trigger),dn=Number(f.down_trigger);
   return `<div class="stratFrame"><div class="stratFrameTop"><span class="stratTf">${f.timeframe}</span><span class="stratScenario ${cls}">${f.scenario||"—"} ${arrow}</span></div><div class="stratPattern">${f.pattern||"—"}</div><div class="stratTrigger"><div class="up">UP TRIGGER<b>${Number.isFinite(up)?`$${up.toFixed(2)}`:"—"}</b></div><div class="down">DOWN TRIGGER<b>${Number.isFinite(dn)?`$${dn.toFixed(2)}`:"—"}</b></div></div><div class="stratFTC">FTC: ${(f.ftc||"neutral").toUpperCase()}</div></div>`;
 }).join("");
}
async function loadStrat(ticker){
 const seq=++stratRequestSeq,status=document.getElementById("stratStatus"),box=document.getElementById("stratFrames"),cont=document.getElementById("stratContinuity");
 if(!ticker)return;
 if(status)status.textContent=`Loading ${ticker} STRAT…`;
 if(box)box.innerHTML="";
 if(cont){cont.className="stratContinuity";cont.textContent="…";}
 try{
   const r=await fetch(`/api/strat/${encodeURIComponent(ticker)}`),j=await r.json();
   if(seq!==stratRequestSeq)return;
   if(!r.ok||!j.ok)throw Error(j.error||"STRAT load failed");
   renderStrat(j);
 }catch(e){if(status)status.innerHTML=`<span class="error">STRAT unavailable: ${e.message}</span>`;}
}

function drawPricePreview(payload){
 const c=document.getElementById("pricePreviewChart"),ctx=c?.getContext("2d");
 if(!c||!ctx)return;
 const rows=payload?.bars||[],W=c.width,H=c.height;
 ctx.clearRect(0,0,W,H);
 const bg=ctx.createLinearGradient(0,0,0,H);bg.addColorStop(0,"#081119");bg.addColorStop(1,"#070d13");ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
 if(!rows.length){ctx.fillStyle="#7f8c9d";ctx.font="12px sans-serif";ctx.fillText("Select a ticker to load daily candles",24,34);return}
 const pad={l:46,r:74,t:24,b:48},volH=82,volGap=14;
 const highs=rows.map(x=>Number(x.high??x.close)),lows=rows.map(x=>Number(x.low??x.close));
 let lo=Math.min(...lows),hi=Math.max(...highs),range=Math.max(.01,hi-lo);lo-=range*.07;hi+=range*.07;
 const priceBottom=H-pad.b-volH-volGap,plotW=W-pad.l-pad.r;
 const X=i=>pad.l+(i+.5)*plotW/rows.length;
 const Y=v=>pad.t+(hi-v)/(hi-lo)*(priceBottom-pad.t);
 ctx.font="10px ui-monospace, SFMono-Regular, Menlo, monospace";ctx.textAlign="left";
 const gridCount=5;
 for(let k=0;k<gridCount;k++){const y=pad.t+k*(priceBottom-pad.t)/(gridCount-1),level=hi-k*(hi-lo)/(gridCount-1);ctx.strokeStyle="#1b2833";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(W-pad.r,y);ctx.stroke();ctx.fillStyle="#758495";ctx.fillText(`$${level.toFixed(2)}`,W-pad.r+9,y+3)}
 let lastMonth="";
 rows.forEach((r,i)=>{const d=new Date(`${r.date}T00:00:00`);if(Number.isNaN(d.getTime()))return;const key=`${d.getFullYear()}-${d.getMonth()}`;if(key!==lastMonth){if(i>1){const x=X(i);ctx.strokeStyle="#101c25";ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,H-pad.b);ctx.stroke();ctx.fillStyle="#687789";ctx.textAlign="center";ctx.fillText(d.toLocaleString(undefined,{month:"short"}),x,H-15);ctx.textAlign="left"}lastMonth=key}});
 const maxVol=Math.max(1,...rows.map(x=>Number(x.volume||0))),bw=Math.max(3,Math.min(15,plotW/rows.length*.58));
 rows.forEach((r,i)=>{const x=X(i),o=Number(r.open??r.close),cl=Number(r.close),up=cl>=o,bull="#16c784",bear="#ef4444";ctx.strokeStyle=up?bull:bear;ctx.fillStyle=up?bull:bear;ctx.lineWidth=1.1;if(r.high!=null&&r.low!=null){ctx.beginPath();ctx.moveTo(x,Y(Number(r.high)));ctx.lineTo(x,Y(Number(r.low)));ctx.stroke()}const yo=Y(o),yc=Y(cl),top=Math.min(yo,yc),h=Math.max(2,Math.abs(yc-yo));ctx.fillRect(x-bw/2,top,bw,h);const vh=Number(r.volume||0)/maxVol*volH;ctx.globalAlpha=.42;ctx.fillRect(x-bw/2,H-pad.b-vh,bw,vh);ctx.globalAlpha=1});
 ctx.strokeStyle="#17232d";ctx.beginPath();ctx.moveTo(pad.l,H-pad.b-volH-volGap/2);ctx.lineTo(W-pad.r,H-pad.b-volH-volGap/2);ctx.stroke();
 const last=rows[rows.length-1],first=rows[0],lastPx=Number(last.close),firstPx=Number(first.close),chg=(lastPx/firstPx-1)*100;
 if(Number.isFinite(lastPx)){const py=Y(lastPx);ctx.save();ctx.font="bold 10px ui-monospace, SFMono-Regular, Menlo, monospace";const label=`$${lastPx.toFixed(2)}`,tw=ctx.measureText(label).width+12,bx=W-pad.r+4,by=Math.max(pad.t,Math.min(priceBottom-18,py-9));ctx.fillStyle=chg>=0?"#d9fbe8":"#ffe0e0";ctx.strokeStyle=chg>=0?"#2f9e6d":"#b94a4a";ctx.lineWidth=1;ctx.beginPath();if(ctx.roundRect){ctx.roundRect(bx,by,tw,18,4)}else{ctx.rect(bx,by,tw,18)}ctx.fill();ctx.stroke();ctx.fillStyle=chg>=0?"#0f5132":"#7f1d1d";ctx.textAlign="center";ctx.fillText(label,bx+tw/2,by+12);ctx.restore()}
 ctx.font="bold 11px ui-monospace, SFMono-Regular, Menlo, monospace";ctx.fillStyle=chg>=0?"#7ee2ad":"#f38b8b";ctx.textAlign="left";ctx.fillText(`${lastPx.toFixed(2)}  ${chg>=0?"+":""}${chg.toFixed(2)}%`,pad.l,H-15);
}

async function loadChartPreview(ticker,period=previewPeriod){
 if(!ticker)return;
 previewTicker=ticker;previewPeriod=period;
 loadStrat(ticker);
 const seq=++previewRequestSeq,st=document.getElementById("previewStatus"),title=document.getElementById("previewTitle");
 if(title)title.textContent=`${ticker} · Chart Preview`;
 if(st)st.textContent=`Loading ${period.toUpperCase()}…`;
 document.getElementById("preview1M")?.classList.toggle("active",period==="1m");
 document.getElementById("preview3M")?.classList.toggle("active",period==="3m");
 document.getElementById("preview6M")?.classList.toggle("active",period==="6m");
 try{
   const r=await fetch(`/api/chart-preview/${encodeURIComponent(ticker)}?period=${period}`);
   const j=await r.json();
   if(seq!==previewRequestSeq)return;
   if(!r.ok||!j.ok)throw Error(j.error||"Chart preview failed");
   drawPricePreview(j);
   const bars=j.bars||[],last=bars[bars.length-1],first=bars[0];
   const lp=document.getElementById("previewLastPrice"),meta=document.getElementById("previewMeta");
   if(lp&&last){const ch=first&&Number(first.close)?(Number(last.close)/Number(first.close)-1)*100:0;lp.textContent=`$${Number(last.close).toFixed(2)}  ${ch>=0?"+":""}${ch.toFixed(2)}%`;lp.style.color=ch>=0?"#7ee2ad":"#f38b8b";}
   if(meta)meta.textContent=`1D candles · ${period.toUpperCase()} range · ${bars.length} sessions`;
   if(st)st.textContent=`${period.toUpperCase()} · ${bars.length} daily candles`;
 }catch(e){
   if(seq!==previewRequestSeq)return;
   if(st)st.innerHTML=`<span class="error">${e.message}</span>`;
   drawPricePreview({bars:[]});
 }
}

function heatTone(score){return `h${Math.max(0,Math.min(10,Math.round(score||0)))}`;}
function sectorHeatScore(x){
 const st=rotationStage(x).level; let s=st*2; const f=x.fast||x||{},t=x.trend||{};
 if(f.tail_trajectory==="Rotating In")s+=1; if(f.tail_trajectory==="Rotating Out")s-=1;
 if((t.quadrant==="Leading"||t.quadrant==="Improving")&&t.rs_up&&t.mom_up)s+=1;
 return Math.max(0,Math.min(10,s));
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
 tg.innerHTML=stocks.length?[...stocks].sort((a,b)=>opportunityScore(b)-opportunityScore(a)).map(x=>{const sc=opportunityScore(x),o=optionScanMap[x.ticker],flow=(activeFlowData?.ticker===x.ticker)?activeFlowData:null;let meta=`${rotationStage(x).label}`;if(o?.liquidity)meta+=` · ${o.liquidity}`;if(flow)meta+=` · Flow ${moneyShort(flow.institutional_premium||0)}`;return `<div class="heatTile ${heatTone(sc)}" data-heat-stock="${x.ticker}"><div class="heatHead"><div><div class="heatTicker">${x.ticker}</div><div class="tiny">${x.fast?.quadrant||x.quadrant||'—'}</div></div><div class="heatScore">${sc.toFixed(1)}</div></div><div class="heatMeta">${meta}</div><div class="heatTags">${heatTagsFor(x,true)}</div></div>`}).join(""):'<div class="note">Click a sector/group tile above to load its stock opportunity map.</div>';
 document.querySelectorAll("[data-heat-stock]").forEach(el=>el.addEventListener("click",async()=>{const t=el.dataset.heatStock,hs=document.getElementById("heatStatus");document.querySelectorAll("[data-heat-stock]").forEach(n=>n.classList.toggle("selected",n.dataset.heatStock===t));if(hs)hs.textContent=`Opening ${t} chart + positioning…`;document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("active",x.dataset.view==="rotation"));document.querySelectorAll(".view").forEach(x=>x.classList.toggle("active",x.id==="rotation"));await loadChartPreview(t);if(alpacaConfigured!==false)await loadOptionsTicker(t,{scroll:false});setTimeout(()=>document.getElementById("pricePreviewChart")?.scrollIntoView({behavior:"smooth",block:"center"}),80);}));
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
 return Math.max(0,Math.min(10,score));
}
function opportunityHTML(x){
 const s=opportunityScore(x),stars=Math.max(1,Math.min(5,Math.ceil(s/2)));
 return `<b>${"★".repeat(stars)}${"☆".repeat(5-stars)}</b><div class="tiny">${s}/10 · rotation + options</div>`;
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
 document.querySelectorAll("[data-live-ticker]").forEach(row=>row.addEventListener("click",()=>{
   const ticker=row.dataset.liveTicker;
   toggleRRGFocus("stockChart",ticker);
   if(ticker){
     loadChartPreview(ticker);
     if(alpacaConfigured!==false)loadOptionsTicker(ticker,{scroll:false});
   }
 }));
 refreshLiveBookmarkButtons();syncLiveRowSelection();
 renderInternalRotation();
 renderHeatMap();
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
 let f=document.getElementById("moverFilter").value;
 const search=(document.getElementById("earnTickerSearch")?.value||"").trim().toUpperCase();
 let arr=earnResults.filter(x=>{
   let l=(x.profile||{}).label||"UNKNOWN";
   const moverOk=f==="all"||(f==="hm"&&(l==="HIGH"||l==="MODERATE"))||(f==="high"&&l==="HIGH");
   const searchOk=!search||String(x.ticker||"").toUpperCase().includes(search)||String(x.name||"").toUpperCase().includes(search);
   return moverOk&&searchOk;
 });
 document.getElementById("earnRows").innerHTML=arr.map((x,k)=>{let p=x.profile,r=x.rotation||{},id=`det-${x.ticker.replace(/[^A-Z0-9]/g,"")}`;return `<tr><td>${k+1}</td><td><b>${x.ticker}</b><div class="tiny">${x.name||""}</div></td><td>${x.earnings_date}<div class="tiny">${x.earnings_time||""}${x.earnings_time?" · ":""}${x.calendar_days_ago} calendar days ago</div><div class="tiny">${x.earnings_source||""}</div></td><td>${moverHTML(p)}</td><td>${compactRRG(r.fast)}<div class="tiny">Trend: ${r.trend?`${r.trend.quadrant} · ${r.trend.rs_up?"RS↑":"RS↓"} · ${r.trend.mom_up?"Mom↑":"Mom↓"}`:"—"}</div><div class="tiny">${alignBadge(r.alignment)}</div></td><td><button class="detailBtn" data-id="${id}" data-ticker="${x.ticker}" data-event="${x.earnings_date}">Earnings history ▾</button></td></tr><tr id="${id}" class="details"><td colspan="6">${detailHTML(x)}</td></tr>`}).join("");
 document.querySelectorAll(".detailBtn").forEach(b=>b.addEventListener("click",()=>{
   const id=b.dataset.id;
   const row=document.getElementById(id);
   const ticker=b.dataset.ticker;
   const eventDate=b.dataset.event;
   const item=earnResults.find(x=>x.ticker===ticker);
   if(row)row.classList.toggle("open");
   if(item && !item.profile && !item.historyLoading && !item.historyError){
      loadHistory(ticker,eventDate,id);
   }
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

async function runEarnings(){
 let st=document.getElementById("estatus");
 st.textContent="Finding recent reporters and calculating current rotation…";
 try{
   const s=document.getElementById("earnSector").value;
   const days=document.getElementById("earnDays").value;
   const params=new URLSearchParams({days:String(days)});
   const response=await fetch(`/api/postearnings/${encodeURIComponent(s)}?${params.toString()}`);
   const raw=await response.text();
   let j;
   try{j=JSON.parse(raw)}catch(parseErr){throw Error(`Server returned an unreadable response (${response.status}). Please retry.`)}
   if(!response.ok || !j.ok)throw Error(j.error||`Scan failed (${response.status})`);
   earnResults=j.results||[];
   const diag=j.earnings_diagnostics||{};
   st.textContent=(earnResults.length?`${earnResults.length} recent earnings names found`:(j.message||"No recent earnings names found"))+
     ` · ${j.holdings_total_loaded||"?"} holdings scanned · ${j.holdings_source||""}`+
     ` · Finnhub ${diag.finnhub||0}, Nasdaq ${diag.nasdaq||0}, Yahoo ${diag.yahoo||0}, targeted ${diag.ticker_history||0}`;
   renderEarnings();
 }catch(e){
   st.innerHTML=`<span class="error">${e.message}</span>`;
 }
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

document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>{
 document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
 document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));
 b.classList.add("active");
 const target=document.getElementById(b.dataset.view);if(target)target.classList.add("active");
 if(b.dataset.view==="heatmap")renderHeatMap();
 if(b.dataset.view==="gexpage")mountGexPage(); else restoreGexSection();
}));
document.getElementById("groupFilter").addEventListener("change",renderGroups);document.getElementById("macroBasketFilter").addEventListener("change",renderGroups);document.getElementById("coreSectorSelect").addEventListener("change",(e)=>{
 if(e.target.value)selectSector(e.target.value,{source:"dropdown"});
});document.getElementById("refreshMarket").addEventListener("click",()=>loadMarket(true));document.getElementById("liveHoldingsLimit").addEventListener("change",loadSector);document.getElementById("liveQuadrantFilter").addEventListener("change",renderLiveStocks);document.getElementById("liveTailFilter").addEventListener("change",renderLiveStocks);document.getElementById("preview1M").addEventListener("click",()=>{if(previewTicker)loadChartPreview(previewTicker,"1m")});
document.getElementById("preview3M").addEventListener("click",()=>{if(previewTicker)loadChartPreview(previewTicker,"3m")});
document.getElementById("preview6M").addEventListener("click",()=>{if(previewTicker)loadChartPreview(previewTicker,"6m")});
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
document.getElementById("refreshFlow")?.addEventListener("click",()=>{if(activeOptionsData?.ticker)loadFlowTicker(activeOptionsData.ticker,true)});
document.getElementById("refreshLiveWatchlist").addEventListener("click",refreshLiveWatchlistData);
document.getElementById("clearLiveWatchlist").addEventListener("click",()=>{
 liveWatchlist=[];
 saveLiveWatchlist();
});document.getElementById("runEarnings").addEventListener("click",runEarnings);document.getElementById("moverFilter").addEventListener("change",renderEarnings);document.getElementById("earnTickerSearch").addEventListener("input",renderEarnings);document.getElementById("rrgFastBtn")?.addEventListener("click",()=>setSectorRRGMode("fast"));
document.getElementById("rrgTrendBtn")?.addEventListener("click",()=>setSectorRRGMode("trend"));
document.getElementById("dashboardSectorSelect")?.addEventListener("change",async e=>{if(e.target.value){toggleRRGFocus("sectorChart",e.target.value);await selectSector(e.target.value,{source:"dashboard"})}});
document.querySelectorAll("#sectorQuadPills .filterPill").forEach(btn=>btn.addEventListener("click",()=>{sectorQuadrantFilter=btn.dataset.q||"all";document.querySelectorAll("#sectorQuadPills .filterPill").forEach(x=>x.classList.toggle("active",x===btn));renderGroups();}));
document.getElementById("dashHeatComposite")?.addEventListener("click",()=>{dashboardHeatMode="composite";document.getElementById("dashHeatComposite")?.classList.add("active");document.getElementById("dashHeatFast")?.classList.remove("active");document.getElementById("dashHeatTrend")?.classList.remove("active");renderDashboardHeat();});
document.getElementById("dashHeatFast")?.addEventListener("click",()=>{dashboardHeatMode="fast";document.getElementById("dashHeatFast")?.classList.add("active");document.getElementById("dashHeatComposite")?.classList.remove("active");document.getElementById("dashHeatTrend")?.classList.remove("active");renderDashboardHeat();});
document.getElementById("dashHeatTrend")?.addEventListener("click",()=>{dashboardHeatMode="trend";document.getElementById("dashHeatTrend")?.classList.add("active");document.getElementById("dashHeatFast")?.classList.remove("active");document.getElementById("dashHeatComposite")?.classList.remove("active");renderDashboardHeat();});
document.getElementById("dashRefreshMarket")?.addEventListener("click",()=>loadMarket(true));
function activateViewById(id){document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("active",x.dataset.view===id));document.querySelectorAll(".view").forEach(x=>x.classList.toggle("active",x.id===id));if(id==="heatmap")renderHeatMap();if(id==="gexpage")mountGexPage();else restoreGexSection();}
loadLiveWatchlist();renderLiveWatchlist();checkAlpacaStatus();loadMarket(false);
</script>
"""
@app.errorhandler(500)
def internal_error(err):
    app.logger.exception("Unhandled server error: %s", err)
    return Response("Internal Server Error — check Render logs for the Python traceback.", status=500, mimetype="text/plain")

@app.get("/")
def home():
    # Important: rendering the shell performs no external network requests.
    shell = "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='theme-color' content='#0b0e11'><meta name='apple-mobile-web-app-capable' content='yes'><meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'><title>Market Rotation Screener</title></head><body>" + str(HTML) + "</body></html>"
    return Response(shell, mimetype="text/html")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=PORT,debug=False,threaded=True)
