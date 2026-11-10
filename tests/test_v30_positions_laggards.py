import numpy as np
import pandas as pd

import app as appmod


def _iso(days):
    return (pd.Timestamp.now().normalize() + pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def test_occ_symbol_format():
    assert appmod.occ_symbol("LEN", "2026-11-27", "call", 86) == "LEN261127C00086000"
    assert appmod.occ_symbol("cost", "2026-12-24", "put", 1062.5) == "COST261224P01062500"


def _single(fill=2.40, opened=None, tp1_done=False, direction="bullish"):
    return {"kind": "single", "direction": direction, "opened_at": opened or pd.Timestamp.now().strftime("%Y-%m-%d"),
            "legs": [{"symbol": "X", "type": "call" if direction == "bullish" else "put", "strike": 86, "expiration": _iso(40), "qty": 1, "fill": fill}],
            "plan": {"exit_plan": {"tp1_premium": 3.6, "tp2_premium": 9.8, "premium_stop": 1.2, "stock_stop": 78.5, "tp2_stock": 95.2,
                                   "time_stop_sessions": 6, "time_stop_min_gain_pct": 20, "exit_by": _iso(26)}},
            "state": {"tp1_done": tp1_done}}


def test_single_position_alerts():
    hold = appmod.evaluate_position(_single(), {"X": 2.6}, 85.0)
    assert hold["status"] == "HOLD" and hold["pnl_pct"] > 0
    tp1 = appmod.evaluate_position(_single(), {"X": 3.7}, 88.0)
    assert tp1["status"] == "EXIT" and any("TP1" in a["text"] for a in tp1["alerts"])
    stop = appmod.evaluate_position(_single(), {"X": 2.0}, 78.0)
    assert any("through the stop" in a["text"] for a in stop["alerts"])
    be = appmod.evaluate_position(_single(tp1_done=True), {"X": 2.3}, 85.0)
    assert any("Breakeven stop" in a["text"] for a in be["alerts"])
    old = appmod.evaluate_position(_single(opened=(pd.Timestamp.now() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")), {"X": 2.5}, 85.0)
    assert any("Time stop" in a["text"] for a in old["alerts"])


def test_strangle_rules():
    pos = {"kind": "strangle", "direction": "neutral", "opened_at": pd.Timestamp.now().strftime("%Y-%m-%d"),
           "legs": [{"symbol": "C", "type": "call", "strike": 100, "expiration": _iso(20), "qty": 1, "fill": 2.0},
                    {"symbol": "P", "type": "put", "strike": 90, "expiration": _iso(20), "qty": 1, "fill": 2.0}],
           "plan": {"exit_plan": appmod._default_exit_plan("strangle", [{"expiration": _iso(20)}], 4.0)}, "state": {}}
    win = appmod.evaluate_position(pos, {"C": 5.6, "P": 0.08}, 104.0)          # call +180%
    assert win["status"] == "EXIT" and any("take-profit zone" in a["text"] for a in win["alerts"])
    assert any("under $0.10" in a["text"] for a in win["alerts"])
    assert not any(a["text"].startswith("Stop") for a in win["alerts"])
    top = appmod.evaluate_position(pos, {"C": 6.2, "P": 0.05}, 106.0)          # call +210%
    assert any("close it" in a["text"] for a in top["alerts"])
    near = appmod.evaluate_position(pos, {"C": 4.2, "P": 0.9}, 102.0)          # call +110%
    assert near["status"] == "WATCH" and any("approaching" in a["text"] for a in near["alerts"])
    loss = appmod.evaluate_position(pos, {"C": 1.2, "P": 1.3}, 95.0)
    assert any(a["text"].startswith("Stop") for a in loss["alerts"])


def test_position_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "DATABASE_URL", "")
    monkeypatch.setattr(appmod, "SETUP_DB_PATH", str(tmp_path / "p.sqlite3"))
    out = appmod.open_position({"ticker": "LEN", "kind": "single", "legs": [{"type": "call", "strike": 86, "expiration": _iso(30), "fill": 2.5, "qty": 1}],
                                "plan": {"modeled_entry_premium": 2.43, "exit_plan": {"tp1_premium": 3.65, "tp2_premium": 9.8, "premium_stop": 1.22, "exit_by": _iso(16)}}})
    assert out["legs"][0]["symbol"].startswith("LEN") and out["exit_plan"]["tp1_premium"] > 3.65  # rescaled to the real fill
    s = appmod.open_position({"ticker": "NVDA", "kind": "strangle", "legs": [{"type": "call", "strike": 200, "expiration": _iso(10), "fill": 3},
                                                                            {"type": "put", "strike": 180, "expiration": _iso(10), "fill": 2.5}]})
    assert s["exit_plan"]["kind"] == "strangle"
    opened = appmod.list_positions("open")
    assert len(opened) == 2
    info = appmod.close_position(opened[0]["id"], {"exit_premium": 8.25})
    assert info["pnl_pct"] == 50.0
    assert appmod.positions_payload_summary()["closed"] == 1


def test_laggard_metrics_detects_catch_up_gap():
    idx = pd.bdate_range("2026-05-01", periods=120)
    rng = np.random.default_rng(3)
    etf_r = rng.normal(.002, .01, 120)
    stock_r = etf_r * 1.1 + rng.normal(0, .004, 120)
    stock_r[-20:-5] -= .006  # lagged the group for three weeks
    stock_r[-5:] += .004     # now turning up
    etf = pd.Series(100 * np.exp(np.cumsum(etf_r)), index=idx)
    stock = pd.Series(50 * np.exp(np.cumsum(stock_r)), index=idx)
    m = appmod.laggard_metrics(stock, etf)
    assert m["corr"] > .6 and m["catch_up_gap"] < -3 and m["rs_turn_5d"] > 0


def test_option_outcome_uses_actual_bars():
    bars = [{"t": "2026-11-09T05:00:00Z", "c": 2.0}, {"t": "2026-11-10T05:00:00Z", "c": 2.6}, {"t": "2026-11-11T05:00:00Z", "c": 3.4},
            {"t": "2026-11-12T05:00:00Z", "c": 3.1}]
    o = appmod.option_outcome("X", {"filled": "2026-11-09", "closed": "2026-11-11"}, bars)
    assert o["option_entry"] == 2.0 and o["option_exit"] == 3.4 and o["option_return_pct"] == 70.0


def test_nightly_guard_is_per_close(monkeypatch):
    c = appmod.app.test_client()
    started = []
    monkeypatch.setattr(appmod.threading, "Thread", lambda target=None, **k: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(appmod, "last_market_close_ts", lambda now=None: 1_000_000.0)
    monkeypatch.setattr(appmod, "_kv_get", lambda k, d=None: d)
    appmod.NIGHTLY.update(running=False, started=1_000_000.0 - 3600)   # ran before the close, 1h earlier
    monkeypatch.setattr(appmod.time, "time", lambda: 1_000_000.0 + 3600)
    assert c.post("/api/nightly/run").get_json()["started"] is True
    appmod.NIGHTLY.update(running=False, started=1_000_000.0 + 60)       # already ran after the close
    assert c.post("/api/nightly/run").get_json()["started"] is False
    appmod.NIGHTLY.update(running=False, started=None)


def test_outperformer_with_spiky_beta_is_not_a_laggard():
    idx = pd.bdate_range("2026-05-01", periods=120)
    rng = np.random.default_rng(5)
    etf_r = rng.normal(.0015, .006, 120)
    stock_r = etf_r * 5 + rng.normal(0, .01, 120)   # very high beta
    etf = pd.Series(100 * np.exp(np.cumsum(etf_r)), index=idx)
    stock = pd.Series(50 * np.exp(np.cumsum(stock_r)), index=idx)
    m = appmod.laggard_metrics(stock, etf)
    assert m["beta"] <= 2.0 and m["raw_beta"] > 2.0
    # if it beat the group, it must not pass the laggard screen
    if m["ret20"] >= m["group_ret20"]:
        assert not (m["ret20"] < m["group_ret20"] and m["rs_lag_20d"] < 0)


def test_fill_latest_closes_patches_missing_newest_session():
    idx = pd.bdate_range("2026-10-26", periods=10)
    close = pd.DataFrame({"SPY": list(range(700, 710)), "^VIX": list(range(20, 30)), "HYG": [80.0] * 10}, index=idx, dtype=float)
    close.iloc[-1, 0] = np.nan  # SPY missing today
    close.iloc[-1, 2] = np.nan  # HYG missing today
    alp = pd.DataFrame({"SPY": [708.0, 731.5], "HYG": [80.0, 79.2]}, index=idx[-2:])
    out = appmod.fill_latest_closes(close, ["SPY", "^VIX", "HYG"], fetch=lambda syms: alp[[s for s in syms if s in alp.columns]])
    assert out["SPY"].iloc[-1] == 731.5 and out["HYG"].iloc[-1] == 79.2 and out["^VIX"].iloc[-1] == 29


def test_fill_latest_closes_adds_newer_session():
    idx = pd.bdate_range("2026-10-26", periods=5)
    close = pd.DataFrame({"SPY": [700.0, 701, 702, 703, 704]}, index=idx)
    nxt = idx[-1] + pd.offsets.BDay(1)
    alp = pd.DataFrame({"SPY": [704.0, 699.0]}, index=[idx[-1], nxt])
    out = appmod.fill_latest_closes(close, ["SPY"], fetch=lambda syms: alp)
    assert out.index.max() == nxt and out["SPY"].iloc[-1] == 699.0


def test_nightly_thread_never_reads_l2(monkeypatch):
    import threading
    monkeypatch.setattr(appmod, "L2_PREFIXES", ("market",))
    monkeypatch.setattr(appmod, "market_open_now", lambda now=None: False)
    monkeypatch.setattr(appmod, "_l2_preload", lambda: None)
    appmod.L2_MEMORY["market"] = (9e12, {"x": 1})
    seen = []
    t = threading.Thread(target=lambda: seen.append(appmod.l2_get("market")), name="nightly")
    t.start(); t.join()
    assert seen == [None] and appmod.l2_get("market")[1] == {"x": 1}


def test_iv_rank_and_juiced_logic():
    assert appmod.iv_rank_from_history([30] * 10, 40) == (None, None)
    hist = list(np.linspace(25, 45, 30))
    rank, pct = appmod.iv_rank_from_history(hist, 43)
    assert rank > 85 and pct > 85
    rows = [{"iv": 60.0, "moneyness_pct": 1.0, "dte": 30}, {"iv": 62.0, "moneyness_pct": -2.0, "dte": 45}, {"iv": 200.0, "moneyness_pct": 30, "dte": 30}]
    assert appmod.atm_iv_from_rows(rows, 100) == 61.0
    ctx = appmod.iv_context("ZZZZ", rows, 100, record=False)
    assert ctx["iv_vs_rv1y"] and ctx["juiced"] is True   # 61% vs stubbed 30% realized


def test_weekly_contract_used_when_budget_tight_and_exit_rules_tighten():
    import sys
    sys.path.insert(0, "tests")
    from test_v28_entry_quality import _row, _structure
    spot = 91.58
    st = _structure(spot, 3.55, 82.08)
    st["pullback"]["entry"] = spot; st["pullback"]["in_zone"] = True   # enter now, short hold
    st["pullback"]["target"] = spot + 6
    rows = [_row("call", k, d, spot) for k in range(86, 104) for d in (12, 42)]
    out = appmod.option_entry_plan("LEN", "bullish", 110, None, st, rows, spot, 3.55, 0.30)
    z = out["single"]
    assert z and z["cost"] <= 110
    if z["weekly"]:
        assert "weekly" in out["exit_plan"]["rules"][-1] and out["exit_plan"]["time_stop_sessions"] <= z["leg"]["dte"]
    # no expiration may end before the planned hold
    assert z["leg"]["dte"] >= out["hold_days_assumed"] + 3


def test_strangle_plan_picks_budget_pair_and_moves():
    import sys
    sys.path.insert(0, "tests")
    from test_v28_entry_quality import _row
    spot = 100.0
    rows = []
    for dte, exp in ((7, "2026-12-01"), (14, "2026-12-08"), (30, "2026-12-24")):
        legs = [_row("call", k, dte, spot, iv=.6) for k in range(100, 131)] + [_row("put", k, dte, spot, iv=.6) for k in range(70, 101)]
        for r in legs: r["expiration"] = exp
        rows += legs
    out = appmod.strangle_plan("TEST", 400, rows, spot, history=(None, []), vp={"available": False})
    assert out["available"] and out["fits_budget"] and out["cost"] <= 400
    assert {c["tenor"] for c in out["choices"]} == {"1 week", "2 week"}
    assert out["tenor"] == "1 week" and out["dte"] == 7          # 30 DTE never offered
    assert out["call"]["strike"] > spot > out["put"]["strike"]
    assert out["call_move_for_150"]["move_pct"] > 0 > out["put_move_for_150"]["move_pct"]
    assert out["time_stop_sessions"] == 2 and out["exit_by"] == "2026-11-30"
    two = next(c for c in out["choices"] if c["tenor"] == "2 week")
    assert two["time_stop_sessions"] == 3 and two["exit_by"] == "2026-12-06"
    tight = appmod.strangle_plan("TEST", 50, rows, spot, history=(None, []), vp={"available": False})
    assert tight["available"] and not tight["fits_budget"] and "over the" in tight["note"]


def test_weekly_strangle_exit_plan_not_immediate():
    exp = (pd.Timestamp.now().normalize() + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    ep = appmod._default_exit_plan("strangle", [{"expiration": exp}], 3.0)
    assert ep["time_stop_sessions"] == 2
    assert ep["exit_by"] == (pd.Timestamp(exp) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def test_strangle_pair_balances_premium():
    import sys
    sys.path.insert(0, "tests")
    from test_v28_entry_quality import _row
    spot = 100.0
    # put skew: puts priced at higher IV, so equal-delta legs would be uneven
    rows = [_row("call", k, 7, spot, iv=.5) for k in range(100, 121)] + [_row("put", k, 7, spot, iv=.8) for k in range(75, 101)]
    for r in rows: r["expiration"] = "2026-12-01"
    out = appmod.strangle_plan("TEST", 400, rows, spot, history=(None, []), vp={"available": False})
    b = out["balance"]
    assert b["balanced"] and b["imbalance_pct"] <= 10
    assert 90 <= b["call"]["breakeven_gain_pct"] <= 125 and 90 <= b["put"]["breakeven_gain_pct"] <= 125
    assert b["call"]["pl_at_150"] > 0 and b["put"]["pl_at_150"] > 0


def _hist_df():
    idx = pd.bdate_range(end="2026-10-30", periods=400)
    c = np.full(len(idx), 100.0)
    df = pd.DataFrame({"Open": c, "High": c + .5, "Low": c - .5, "Close": c, "Volume": 1e6}, index=idx)
    dates = []
    for k in range(60, 400, 63):              # quarterly "reports"
        d = idx[k]; dates.append(d)
        # day 1 reaction, then a big follow-through in the next two sessions on most events
        big = (k // 63) % 4 != 0
        df.iloc[k + 1, df.columns.get_loc("High")] = 100 * (1.10 if big else 1.01)
        df.iloc[k + 2, df.columns.get_loc("Low")] = 100 * (.99)
    return df, dates


def test_post_reaction_moves_and_odds():
    df, dates = _hist_df()
    mv = appmod.post_reaction_moves(df, dates, 2)
    assert len(mv) >= 4 and any(m["up_pct"] >= 9 for m in mv)
    choice = {"hold_days_assumed": 2, "call_move_for_150": {"move_pct": 5.0, "stock_price": 105.0},
              "put_move_for_150": {"move_pct": -8.0, "stock_price": 92.0}}
    vp = {"available": True, "poc": 100.0, "vah": 101.0, "val": 99.5, "hvn": [103.0], "lvn": [96.0], "prior_week": None}
    o = appmod.strangle_odds(choice, 100.0, mv, vp)
    assert o["hits"] is not None and o["hits"] >= 3
    assert o["location"] == "inside value"
    assert o["call_path"]["clear"] is False and o["call_path"]["walls"] == 2
    assert o["put_path"]["clear"] is True and o["put_path"]["air"] is True
    assert o["label"] in ("High", "Medium", "Low") and any("reports" in n for n in o["notes"])


def test_post_reaction_moves_dedupes_same_report():
    df, dates = _hist_df()
    dup = dates + [d + pd.Timedelta(hours=16) for d in dates] + [d + pd.Timedelta(days=1) for d in dates]
    assert len(appmod.post_reaction_moves(df, dup, 2)) == len(appmod.post_reaction_moves(df, dates, 2))


def test_date_only_after_close_maps_to_reaction_session():
    idx = pd.bdate_range("2026-01-05", periods=30)
    c = np.full(30, 100.0); c[11:] = 110.0      # gap on session 11 (report after close on session 10)
    df = pd.DataFrame({"Open": c, "High": c + .5, "Low": c - .5, "Close": c, "Volume": 1e6}, index=idx)
    pos = appmod.event_session_index(df, idx[10])  # date-only, the report day
    assert pos == 11
    assert appmod.event_session_index(df, idx[11]) == 11


def test_participation_liquidity_overrides_wide_after_hours_spreads():
    rows = []
    for k, (oi, vol, spr) in enumerate([(10813, 324, 14.7), (5145, 81, 17.6), (1683, 119, 27.5), (1002, 99, 29.8), (922, 32, 16.7), (2301, 10, 25.0)]):
        rows.append({"symbol": f"S{k}", "type": "call", "strike": 60 + k, "mid": 1.0, "dte": 11, "iv": 45, "delta": .4,
                     "moneyness_pct": k * 1.0, "open_interest": oi, "volume": vol, "spread_pct": spr,
                     "liquidity": "Tradable" if spr <= 15 and oi >= 100 and vol >= 25 else "Thin"})
    out = appmod._summarize_option_rows("PYPL", 60.0, rows, .3)
    assert out["liquidity"] == "Tradable" and out["liquidity_basis"] == "participation"
