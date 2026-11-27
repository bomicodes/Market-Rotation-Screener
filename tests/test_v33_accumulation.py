import numpy as np
import pandas as pd

import app as appmod


def _frame(closes, highs, lows, vols):
    idx = pd.bdate_range(end="2026-11-06", periods=len(closes))
    opens = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)


def _base(bull=True, n_trend=130, n_base=30, seed=1):
    rng = np.random.default_rng(seed)
    # trend into the base, then a tight range whose volume leans one way
    t = (60 if bull else 140) + np.cumsum(rng.normal(.35 if bull else -.35, 1.0, n_trend))
    lvl = t[-1]
    c, h, l, v = list(t), list(t + 1.2), list(t - 1.2), list(rng.uniform(1e6, 1.4e6, n_trend))
    for i in range(n_base):
        wave = 1.6 * np.sin(i / 2.2)
        drift = (i / n_base) * (.9 if bull else -.9)
        px = lvl + wave + drift
        up = px > c[-1]
        # up days close near their high, down days near their low
        c.append(px); h.append(px + (.15 if up else .85)); l.append(px - (.85 if up else .15))
        heavy = (up and bull) or ((not up) and not bull)
        v.append((1.6e6 if heavy else .7e6) * (1 + i / n_base * .5))
    # last bar: an inside (1) bar near the edge the volume favours
    edge = max(h[-n_base:]) - .6 if bull else min(l[-n_base:]) + .6
    c.append(edge); h.append(min(h[-1], edge + .25)); l.append(max(l[-1], edge - .25)); v.append(1.8e6)
    if h[-1] < c[-1]: h[-1] = c[-1]
    if l[-1] > c[-1]: l[-1] = c[-1]
    return _frame(np.array(c), np.array(h), np.array(l), np.array(v))


def test_accumulation_base_detected_with_plan():
    b = appmod.base_detect(_base(True))
    assert b and b["available"]
    assert b["bias"] == "accumulation" and b["direction"] == "bullish"
    assert 15 <= b["base_days"] <= 60 and b["height_atr"] <= 5.0
    assert b["up_down_vol"] > 1 and b["ad"] > 0
    p = b["plan"]
    assert p and p["stop"] < p["entry"] < p["target"]
    assert p["entry"] <= b["base_high"] + .01
    assert "volume" in p["confirm"].lower()


def test_distribution_base_detected_with_short_plan():
    b = appmod.base_detect(_base(False, seed=3))
    assert b and b["bias"] == "distribution" and b["direction"] == "bearish"
    p = b["plan"]
    assert p and p["target"] < p["entry"] < p["stop"]


def test_trending_stock_is_not_a_base():
    rng = np.random.default_rng(7)
    c = 50 + np.cumsum(rng.normal(.8, .6, 200))
    df = _frame(c, c + .8, c - .8, np.full(200, 1e6))
    assert appmod.base_detect(df) is None


def test_base_plan_feeds_entry_levels():
    s = {"available": True, "spot": 100, "base_plans": {"bullish": {"entry": 101, "stop": 97, "target": 110, "rr": 2.25}}}
    lv = appmod._entry_levels_for(s, "bullish", "base")
    assert lv["plan"] == "base" and lv["entry"] == 101 and lv["target"] == 110


def test_group_volume_rotation_and_alignment():
    import sys
    sys.path.insert(0, "tests")
    acc = {f"A{i}": _base(True, seed=i) for i in range(6)}
    dist = {f"D{i}": _base(False, seed=10 + i) for i in range(6)}
    data = {**acc, **dist}
    mem = {**{t: ["XLK"] for t in acc}, **{t: ["XLE"] for t in dist}}
    rrg = {"XLK": {"quadrant": "Lagging"}, "XLE": {"quadrant": "Leading"}}
    g = appmod.group_volume_rotation(data, mem, rrg)
    assert g["XLK"]["flow"] >= 15 and g["XLK"]["signal"].startswith("Quiet accumulation")
    assert g["XLE"]["flow"] <= -15 and g["XLE"]["signal"].startswith("Distribution under")
    assert appmod._rotation_alignment("bullish", {"quadrant": "Improving", "flow": 30})[0] == 15
    assert appmod._rotation_alignment("bullish", g["XLK"])[0] == 10
    assert appmod._rotation_alignment("bullish", g["XLE"])[0] == -15
    assert appmod._rotation_alignment("bearish", g["XLE"])[0] == 10


