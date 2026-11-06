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


def test_entry_plan_respects_budget_and_offers_spread():
    spot = 91.58
    rows = [_row("call", k, 42, spot) for k in range(80, 106)]
    st = _structure(spot, 3.55, 82.08)
    out = appmod.option_entry_plan("LEN", "bullish", 250, None, st, rows, spot, 3.55, 0.30)
    assert out["available"]
    assert out["plan"] == "pullback"
    if out["single"]:
        assert out["single"]["cost"] <= 250
    if out["spread"]:
        assert out["spread"]["cost"] <= 250
        assert out["spread"]["premium_at_target"] <= out["spread"]["max_value"] + 1e-6
    assert out["single"] or out["spread"]
    # IV 0.40 vs realized 0.30 -> rich premium flagged
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
