import numpy as np
import pandas as pd

import app as appmod


def test_black_scholes_put_call_parity_and_iv_roundtrip():
    s, k, t, iv, r = 100.0, 105.0, 45 / 365, 0.32, 0.04
    c = appmod.bs_price(s, k, t, iv, False, r)
    p = appmod.bs_price(s, k, t, iv, True, r)
    assert abs((c - p) - (s - k * np.exp(-r * t))) < 1e-6
    assert abs(appmod.bs_implied_vol(c, s, k, t, False) - iv) < 1e-3


def test_pullback_plan_for_extended_uptrend_waits_near_20d_mean():
    # LEN-like: spot far above its 20-day average.
    pb = appmod._pullback_plan("bullish", 91.58, 3.55, 82.08, 93.84, 80.0)
    assert pb["entry"] < 85 and pb["stop"] < pb["entry"] < pb["target"]
    assert pb["in_zone"] is False
    assert pb["rr"] >= 1.5


def _frame(highs, lows, opens, closes):
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": 1e6}, index=idx)


def test_supply_rejection_detected_at_prior_high():
    # Prior high ~1096 earlier in the year, a selloff, then a rally back into
    # the zone that leaves a long upper wick (COST-like).
    n = 200
    base = list(np.linspace(1000, 1096, 60)) + list(np.linspace(1090, 950, 70)) + list(np.linspace(955, 1060, n - 130))
    closes = np.array(base, dtype=float)
    highs = closes + 6
    lows = closes - 6
    opens = closes.copy()
    # Rejection bar: open 1071, high 1081.5, low 1061, close 1065.6 (last bar).
    opens[-1], highs[-1], lows[-1], closes[-1] = 1071.0, 1081.55, 1061.0, 1065.59
    df = _frame(highs, lows, opens, closes)
    atr = 16.0
    sig = appmod._rejection_signal(df, atr, float(pd.Series(closes).rolling(20).mean().iloc[-1]))
    assert sig is not None
    assert sig["type"] == "supply_rejection" and sig["direction"] == "bearish"
    assert sig["stop"] > sig["trigger"] > sig["target2"]


def test_no_rejection_on_clean_breakout_close():
    n = 200
    closes = np.array(list(np.linspace(1000, 1096, 60)) + list(np.linspace(1090, 950, 70)) + list(np.linspace(955, 1095, n - 130)))
    highs, lows, opens = closes + 3, closes - 3, closes - 2
    opens[-1], highs[-1], lows[-1], closes[-1] = 1090.0, 1110.0, 1088.0, 1109.0
    df = _frame(highs, lows, opens, closes)
    assert appmod._rejection_signal(df, 16.0, 1050.0) is None


def test_falling_premium_is_not_called_support():
    closes = [135, 128, 120, 112, 104, 97, 90, 84, 78, 72, 66, 60, 56, 53]
    bars = [{"o": c * 1.01, "h": c * 1.04, "l": c * .97, "c": c, "v": 10} for c in closes]
    m = appmod._premium_support_metrics(bars, 52.5)
    assert m["state"] == "FALLING · NO BASE"
    assert m["floor_reliable"] is False
    assert m["score"] <= 35


def _row(kind, strike, dte, spot, iv=0.40, spread=6.0):
    is_put = kind == "put"
    t = dte / 365
    mid = appmod.bs_price(spot, strike, t, iv, is_put)
    # crude delta from a finite difference
    d = (appmod.bs_price(spot + .01, strike, t, iv, is_put) - mid) / .01
    return {"symbol": f"X{kind[0]}{strike}{dte}", "type": kind, "expiration": f"2027-01-{dte % 28 + 1:02d}", "dte": dte,
            "strike": float(strike), "mid": mid, "bid": mid * .97, "ask": mid * 1.03, "delta": d, "iv": iv * 100,
            "open_interest": 500, "volume": 100, "spread_pct": spread, "liquidity": "Liquid"}


def _structure(spot, atr, sma20):
    return {"available": True, "spot": spot, "atr14": atr, "direction": "bullish", "plan_valid": False,
            "pullback": appmod._pullback_plan("bullish", spot, atr, sma20, spot + 2.5 * atr, spot - 4 * atr)}


def test_entry_plan_is_single_leg_with_exit_rules():
    spot = 91.58
    rows = [_row("call", k, d, spot) for k in range(80, 106) for d in (28, 42, 63)]
    st = _structure(spot, 3.55, 82.08)
    out = appmod.option_entry_plan("LEN", "bullish", 250, None, st, rows, spot, 3.55, 0.30)
    assert out["available"] and out["plan"] == "pullback"
    assert out["spread"] is None
    assert out["single"] and out["single"]["cost"] <= 250 and out["recommended"] == "single"
    ep = out["exit_plan"]
    assert ep["premium_stop"] < out["single"]["entry_premium"] < ep["tp1_premium"] <= out["single"]["entry_premium"] * 1.5 + 1e-6
    assert ep["exit_by"] and len(ep["rules"]) == 5
    assert out["iv_rv"] and out["iv_rv"] >= 1.3