def _climax(test=True, buying=True, seed=5):
    rng = np.random.default_rng(seed)
    c = list(100 + np.cumsum(rng.normal(.05, .8, 100))); v = list(rng.uniform(1e6, 1.2e6, 100))
    for i in range(12):                       # decline into the low
        c.append(c[-1] - 1.2); v.append(1.3e6)
    c.append(c[-1] - 2.5); v.append(4.0e6)   # climax bar
    low_i = len(c) - 1
    for i in range(6):                        # bounce
        c.append(c[-1] + 1.0); v.append(1.8e6 if buying else .5e6)
        if not buying: c[-1] -= .0
    if not buying:
        for i in range(3): c.append(c[-1] - .4); v.append(1.6e6)
    if test:
        for i in range(4):                    # light-volume pullback holding above the low
            c.append(c[-1] - .6); v.append(.6e6)
    c = np.array(c); h = c + .6; l = c - .6
    l[low_i] = c[low_i] - 1.5
    return _frame(c, h, l, np.array(v))


def test_since_low_read_climax_and_test():
    r = appmod.since_low_read(_climax(test=True))
    assert r["climax"] and r["test"] and r["bounce_udr"] >= 1.2
    r2 = appmod.since_low_read(_climax(test=False, buying=False, seed=6))
    assert r2["climax"] and not r2["test"]


def test_group_climax_overrides_distribution():
    data = {f"S{i}": _climax(test=True, seed=20 + i) for i in range(6)}
    mem = {t: ["SMH"] for t in data}
    g = appmod.group_volume_rotation(data, mem, {"SMH": {"quadrant": "Improving"}})["SMH"]
    assert g["flow_20d"] <= -15
    assert g["signal"].startswith("Selling climax") and g["flow"] >= 0
    assert appmod._rotation_alignment("bullish", g)[0] >= 0


def test_scan_caches_are_refreshed_by_nightly():
    src = open(appmod.__file__).read()
    line = src[src.index("L2_PREFIXES=("):src.index(")", src.index("L2_PREFIXES=("))]
    prefixes = [x.strip().strip('"') for x in line.split("(", 1)[1].replace("\n", "").split(",") if x.strip()]
    for k in ("accum-v34-4:250", "group-flow-v34-4"):
        assert any(k.startswith(p) for p in prefixes)


def test_patch_latest_bars_appends_scaled_volume(monkeypatch):
    idx = pd.bdate_range(end="2026-11-06", periods=100)
    df = pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.0, "Volume": 1e6}, index=idx)
    al = {"AAA": {idx[-2].normalize(): (10, 11, 9, 10, 2e4), idx[-1].normalize(): (10, 11, 9, 10, 2e4),
                  pd.Timestamp("2026-11-09"): (10, 12, 10, 11.5, 4e4)}}
    monkeypatch.setattr(appmod, "_alpaca_multi_daily_ohlcv", lambda syms, days=12: al)
    out = appmod._patch_latest_bars({"AAA": df})
    last = out["AAA"].iloc[-1]
    assert out["AAA"].index[-1] == pd.Timestamp("2026-11-09") and last["Close"] == 11.5
    assert abs(last["Volume"] - 2e6) < 1     # 4e4 IEX x (1e6 / 2e4) ratio


def test_reversal_prefilter_finds_failed_2d():
    import numpy as np, pandas as pd
    idx = pd.bdate_range(end="2026-11-11", periods=80)   # a Wednesday
    c = np.linspace(80, 60, 80)
    df = pd.DataFrame({"Open": c + .2, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 2e6}, index=idx)
    # last bar takes out the prior low and closes green above its open
    df.iloc[-1] = [59.0, 60.5, 58.0, 60.4, 3e6]
    out = appmod._rev_prefilter(df, ["XLK"], {"XLK": {"flow": 30}})
    assert out and out["direction"] == "bullish" and any(x["type"] == "Failed 2D" for x in out["signals"])
    assert out["prescore"] >= 4   # daily signal + near the 20d low + group flow aligned


