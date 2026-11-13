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
