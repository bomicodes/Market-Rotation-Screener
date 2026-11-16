import numpy as np
import pandas as pd

import app as appmod


def _pead_frame(bull=True, react=8.0, vol_mult=3.0, after=4, giveback=0.1, inside=True, n=60):
    """n-1-after quiet sessions, a day-1 earnings bar, then `after` flag sessions."""
    rng = np.random.default_rng(3)
    pre = n - 1 - after
    base = 100 + np.cumsum(rng.normal(0, .4, pre))
    c, h, l, v = list(base), list(base + .8), list(base - .8), [1e6] * pre
    b = c[-1]; c1 = b * (1 + (react if bull else -react) / 100)
    rng1 = abs(c1 - b) * .6
    h1, l1 = (c1 + rng1 * .1, c1 - rng1) if bull else (c1 + rng1, c1 - rng1 * .1)
    c.append(c1); h.append(h1); l.append(l1); v.append(1e6 * vol_mult)
    sgn = 1 if bull else -1
    for i in range(after):
        px = c1 - sgn * abs(c1 - b) * giveback * (i + 1) / after
        c.append(px); h.append(px + .5); l.append(px - .5); v.append(1.2e6)
    if inside and after >= 2:
        h[-1], l[-1] = h[-2] - .1, l[-2] + .1
    idx = pd.bdate_range(end="2026-11-13", periods=len(c))
    df = pd.DataFrame({"Open": np.r_[c[0], c[:-1]], "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)
    return df, idx[pre]


def test_pead_bull_setup_found_with_inside_bar_trigger():
    df, ev = _pead_frame()
    z, why = appmod._pead_eval(df, ev, "premarket")
    assert z, why
    assert z["direction"] == "bullish" and z["trigger_kind"] == "inside-bar break"
    p = z["plan"]
    assert p["stop"] < p["entry"] < p["target"]
    assert abs((p["target"] - p["entry"]) - 2 * (p["entry"] - p["stop"])) < .05
    assert p["entry"] - p["stop"] >= z["atr14"] - .01   # stop at least 1 ATR


def test_pead_bear_mirror():
    df, ev = _pead_frame(bull=False)
    z, why = appmod._pead_eval(df, ev, "premarket")
    assert z, why
    assert z["direction"] == "bearish" and z["plan"]["target"] < z["plan"]["entry"] < z["plan"]["stop"]


def test_pead_after_close_report_uses_next_session():
    df, ev = _pead_frame()
    prior = df.index[df.index.get_loc(ev) - 1]
    z, why = appmod._pead_eval(df, prior, "afterhours")
    assert z and z["event_date"] == str(ev.date())


def test_pead_rejections():
    df, ev = _pead_frame(react=2.5)
    assert appmod._pead_eval(df, ev)[0] is None
    df, ev = _pead_frame(vol_mult=1.3)
    assert "volume" in appmod._pead_eval(df, ev)[1]
    df, ev = _pead_frame(giveback=.7)
    assert appmod._pead_eval(df, ev)[0] is None
    df, ev = _pead_frame(after=0, inside=False)
    assert "wait for a flag" in appmod._pead_eval(df, ev)[1]


def test_pead_plan_feeds_existing_contract_picker_levels():
    s = {"pead_plans": {"bullish": {"entry": 110.0, "stop": 105.0, "target": 120.0, "rr": 2.0}},
         "vp_plans": {"bullish": {"entry": 108.0, "stop": 100.0, "target": 130.0, "rr": 2.75, "in_zone": False}}, "spot": 109.0}
    lv = appmod._entry_levels_for(s, "bullish", "pead")
    assert lv["plan"] == "pead" and lv["entry"] == 110.0 and lv["stop"] == 105.0


def test_catalyst_kind_and_date():
    assert appmod._catalyst_kind("Acme to Present at the UBS Global Technology Conference")[0] == "conference"
    assert appmod._catalyst_kind("Acme Announces 2026 Investor Day")[0] == "investor_day"
    assert appmod._catalyst_kind("Acme Reports Third Quarter Results") is None
    assert appmod._catalyst_kind("Acme to Host Third Quarter Earnings Conference Call") is None
    d = appmod._extract_event_date("Acme to present at conference on December 3, 2026", "2026-11-10T13:00:00Z")
    assert str(d.date()) == "2026-12-03"
    d = appmod._extract_event_date("Investor Day on Jan. 14", "2026-11-20T13:00:00Z")
    assert str(d.date()) == "2027-01-14"


def test_news_catalysts_filters_window_and_watchlist():
    arts = [
        {"headline": "Acme to Present at Barclays Conference on November 18", "created_at": "2026-11-10T12:00:00Z", "symbols": ["ACME", "ZZZ"]},
        {"headline": "Acme to Present at Old Conference on October 2", "created_at": "2026-09-20T12:00:00Z", "symbols": ["ACME"]},
        {"headline": "Beta Reports Q3 Results", "created_at": "2026-11-12T12:00:00Z", "symbols": ["BETA"]},
    ]
    out = appmod.news_catalysts(arts, ["ACME", "BETA"], today="2026-11-15", within_days=14)
    assert len(out) == 1 and out[0]["ticker"] == "ACME" and out[0]["date"] == "2026-11-18" and out[0]["days_away"] == 3


def test_past_event_recaps_are_dropped():
    # v37.1: NOW's 11/4 event showed up after it had passed
    arts = [
        {"headline": "ServiceNow Investor Day: 5 Takeaways", "created_at": "2026-11-10T12:00:00Z", "symbols": ["NOW"]},
        {"headline": "ServiceNow at Investor Day: CEO Sees AI Agents Driving Growth", "created_at": "2026-11-11T12:00:00Z", "symbols": ["NOW"]},
        {"headline": "ServiceNow Investor Day Highlights Margin Targets", "created_at": "2026-11-12T12:00:00Z", "symbols": ["NOW"]},
        {"headline": "ServiceNow to Host Investor Day on 11/4", "created_at": "2026-11-12T12:00:00Z", "symbols": ["NOW"]},
        {"headline": "ServiceNow Investor Day Draws Analyst Praise", "created_at": "2026-11-12T12:00:00Z", "symbols": ["NOW"]},
        {"headline": "ServiceNow to Present at the UBS Technology Conference", "created_at": "2026-11-13T12:00:00Z", "symbols": ["NOW"]},
    ]
    out = appmod.news_catalysts(arts, ["NOW"], today="2026-11-15", within_days=14)
    assert [o["headline"] for o in out] == ["ServiceNow to Present at the UBS Technology Conference"]
    assert out[0]["date"] is None


def test_numeric_and_past_dates():
    assert str(appmod._extract_event_date("event on 11/4", "2026-11-14T00:00:00Z").date()) == "2026-11-04"
    assert str(appmod._extract_event_date("webcast 12/2/2026", "2026-11-14T00:00:00Z").date()) == "2026-12-02"
    assert str(appmod._extract_event_date("Investor Day on Jan. 14", "2026-11-20T00:00:00Z").date()) == "2027-01-14"
    assert appmod._extract_event_date("up 3.5% to $1/share", "2026-11-14T00:00:00Z") is None