def test_v363_lists_sorted_by_trigger_status():
    src = open(__import__("os").path.join(__import__("os").path.dirname(__file__), "..", "app.py")).read()
    assert "function v363Tier" in src and "function v363SortGrid" in src
    # the sort layer must wrap the renderers before the final wiring
    assert src.index("function v363Tier") < src.index("const _renderTopSetupsV32=renderTopSetups")
    assert "Triggered / in the entry zone" in src


def _flow_row(sym, typ, vol, oi, prem, loc, d="2026-11-13"):
    return {"symbol": sym, "type": typ, "strike": 100.0, "expiration": "2026-12-18", "vol": vol, "oi": oi, "prem": prem, "loc": loc, "bar_date": d}


def test_v364_flow_summary_and_lean():
    import app as A
    rows = [
        _flow_row("X1", "call", 2000, 500, 400000, 0.9),
        _flow_row("X2", "call", 800, 300, 150000, 0.7),
        _flow_row("X3", "put", 300, 5000, 90000, 0.2),     # under OI: not opening-size
        _flow_row("X4", "put", 50, 0, 60000, 0.5),          # under 100 contracts
        _flow_row("X5", "call", 5000, 100, 900000, 0.9, d="2026-11-12"),  # stale bar: ignored
    ]
    s = A.flow_rows_summary(rows, "2026-11-13")
    assert s["new_call_prem"] == 550000 and s["new_put_prem"] == 0
    assert A.flow_lean(s)[0] == "calls"
    assert A.flow_lean({"new_call_prem": 20000, "new_put_prem": 10000})[0] == "quiet"
    # held: yesterday's 1000-contract print, OI grew 900 -> 90% held
    s2 = A.flow_rows_summary(rows, "2026-11-13", prior_top=[{"symbol": "Y", "vol": 1000, "oi": 200}], oi_now={"Y": 1100})
    assert s2["held_ratio"] == 0.9


def test_v364_flow_read_repeat_days_raise_score():
    import app as A
    day = lambda d, c, p: {"ticker": "T", "session": d, "new_call_prem": c, "new_put_prem": p, "bull": c, "bear": p, "held_ratio": None, "top": []}
    one = A.flow_read_from(day("2026-11-13", 500000, 100000), [])
    three = A.flow_read_from(day("2026-11-13", 500000, 100000), [day("2026-11-12", 400000, 50000), day("2026-11-11", 300000, 20000)])
    assert three["score"] > one["score"] and three["bias"] == "bullish"
    assert "calls 3 of the last 3 days" in three["read"]
    q = A.flow_read_from(day("2026-11-13", 1000, 1000), [])
    assert q["lean"] == "quiet" and q["score"] is None and q["bias"] == "neutral"


def test_v364_group_tag():
    import app as A
    assert A.group_tag_for("bullish", 40) == "with"
    assert A.group_tag_for("bullish", -30) == "against"
    assert A.group_tag_for("bearish", -30) == "with"
    assert A.group_tag_for("bullish", 5) == "neutral"
    assert A.group_tag_for("bullish", None) == "unknown"


def test_v364_day1_grace_variant():
    import app as A, pandas as pd
    idx = pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"])
    # day 1 trades through 100 then closes 99.5 (failed 2U), day 2 rips to target
    df = pd.DataFrame({"Open": [98, 99, 99.6, 101], "High": [99, 100.5, 104, 106], "Low": [97, 98.8, 99.2, 100.5], "Close": [98.5, 99.5, 103, 105]}, index=idx)
    row = {"trade_date": "2026-11-10", "direction": "bullish", "entry": 100, "stop": 98, "target": 104, "fill": "stop", "lane": "vp_counter"}
    out = A.evaluate_setup_outcome(row, df)
    assert out.get("exit") == "failed 2U" and out["r"] < 0
    assert out["grace"]["status"] == "win" and out["grace"]["r"] == 2.0


