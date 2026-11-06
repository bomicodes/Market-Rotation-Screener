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
    win = appmod.evaluate_position(pos, {"C": 5.6, "P": 0.08}, 104.0)
    assert win["status"] == "EXIT" and any("Take profit" in a["text"] for a in win["alerts"])
    assert any("under $0.10" in a["text"] for a in win["alerts"])
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
