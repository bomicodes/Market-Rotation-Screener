import pandas as pd

import app as appmod


def test_top_setup_universe_uses_one_shared_price_download(monkeypatch):
    calls = []

    def holdings(etf):
        return ([
            {"ticker": f"{etf}A", "name": f"{etf} A", "weight": 60.0},
            {"ticker": f"{etf}B", "name": f"{etf} B", "weight": 40.0},
        ], "test holdings")

    def prices(tickers, period, repair_missing, attempts):
        calls.append((list(tickers), period, repair_missing, attempts))
        return pd.DataFrame(index=pd.bdate_range("2025-01-01", periods=150))

    def stock_rows(frame, benchmark, members, *args):
        return [{
            "ticker": ticker,
            "fast": {"quadrant": "Improving"},
            "trend": {"quadrant": "Improving"},
            "date": "2026-09-14",
        } for ticker in members]

    monkeypatch.setattr(appmod, "get_fund_holdings", holdings)
    monkeypatch.setattr(appmod, "apply_sector_supplements", lambda etf, rows: rows)
    monkeypatch.setattr(appmod, "dl_prices", prices)
    monkeypatch.setattr(appmod, "dual_rrg_rows", stock_rows)
    monkeypatch.setattr(appmod, "rrg_rows", lambda *args, **kwargs: [{"quadrant": "Improving"}])

    payload = appmod.top_setup_universe_payload(["XLC", "XLK"], limit=2)

    assert len(calls) == 1
    symbols, period, repair_missing, attempts = calls[0]
    assert symbols == ["SPY", "XLC", "XLCA", "XLCB", "XLK", "XLKA", "XLKB"]
    assert period == "18mo"
    assert repair_missing is False
    assert attempts == 1
    assert [group["sector"] for group in payload["groups"]] == ["XLC", "XLK"]
    assert payload["symbols_downloaded"] == 7