def test_v365_support_dip_variant():
    import app as A, pandas as pd
    idx = pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"])
    row = {"trade_date": "2026-11-10", "direction": "bullish", "entry": 100, "stop": 98, "target": 104, "fill": "stop", "lane": "vp_counter"}
    # dips to 99 (halfway to the stop) on day 1, then runs to the target on day 2
    df = pd.DataFrame({"Open": [99.5, 99.6, 100, 103], "High": [100, 99.9, 104.5, 105], "Low": [99, 98.9, 99.8, 102], "Close": [99.7, 99.5, 104, 104.5]}, index=idx)
    d = A._evaluate_support_dip(row, df)
    assert d["dip_price"] == 99.0 and d["fill_price"] == 99.0
    assert d["status"] == "win" and d["r"] == 2.5          # (104-99)/2 in the original 1R units
    # straight breakout, never dips: not filled once the wait window passes
    df2 = pd.DataFrame({"Open": [99.5, 100.2, 101, 102], "High": [100, 101, 103, 105], "Low": [99.5, 100, 100.8, 101.5], "Close": [99.9, 100.8, 102.8, 104.6]}, index=idx)
    assert A._evaluate_support_dip(row, df2, max_wait=3)["status"] == "not filled"
    # logged VP level used when it sits meaningfully below the trigger
    d3 = A._evaluate_support_dip({**row, "level": 99.4}, df)
    assert d3["dip_price"] == 99.4
    # and evaluate_setup_outcome carries it for trigger plans
    assert "dip" in A.evaluate_setup_outcome(row, df)


def _bars(closes, highs=None, lows=None, opens=None, vol=2_000_000):
    import pandas as pd, numpy as np
    n = len(closes); idx = pd.bdate_range("2026-06-01", periods=n)
    c = np.array(closes, float)
    h = np.array(highs, float) if highs is not None else c * 1.01
    l = np.array(lows, float) if lows is not None else c * 0.99
    o = np.array(opens, float) if opens is not None else np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": np.full(n, vol, float)}, index=idx)


def test_v372_bear_bar_and_levels():
    import app as A
    # failed 2U: takes out yesterday's high, closes back below it, red
    df = _bars([99, 100, 101], highs=[100, 102, 103], lows=[98, 99, 99.5], opens=[99, 100, 102.5])
    df.iloc[-1, df.columns.get_loc("Close")] = 101.0
    assert A._bear_bar(df) == "Failed 2U"
    # levels: stop forced to >= 1 ATR, target the first support >= 1.5R away
    lv = A._ps_levels(100, 100.5, [97.5, 99.6], 1.0)
    assert lv["stop"] == 101 and lv["target"] == 97.5 and lv["rr"] == 2.5
    # a support in the way of a 1.5R target -> no plan
    assert A._ps_levels(100, 101, [99.2], 1.0) is None
    # no support below -> 2R
    assert A._ps_levels(100, 101, [], 1.0)["target"] == 98


def test_v372_catch_down_candidate():
    import app as A, numpy as np
    n = 120
    g = list(np.linspace(100, 100, n - 20)) + list(np.linspace(100, 92, 20))           # group falls 8%
    t = list(np.linspace(50, 50, n - 20)) + list(np.linspace(50, 51, 19)) + [50.2]      # stock holds up, then cracks
    # make the stock track the group day-to-day (correlation) with a small offset
    rg = np.diff(np.log(g)); t = [50.0]
    for i, r in enumerate(rg):
        t.append(t[-1] * np.exp(r * 0.4 + (0.004 if i < n - 3 else -0.012)))
    data = {"GRP": _bars(g), "AAA": _bars(t, vol=1_000_000)}
    flows = {"GRP": {"flow": -40, "quadrant": "Lagging", "signal": "Rotating out · volume confirms"}}
    cands, outg = A.put_side_candidates(data, {"AAA": ["GRP"]}, flows)
    assert outg == ["GRP"]
    lanes = {c["lane"] for c in cands if c["ticker"] == "AAA"}
    assert "catch_down" in lanes
    cd = next(c for c in cands if c["lane"] == "catch_down")
    lv = cd["levels"]; assert lv["stop"] > lv["entry"] > lv["target"] and lv["rr"] >= 1.5


