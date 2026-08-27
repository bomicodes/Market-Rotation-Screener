from pathlib import Path

p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "25.25"','APP_VERSION = "25.26"',1)
s=s.replace('.institutionalScore{font-size:17px;font-weight:900;color:#93c5fd}.instRadarHead{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.instRadarHead .status{margin-left:auto}.instPrint{font-weight:800;color:#e2e8f0}.instHot{color:#86efac}.instWarm{color:#fde68a}.instMuted{color:#94a3b8}', '.institutionalScore{font-size:17px;font-weight:900;color:#93c5fd}.instRadarHead{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.instRadarHead .status{margin-left:auto}.instPrint{font-weight:800;color:#e2e8f0}.instHot{color:#86efac}.instWarm{color:#fde68a}.instMuted{color:#94a3b8}\n.instContextRow td{border-top:none;padding-top:0}\n.darkPoolContext{color:#7f97a8;line-height:1.5}',1)
needle='\ndef _institutional_trade_sample(ticker):\n'
insert='''\ndef dark_pool_spike_context(ticker, spike_date_str):
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
'''
assert needle in s
s=s.replace(needle,insert+needle,1)
old='''    activity=min(10.0,2.5+min(3.5,max(0,multiple-1)*2.0)+min(2.0,repeated*.55)+min(2.0,large_notional/max(1,baseline)*.55))
    return {"ticker":ticker,"ok":True,"largest_print":round(cur["largest"],2),"baseline_largest":round(baseline,2),
            "largest_multiple":round(multiple,2),"large_print_notional":round(large_notional,2),"repeat_days":repeated,
'''
new='''    activity=min(10.0,2.5+min(3.5,max(0,multiple-1)*2.0)+min(2.0,repeated*.55)+min(2.0,large_notional/max(1,baseline)*.55))
    context=None
    if multiple>=2.0:
        try:
            context=dark_pool_spike_context(ticker,cur["date"])
        except Exception:
            context=None
    return {"ticker":ticker,"ok":True,"date":cur["date"],"largest_print":round(cur["largest"],2),"baseline_largest":round(baseline,2),
            "context":context,
            "largest_multiple":round(multiple,2),"large_print_notional":round(large_notional,2),"repeat_days":repeated,
'''
assert old in s
s=s.replace(old,new,1)
oldjs='''   return `<tr class="clickrow" data-inst-open="${x.ticker}"><td>${i+1}</td><td><b>${x.ticker}</b><div class="tiny">${m.etf||""}</div></td><td><span class="institutionalScore">${Number(x.composite_score||0).toFixed(1)}/10</span><div class="tiny">activity ${Number(x.activity_score||0).toFixed(1)}/10</div></td><td><span class="instPrint ${cls}">${instMoney(x.largest_print)}</span><div class="tiny">${mult.toFixed(1)}× prior sampled largest · ${x.sampled_trades||0} trades sampled</div></td><td>${x.repeat_days||0}/4 sessions<div class="tiny">large-print persistence</div></td><td>${m.stage||0}/4 · ${m.quadrant||"—"}<div class="tiny">${m.tail||"—"} · opportunity ${m.opportunity||0}/10</div></td><td>${why.join(" · ")||"Large-print activity under review"}<div class="tiny">Click to open chart/options</div></td></tr>`;
'''
newjs='''   const mainRow=`<tr class="clickrow" data-inst-open="${x.ticker}"><td>${i+1}</td><td><b>${x.ticker}</b><div class="tiny">${m.etf||""}</div></td><td><span class="institutionalScore">${Number(x.composite_score||0).toFixed(1)}/10</span><div class="tiny">activity ${Number(x.activity_score||0).toFixed(1)}/10</div></td><td><span class="instPrint ${cls}">${instMoney(x.largest_print)}</span><div class="tiny">${mult.toFixed(1)}× prior sampled largest · ${x.sampled_trades||0} trades sampled</div></td><td>${x.repeat_days||0}/4 sessions<div class="tiny">large-print persistence</div></td><td>${m.stage||0}/4 · ${m.quadrant||"—"}<div class="tiny">${m.tail||"—"} · opportunity ${m.opportunity||0}/10</div></td><td>${why.join(" · ")||"Large-print activity under review"}<div class="tiny">Click to open chart/options</div></td></tr>`;
   // Deterministic (not AI-generated) context for genuine spikes only.
   const contextRow=(x.context&&x.context.length)?`<tr class="instContextRow"><td></td><td colspan="6"><div class="tiny darkPoolContext">${x.context.map(n=>`<div>· ${n}</div>`).join("")}</div></td></tr>`:"";
   return mainRow+contextRow;
'''
assert oldjs in s
s=s.replace(oldjs,newjs,1)
p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
entry='''v25.26 — DETERMINISTIC CONTEXT FOR INSTITUTIONAL ACTIVITY TOP SETUPS
- Rather than shipping a second, standalone "Dark Pool Prints" panel that would have overlapped with the already-built, more deeply-integrated "Institutional Activity Top Setups" scanner (multi-ticker, blended into rotation scoring), grafted the deterministic (non-AI) context piece onto that existing feature instead.
- Added dark_pool_spike_context(): reuses the app's own earnings-calendar merge and macro calendar to report "Xd after/before known earnings" and "nearby macro events" for a flagged large-print day — zero LLM calls, zero web search, zero new ongoing cost. Wired into _institutional_trade_sample() so context is only computed for genuine spikes (largest_multiple >= 2.0), not every row.
- Frontend: a compact context sub-row now appears beneath any flagged ticker's row in the Institutional Activity table when context exists.
- NOTE: versions between 25.22 and 25.25 are not yet documented here despite shipping real changes; worth a dedicated backfill pass.

'''
if not rs.startswith('v25.26'):
    r.write_text(entry+rs)
