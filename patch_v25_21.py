from pathlib import Path

p=Path('app.py')
s=p.read_text()

# Reduce calendar fan-out on the 512 MB web instance.
s=s.replace('with ThreadPoolExecutor(max_workers=6) as ex:\n        futures = {ex.submit(public_day, d): d for d in days}',
            'with ThreadPoolExecutor(max_workers=2) as ex:\n        futures = {ex.submit(public_day, d): d for d in days}')

start=s.index('@app.get("/api/postearnings-opportunities")')
end=s.index('@app.get("/api/postearnings-option/<ticker>")', start)
new=r'''@app.get("/api/postearnings-opportunities")
def api_postearnings_opportunities():
    """Memory-bounded market-wide post-earnings scanner.

    V25.21: discovery, price work, and historical enrichment are deliberately
    staged. Large intermediate price frames are released before 4-year ticker
    histories are loaded. Options remain deferred to per-row hydration.
    """
    import gc
    try:
        recent_days=max(3,min(10,int(request.args.get("days","5"))))
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

        recent_map,diag=discover_recent_earnings(tickers,recent_days)
        reporters=[s for s in tickers if s in recent_map]
        now=pd.Timestamp.now().normalize()
        if not reporters:
            return jsonify({"ok":True,"results":[],"universe":len(tickers),
                            "recent_reporters":0,"diagnostics":diag})

        # Six months is ample for the 8/8 RRG windows and dramatically smaller
        # than the former 18-month all-reporter frame. One frame also supplies
        # current post-earnings movement, so no duplicate ticker downloads occur.
        prices=dl_prices(["SPY"]+reporters,"6mo")
        rrg={r["ticker"]:r for r in dual_rrg_rows(prices,"SPY",reporters,8,8)}

        def current_from_frame(sym,event_date):
            try:
                if sym not in prices.columns:return {}
                ss=prices[sym].dropna()
                if len(ss)<2:return {}
                d=pd.Timestamp(event_date).normalize()
                before=ss[ss.index.normalize()<d]; after=ss[ss.index.normalize()>=d]
                if before.empty or after.empty:return {}
                base=float(before.iloc[-1]); last=float(after.iloc[-1]); day1=float(after.iloc[0])
                return {"current_move_pct":round((last/base-1)*100,2),
                        "day1_move_pct":round((day1/base-1)*100,2),
                        "sessions_since":int(len(after))}
            except Exception:return {}

        # Cheap stock-only rank. Historical 4-year profiles are intentionally
        # postponed until the large multi-ticker price frame is gone.
        prelim=[]
        for sym in reporters:
            meta=recent_map[sym]; d=pd.Timestamp(meta["date"]).normalize(); rot=rrg.get(sym,{})
            f=rot.get("fast") or {}; tr=rot.get("trend") or {}; cur=current_from_frame(sym,d)
            move=abs(float(cur.get("current_move_pct") or 0))
            f_in=((f.get("tail_trajectory")=="Rotating In") if f.get("tail_trajectory") else (f.get("rs_up") is True and f.get("mom_up") is True))
            t_in=((tr.get("tail_trajectory")=="Rotating In") if tr.get("tail_trajectory") else (tr.get("rs_up") is True and tr.get("mom_up") is True))
            pre=move*2.2+(12 if f_in else 0)+(8 if t_in else 0)+(5 if f.get("quadrant") in ("Leading","Improving") else 0)
            prelim.append((pre,sym,d,rot,cur))
        prelim.sort(reverse=True,key=lambda x:x[0])

        # Critical memory boundary: each candidate tuple already owns the small
        # RRG/current dicts it needs. Release the large dataframe/dicts before
        # any 4-year history is requested.
        del prices, rrg, reporters
        gc.collect()

        def enrich(item):
            pre,sym,d,rot,cur=item
            cache_key=f"peprofile-v25-21:{sym}:{d.strftime('%Y-%m-%d')}"
            def build_profile():
                dates=merged_historical_earnings_dates(sym,d.strftime("%Y-%m-%d"))
                return earnings_profile(sym,dates)
            profile=cached(cache_key,build_profile,ttl=3600)
            if not profile:return None
            hist_score=historical_continuation_score(profile)
            f=rot.get("fast") or {}; tr=rot.get("trend") or {}
            f_in=((f.get("tail_trajectory")=="Rotating In") if f.get("tail_trajectory") else (f.get("rs_up") is True and f.get("mom_up") is True))
            t_in=((tr.get("tail_trajectory")=="Rotating In") if tr.get("tail_trajectory") else (tr.get("rs_up") is True and tr.get("mom_up") is True))
            rot_score=(12 if f_in else 0)+(8 if t_in else 0)+(5 if f.get("quadrant") in ("Leading","Improving") else 0)
            move=float(cur.get("current_move_pct") or 0)
            current_score=min(25,abs(move)*2.2)+(4 if profile.get("behavior")=="CONTINUATION" else 0)
            expected=max(float(profile.get("median_exc10") or 0),float(profile.get("median_exc14") or 0))
            expected_window=14 if profile.get("has_exc14_data") else 10
            sessions_since=cur.get("sessions_since")
            window_progress_pct=round(min(150.0,100.0*sessions_since/expected_window),1) if sessions_since else None
            if window_progress_pct is not None and window_progress_pct>=100: current_score-=6
            day1_move=cur.get("day1_move_pct"); round_trip=False; retained_pct=None
            if day1_move is not None and abs(day1_move)>=1.0 and sessions_since and sessions_since>1:
                retained_pct=round(100.0*move/day1_move,1)
                if (move*day1_move)<0 or retained_pct<=15:
                    round_trip=True; current_score-=15
            meta=recent_map.get(sym) or {}; surprise_pct=None
            est=_safe_float(meta.get("eps_estimate")); act=_safe_float(meta.get("eps_actual"))
            if est not in (None,0) and act is not None:
                surprise_pct=round((act-est)/abs(est)*100,1)
                react_move=day1_move if day1_move is not None else move
                aligned=(surprise_pct>0 and react_move>=0) or (surprise_pct<0 and react_move<0)
                if aligned: current_score+=min(8.0,abs(surprise_pct)*0.15)
            total=max(0.0,min(100.0,.48*hist_score+current_score+rot_score))
            return {"ticker":sym,"name":all_holdings[sym].get("name"),
                    "earnings_date":d.strftime("%Y-%m-%d"),"calendar_days_ago":max(0,(now-d).days),
                    "parents":parent_map.get(sym,[]),"profile":profile,"historical_score":round(hist_score,1),
                    "current":cur,"rotation":rot,"best_contract":None,"options_execution":"Loading…","options_loading":True,
                    "expected_continuation_pct":round(expected,2),"direction":"bullish" if move>=0 else "bearish",
                    "opportunity_score":round(float(total),1),"eps_surprise_pct":surprise_pct,
                    "drift_window_sessions":expected_window,"drift_window_progress_pct":window_progress_pct,
                    "retained_pct_of_day1_move":retained_pct,"round_trip":round_trip}

        # Sequential enrichment is intentional on the 512 MB plan. Four concurrent
        # 4-year histories were the main transient-memory multiplier behind the 502.
        rows=[]; failures=[]
        for item in prelim[:8]:
            try:
                x=enrich(item)
                if x: rows.append(x)
            except Exception as e:
                failures.append({"ticker":item[1],"error":str(e)[:180]})
            finally:
                gc.collect()
        rows.sort(key=lambda x:-x.get("opportunity_score",0))
        return jsonify({"ok":True,"results":rows[:8],"universe":len(tickers),
                        "recent_reporters":diag.get("found",0),"recent_days":recent_days,
                        "diagnostics":diag,"holdings_sources":sorted(sources),"options_deferred":True,
                        "memory_bounded":True,"enrichment_failures":failures})
    except Exception as e:
        gc.collect()
        return jsonify({"ok":False,"error":str(e)}),500


'''
s=s[:start]+new+s[end:]

# Expose patch version in health without disturbing existing version semantics.
s=s.replace('"persistent_setup_history": bool(DATABASE_URL),\n    })',
            '"persistent_setup_history": bool(DATABASE_URL),\n        "postearnings_engine": "v25.21-memory-bounded",\n    })',1)

p.write_text(s)
print('Applied v25.21 post-earnings memory hardening')