def test_v373_trigger_plan_close_to_trigger_is_not_buy_now():
    import app as A
    st = {"available": True, "spot": 43.05, "atr14": 1.0,
          "put_plans": {"bearish": {"name": "failed_bounce", "entry": 43.01, "stop": 44.78, "target": 39.57, "rr": 1.95}}}
    rows = []
    for k in (40, 41, 42, 42.5, 43, 44):
        rows.append({"symbol": f"BSX261218P000{int(k*1000):05d}", "type": "put", "strike": float(k), "expiration": "2026-12-18", "dte": 31,
                     "bid": 1.0, "ask": 1.1, "mid": 1.05 + (k - 40) * 0.25, "spread_pct": 8, "open_interest": 500, "volume": 100,
                     "iv": 35.0, "delta": -0.4, "moneyness_pct": (k / 43.05 - 1) * 100, "liquidity": "Liquid"})
    p = A.option_entry_plan("BSX", "bearish", 250, "failed_bounce", structure=st, rows=rows, rv20=0.3)
    assert p["available"], p.get("reason")
    assert p["underlying"]["in_zone"] is False
    assert p["broker_order"]["entry"]["type"] == "Stop limit"


def test_v374_board_log_rows_and_short_gate():
    import app as A
    plan = lambda d: {"available": True, "plan": "breakout", "underlying": {"entry": 10, "stop": 9 if d == "bullish" else 11, "target": 12 if d == "bullish" else 8, "rr": 2},
                      "single": {"cost": 120, "entry_premium": 1.2, "leg": {"symbol": "X", "expiration": "2026-12-18", "strike": 10, "type": "call" if d == "bullish" else "put"}}}
    payloads = {"reversal": {"rows": [{"ticker": "AAA", "direction": "bullish", "parent": "XLK", "prescore": 5, "entry_plan": plan("bullish"), "vp_plan": {"level": 9.8}}]},
                "put_side": {"rows": [{"ticker": "BBB", "lane": "failed_bounce", "parent": "XLV", "score": 4, "signal": "Shooter", "entry_plan": plan("bearish")}]},
                "pead": {"rows": [{"ticker": "CCC", "direction": "bullish", "score": 70, "entry_plan": {"available": False}}]}}
    risk_off = {"asof": "2026-11-19", "risk_appetite": "Risk-Off", "internals": {"SPY": {"above20": False, "d1": -1.5, "d5": -3}, "VIX": {"value": 26}, "CREDIT": {"d5": 0.3}}}
    risk_on = {"asof": "2026-11-19", "risk_appetite": "Risk-On", "internals": {"SPY": {"above20": True, "d1": 0.5}, "CREDIT": {"d5": 0.3}}}
    assert A.server_shorts_ok(risk_off) and not A.server_shorts_ok(risk_on)
    rows = A.board_log_rows("2026-11-19", risk_off, payloads)
    assert {(r["ticker"], r["lane"]) for r in rows} == {("AAA", "vp_counter"), ("BBB", "failed_bounce")}
    b = next(r for r in rows if r["ticker"] == "BBB")
    assert b["direction"] == "bearish" and b["contract"]["label"] == "2026-12-18 $10P" and b["regime"] == "Risk-Off"
    # shorts not allowed -> the put is not logged
    assert [r["ticker"] for r in A.board_log_rows("2026-11-19", risk_on, payloads)] == ["AAA"]


def test_v375_grade_strangle():
    import app as A
    bc = [{"t": "2026-11-18", "h": 1.0, "c": 1.0}, {"t": "2026-11-19", "h": 0.8, "c": 0.6}, {"t": "2026-11-20", "h": 0.5, "c": 0.3}]
    bp = [{"t": "2026-11-18", "h": 1.0, "c": 1.0}, {"t": "2026-11-19", "h": 1.6, "c": 1.5}, {"t": "2026-11-20", "h": 2.7, "c": 2.6}]
    g = A.grade_strangle(1.0, 1.0, bc, bp, "2026-11-18", 3, "2026-11-25")
    assert g["status"] == "win" and g["exit"] == "put +150%" and g["closed"] == "2026-11-20" and g["pct"] == 40.0   # (2.5 + 0.3)/2 - 1
    # nothing happens: time stop after 2 sessions
    flat = [{"t": "2026-11-19", "h": 1.05, "c": 0.9}, {"t": "2026-11-20", "h": 1.0, "c": 0.85}]
    g2 = A.grade_strangle(1.0, 1.0, flat, flat, "2026-11-18", 2, "2026-11-25")
    assert g2["status"] == "loss" and g2["exit"] == "time stop" and g2["pct"] == -15.0
    # combined stop
    bad = [{"t": "2026-11-19", "h": 0.7, "c": 0.6}]
    assert A.grade_strangle(1.0, 1.0, bad, bad, "2026-11-18", 3, None)["exit"] == "-35% combined"


