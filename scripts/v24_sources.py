from pathlib import Path
p=Path('app.py'); s=p.read_text()
def rep(a,b,n):
    global s
    if a not in s: raise SystemExit('missing '+n)
    s=s.replace(a,b,1)
if '_mark_source("yfinance", True)' in s: raise SystemExit(0)
rep('''    if close.empty or close.dropna(how="all").empty:
        raise RuntimeError("Price provider returned no usable data" + (f": {last_err}" if last_err else "."))

    return close.sort_index().dropna(how="all")''','''    if close.empty or close.dropna(how="all").empty:
        _mark_source("yfinance", False, last_err)
        raise RuntimeError("Price provider returned no usable data" + (f": {last_err}" if last_err else "."))

    _mark_source("yfinance", True)
    return close.sort_index().dropna(how="all")''','yfinance')
rep('''                except Exception: detail=r.text
                return {"sessions":[],"weeks":[],"source":source_tf,''','''                except Exception: detail=r.text
                _mark_source("alpaca_stocks", False, detail or r.status_code)
                return {"sessions":[],"weeks":[],"source":source_tf,''','alpaca stock auth')
rep('''        return {"sessions":session_items,"weeks":week_items,"source":source_tf,"error":None}
    except Exception as e:
        return {"sessions":[],"weeks":[],"source":None,"error":str(e)}''','''        _mark_source("alpaca_stocks", True)
        return {"sessions":session_items,"weeks":week_items,"source":source_tf,"error":None}
    except Exception as e:
        _mark_source("alpaca_stocks", False, e)
        return {"sessions":[],"weeks":[],"source":None,"error":str(e)}''','alpaca stock result')
rep('''        return out
    except Exception:
        return {}

def uw_api_get''','''        _mark_source("finnhub", True)
        return out
    except Exception as e:
        _mark_source("finnhub", False, e)
        return {}

def uw_api_get''','finnhub')
rep('''    resp = requests.get(url, params=params or {}, headers=headers, timeout=25)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("data", payload)''','''    try:
        resp = requests.get(url, params=params or {}, headers=headers, timeout=25)
        resp.raise_for_status()
        payload = resp.json()
        _mark_source("unusual_whales", True)
        return payload.get("data", payload)
    except Exception as e:
        _mark_source("unusual_whales", False, e)
        raise''','uw')
rep('''        return out
    except Exception:
        return {}

def yahoo_calendar_for_day''','''        _mark_source("nasdaq_yahoo_calendar", True)
        return out
    except Exception as e:
        _mark_source("nasdaq_yahoo_calendar", False, e)
        return {}

def yahoo_calendar_for_day''','nasdaq')
rep('''        return out
    except Exception:
        return {}

def discover_recent_earnings''','''        _mark_source("nasdaq_yahoo_calendar", True)
        return out
    except Exception as e:
        _mark_source("nasdaq_yahoo_calendar", False, e)
        return {}

def discover_recent_earnings''','yahoo')
rep('''    out={}; token=None
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
    return out''','''    out={}; token=None
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
    return out''','alpaca options')
p.write_text(s)