def test_entry_plan_reports_when_nothing_fits_budget():
    spot = 1065.0
    rows = [_row("put", k, 42, spot, iv=.25) for k in range(1000, 1080, 5)]
    st = {"available": True, "spot": spot, "atr14": 17.0, "direction": "bullish", "plan_valid": False, "pullback": None,
          "counter": {"type": "supply_rejection", "direction": "bearish", "trigger": 1059.3, "stop": 1085.8, "target2": 1010.0, "rr": 1.86}}
    out = appmod.option_entry_plan("COST", "bearish", 250, "rejection", st, rows, spot, 17.0, 0.22)
    assert out["available"] and out["plan"] == "rejection"
    assert out["single"] is None
    assert any("budget" in n for n in out["notes"])


# ---------------------------------------------------------------- v29 ------
def _daily(rows, start="2026-10-01"):
    idx = pd.bdate_range(start, periods=len(rows))
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}, index=idx)


def test_strat_failed_2u_and_inside_bar():
    df = _daily([(100, 102, 98, 101), (101, 105, 100, 104), (104, 107, 103, 103.5)])
    sig = appmod.strat_actionable(df, "1D")
    assert any(x["type"] == "Failed 2U" and x["direction"] == "bearish" for x in sig)
    df2 = _daily([(100, 102, 98, 101), (101, 105, 100, 104), (103, 104.5, 101, 102)])
    sig2 = appmod.strat_actionable(df2, "1D")
    assert any(x["type"].endswith("pending") for x in sig2)


def test_strat_ftfc_states():
    rows = [(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(70)]
    st = appmod.strat_ftfc(_daily(rows, "2026-07-01"))
    assert st["state"] == "bullish"


def _visible(prices_by_session):
    sessions = []
    for i, prices in enumerate(prices_by_session):
        bins = [{"price": p, "volume": v} for p, v in prices]
        sessions.append({"date": f"2026-10-{i+1:02d}", "profile": {"bins": bins}})
    weeks = [{"week": "2026-W41", "profile": {"vah": 104, "poc": 101, "val": 98}},
             {"week": "2026-W42", "profile": {"vah": 106, "poc": 103, "val": 100}}]
    return {"sessions": sessions, "weeks": weeks}


def test_swing_profile_and_vp_strat_plan():
    sess = []
    for _ in range(15):
        sess.append([(95 + k * .5, 2000 if 100 <= 95 + k * .5 <= 102 else (300 if 103 <= 95 + k * .5 <= 104 else 800)) for k in range(30)])
    vp = appmod.swing_volume_profile(_visible(sess), 101.2)
    assert vp["available"] and 99 <= vp["poc"] <= 103 and vp["val"] < vp["poc"] < vp["vah"]
    bar = pd.Series({"Open": 100.8, "High": 101.6, "Low": 100.5, "Close": 101.2})
    plan = appmod.vp_strat_plan("bullish", 101.2, 1.5, vp, bar, 108.0, 94.0)
    assert plan and plan["stop"] < plan["level"] <= plan["entry"] < plan["target"]
    assert plan["rr"] > 0


def test_outcome_win_loss_and_expiry():
    df = _daily([(100, 101, 99, 100), (100, 103, 99.5, 102.5), (102.5, 106.5, 102, 106), (106, 107, 105, 106)], "2026-10-05")
    row = {"trade_date": "2026-10-05", "lane": "breakout", "direction": "bullish", "entry": 102.0, "stop": 99.0, "target": 106.0}
    out = appmod.evaluate_setup_outcome(row, df)
    assert out["status"] == "win" and out["r"] > 1
    row2 = dict(row, stop=101.0, target=110.0)
    df2 = _daily([(100, 101, 99, 100), (100, 103, 100.5, 102.5), (102.5, 102.8, 100.8, 101)], "2026-10-05")
    assert appmod.evaluate_setup_outcome(row2, df2)["status"] == "loss"
    row3 = dict(row, lane="pullback", entry=90.0, stop=88.0, target=96.0)
    flat = _daily([(100, 101, 99, 100)] * 13, "2026-10-05")
    assert appmod.evaluate_setup_outcome(row3, flat)["status"] == "expired"


def test_setup_log_roundtrip_and_track_record(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "DATABASE_URL", "")
    monkeypatch.setattr(appmod, "SETUP_DB_PATH", str(tmp_path / "t.sqlite3"))
    n = appmod.log_setups([{"ticker": "LEN", "trade_date": "2026-11-05", "lane": "pullback", "direction": "bullish", "entry": 83, "stop": 78.5, "target": 95}])
    assert n == 1
    tr = appmod.track_record_payload(days=3650)
    assert tr["total"] == 1 and tr["lanes"][0]["lane"] == "pullback"


def test_l2_only_serves_after_close(monkeypatch):
    monkeypatch.setattr(appmod, "L2_PREFIXES", ("market",))
    monkeypatch.setattr(appmod, "market_open_now", lambda now=None: True)
    appmod.L2_MEMORY["market"] = (9e12, {"x": 1})
    assert appmod.l2_get("market") is None
    monkeypatch.setattr(appmod, "market_open_now", lambda now=None: False)
    monkeypatch.setattr(appmod, "_l2_preload", lambda: None)
    assert appmod.l2_get("market")[1] == {"x": 1}


def test_tight_budget_falls_back_to_lower_delta():
    spot = 91.58
    rows = [_row("call", k, d, spot) for k in range(80, 112) for d in (28, 42, 63)]
    out = appmod.option_entry_plan("LEN", "bullish", 120, None, _structure(spot, 3.55, 82.08), rows, spot, 3.55, 0.30)
    assert out["single"] and out["single"]["cost"] <= 120
    assert .15 <= out["single"]["delta_at_entry"] < .30