def test_v375_day1_follow_through():
    import app as A
    # 40 quiet days, then a -3% day 1 that closes at the low, in a falling market
    closes = [100.0] * 40 + [97.0]
    highs = [101.0] * 40 + [99.5]; lows = [99.0] * 40 + [96.9]
    df = _bars(closes, highs=highs, lows=lows, opens=[100.0] * 40 + [99.0])
    f = A.day1_follow_through(df, df.index[-1], None, True)
    assert f["close_location"] >= 0.95 and f["label"] == "Strong" and "with the market" in f["read"]
    # a fade off the high on an up day, against a falling market
    df2 = _bars([100.0] * 40 + [104.0], highs=[101.0] * 40 + [109.0], lows=[99.0] * 40 + [100.5], opens=[100.0] * 40 + [107.0])
    f2 = A.day1_follow_through(df2, df2.index[-1], None, True)
    assert f2["label"] == "Weak"


def test_v376_confirm_day1_by_volume():
    import app as A, numpy as np
    df = _bars([100.0] * 30 + [110.0, 111.0], vol=1_000_000)
    v = df["Volume"].values.copy(); v[30] = 5_000_000; df["Volume"] = v
    # calendar maps day 1 to the session AFTER the spike (an "after-close" listing that was wrong)
    assert A.confirm_day1_index(df, 31) == 30
    # a heavier day 2 never moves day 1 forward
    v2 = df["Volume"].values.copy(); v2[31] = 9_000_000; df["Volume"] = v2
    assert A.confirm_day1_index(df, 30) == 30
    # no spike anywhere -> keep the calendar's session
    df2 = _bars([100.0] * 32, vol=1_000_000)
    assert A.confirm_day1_index(df2, 31) == 31


def test_v377_digest_endpoint_is_public_and_quiet():
    import app as A
    A.DIGEST_STATE["last"] = 0.0
    called = []
    orig = A.print_digest
    A.print_digest = lambda kind="nightly": called.append(kind)
    try:
        c = A.app.test_client()
        j = c.get("/api/digest/print").get_json()
        assert j["ok"] and j["queued"] and set(j) == {"ok", "queued"}      # returns no data
        assert c.get("/api/digest/print").get_json()["queued"] is False    # rate limited
    finally:
        A.print_digest = orig
    p = {"available": True, "underlying": {"entry": 10, "stop": 11, "target": 8, "rr": 2},
         "single": {"cost": 150, "leg": {"expiration": "2026-12-18", "strike": 9.5, "type": "put"}}}
    assert A._dg_plan(p) == "entry 10 stop 11 tgt 8 rr 2 · 12-18 $9.5P ≈$150"


def test_v378_universe_breadth():
    import app as A, numpy as np
    up = _bars(list(np.linspace(90, 110, 60)))
    down = _bars(list(np.linspace(110, 90, 60)))
    b = A.universe_breadth({"AAA": up, "BBB": down, "CCC": down})
    assert b["n"] == 3 and b["above50_pct"] == 33 and b["new20_highs"] == 1 and b["new20_lows"] == 2


