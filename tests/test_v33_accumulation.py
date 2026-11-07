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
    assert 15 <= b["base_days"] <= 60 and b["height_atr"] <= 3.5
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
