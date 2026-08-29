from pathlib import Path

p=Path('app.py')
s=p.read_text()

# Keep this patch independent of the pending v26.6 work: accept either v26.5 or v26.6.
if 'APP_VERSION = "26.5"' in s:
    s=s.replace('APP_VERSION = "26.5"','APP_VERSION = "26.7"',1)
elif 'APP_VERSION = "26.6"' in s:
    s=s.replace('APP_VERSION = "26.6"','APP_VERSION = "26.7"',1)
elif 'APP_VERSION = "26.7"' not in s:
    raise AssertionError('Unexpected APP_VERSION; refusing to patch')

old='''     await Promise.all(candidates.slice(n,n+6).map(async x=>{\n       try{\n         const r=await fetch(`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`),j=await r.json();\n         if(r.ok&&j.ok)x._earlyPriceSignal=v262DailyReversalSignal(j);\n       }catch(e){}\n     }));'''
new='''     await Promise.all(candidates.slice(n,n+6).map(async x=>{\n       const ac=new AbortController();\n       const timer=setTimeout(()=>ac.abort(),8000);\n       try{\n         const r=await fetch(`/api/chart-preview/${encodeURIComponent(x.ticker)}?period=1m&timeframe=1d`,{signal:ac.signal}),j=await r.json();\n         if(r.ok&&j.ok)x._earlyPriceSignal=v262DailyReversalSignal(j);\n       }catch(e){ /* timeout or network error on one ticker should not block the rest of the scan */ }\n       finally{clearTimeout(timer)}\n     }));'''

if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise AssertionError('Layer 2.5 fetch block not found; refusing to patch')

p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
entry='''v26.7 — FIX LAYER 2.5 SCAN STALL (INDEPENDENT OF THE PENDING v26.6 PATCH)\n- Reported bug: on web (not mobile), a Top Setups scan that had genuinely started would stall indefinitely at "Layer 2.5 · checking early daily reversals..." and never complete. Root cause confirmed: this step fetches /api/chart-preview/<ticker> for up to 90 candidates in batches of 6, with no timeout on any individual request. Promise.all waits for every request in a batch to settle, so a single slow/hanging request (a stuck Alpaca response, a dropped connection) froze the entire batch, and therefore the entire scan, forever.\n- Fixed: each fetch now runs under an 8-second AbortController timeout. A timed-out or failed request for one ticker is caught and simply skipped (that ticker just doesn't get an early-reversal signal), instead of blocking the other 5 requests in its batch and every batch after it.\n- Verified by directly simulating the exact stall scenario: a batch containing one request that never resolves on its own now completes in bounded time instead of hanging indefinitely, with the other 5 tickers in the batch completing normally.\n- Note: safeTickerFetchJson (used by several other endpoints — flow, options, strat, institutional-context) has retry logic for HTTP error codes but also has no request timeout — the same class of stall is theoretically possible there too. Not touched in this fix since it's a broader, shared helper across several call sites that would want separate, careful testing; worth a dedicated follow-up.\n\n'''
if not rs.startswith('v26.7 — FIX LAYER 2.5 SCAN STALL'):
    r.write_text(entry+rs)