def test_v3715_partial_then_breakeven_variant():
    import app as A, pandas as pd
    idx = pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16"])
    row = {"trade_date": "2026-11-10", "direction": "bullish", "entry": 100, "stop": 98, "target": 104, "fill": "limit", "lane": "pullback"}
    # fills at 100 on 11/11 (a high of 101.5 that day doesn't count -- it may have come before the dip),
    # +0.5R (101) on 11/12, back to break-even on 11/13 -> +0.25R, a win
    df = pd.DataFrame({"Open": [102, 100.8, 100.5, 101, 101], "High": [102.5, 101.5, 101.4, 101.1, 101],
                       "Low": [101, 99.8, 100.2, 99.9, 100], "Close": [101.5, 100.4, 101.2, 100.2, 100.5]}, index=idx)
    p = A.evaluate_setup_outcome(row, df)["pbe"]
    assert p["status"] == "win" and p["r"] == 0.25 and p["exit"] == "break-even" and p["closed"] == "2026-11-13"
    # partial then target: 0.25 + 0.5 * 2R
    df2 = df.copy(); df2.loc["2026-11-13", "High"] = 104.2; df2.loc["2026-11-13", "Low"] = 100.5
    p2 = A.evaluate_setup_outcome(row, df2)["pbe"]
    assert p2["exit"] == "target" and p2["r"] == 1.25
    # stopped out before +0.5R: the full -1R
    df3 = df.copy(); df3.loc["2026-11-12", "High"] = 100.6; df3.loc["2026-11-12", "Low"] = 97.5
    p3 = A.evaluate_setup_outcome(row, df3)["pbe"]
    assert p3["status"] == "loss" and p3["r"] == -1.0
    # bearish mirror on a trigger plan: fills on the break of 50, +0.5R = 49
    rowp = {"trade_date": "2026-11-10", "direction": "bearish", "entry": 50, "stop": 52, "target": 46, "fill": "stop", "lane": "catch_down"}
    dfp = pd.DataFrame({"Open": [51, 50.5, 49.6, 49.4, 49.8], "High": [51.5, 50.6, 49.9, 50.1, 50],
                        "Low": [50.5, 48.9, 49.2, 49.3, 49.5], "Close": [50.8, 49.7, 49.5, 49.6, 49.9]}, index=idx)
    pp = A.evaluate_setup_outcome(rowp, dfp)["pbe"]
    assert pp["status"] == "win" and pp["r"] == 0.25 and pp["exit"] == "break-even"


def test_v38_index_pullback_signal():
    import app as A, pandas as pd, numpy as np
    n = 120
    idx = pd.bdate_range("2026-01-01", periods=n)
    # steady uptrend; day -2 dips into the zone (SMA20 + 0.25 ATR) and closes weak;
    # the last day closes near its high above the zone -> signal at that close
    c = list(100 + np.arange(n - 3) * 0.5); last = c[-1]
    c += [last - 1.5, last - 3.3, last - 1.2]; c = np.array(c)
    h = c + 0.6; l = c - 0.6; o = c - 0.1
    h[-2] = c[-2] + 1.6; l[-2] = c[-2] - 0.9
    l[-1] = c[-1] - 1.6; h[-1] = c[-1] + 0.2
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)
    # (lookback=3 starts the replay at day -3; the live scan replays ~2 years so the
    #  one-order-at-a-time chain lines up with the backtest)
    r = A.index_pullback_signals(df, df, lookback=3)
    st = r["state"]
    assert st["status"] == "signal today", st
    assert st["direction"] == "bullish" and st["stop"] < st["entry"] < st["target"]
    assert abs(st["half_at"] - (st["entry"] + 0.5 * (st["entry"] - st["stop"]))) < 0.02
    rows = A.index_pullback_log_rows({"symbols": {"SPY": r}})
    assert rows and rows[0]["lane"] == "index_pullback" and rows[0]["entry"] == st["entry"]
    assert A.index_pullback_lines({"symbols": {"SPY": r}}).startswith("[digest index] SPY: SIGNAL (calls)")
    # Filter 3: SPY below its 50-day blocks the long confirmation
    spy_down = df.copy()
    spy_down[["Open", "High", "Low", "Close"]] = spy_down[["Open", "High", "Low", "Close"]].values[::-1]
    assert A.index_pullback_signals(df, spy_down, lookback=3)["state"].get("status") != "signal today"


def test_v381_index_pullback_trade_closes():
    import app as A, pandas as pd, numpy as np
    n = 126
    idx = pd.bdate_range("2026-01-01", periods=n)
    c = list(100 + np.arange(n - 9) * 0.5); last = c[-1]
    c += [last - 1.5, last - 3.3, last - 1.2]
    c += [c[-1] + 1.5 * k for k in range(1, 7)]       # rally through the target
    c = np.array(c)
    h = c + 0.6; l = c - 0.6; o = c - 0.1
    h[-8] = c[-8] + 1.6; l[-8] = c[-8] - 0.9
    l[-7] = c[-7] - 1.6; h[-7] = c[-7] + 0.2
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)
    r = A.index_pullback_signals(df, df, lookback=9)
    ev = r["events"][0]
    assert ev["exit"] == "target" and ev["r"] == ev["rr"] > 0   # target inside +0.5R: whole position exits there
    assert "last trade" in A.index_pullback_lines({"symbols": {"SPY": {"state": {**ev, "status": "closed"}}}})
