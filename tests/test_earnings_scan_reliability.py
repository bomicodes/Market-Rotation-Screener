from pathlib import Path

import app as appmod


APP_SOURCE = (Path(__file__).parents[1] / "app.py").read_text()


def test_earnings_client_uses_one_bounded_safe_request_without_retry_loop():
    start = APP_SOURCE.index("async function runEarnings()")
    end = APP_SOURCE.index("\nlet historicalData=[];", start)
    loader = APP_SOURCE[start:end]

    assert 'safeServiceFetchJson("/api/postearnings-opportunities"' in loader
    assert "timeoutMs:110000" in loader
    assert "const waits=" not in loader
    assert "Earnings service restarted or is busy" not in loader


def test_earnings_failure_preserves_prior_results_and_is_not_unreadable():
    start = APP_SOURCE.index("async function runEarnings()")
    end = APP_SOURCE.index("\nlet historicalData=[];", start)
    loader = APP_SOURCE[start:end]

    assert "earnResults=previousResults" in loader
    assert "Earnings scan incomplete:" in loader
    assert "Earnings service returned an unreadable response" not in loader


def test_market_wide_earnings_scan_defers_all_historical_profiles():
    assert 'alpaca_multi_daily_closes(price_symbols,"18mo")' in APP_SOURCE
    assert 'dl_prices(price_symbols,"18mo",repair_missing=False,attempts=1)' in APP_SOURCE
    start = APP_SOURCE.index("def api_postearnings_opportunities()")
    end = APP_SOURCE.index('@app.get("/api/postearnings-option/<ticker>")', start)
    scanner = APP_SOURCE[start:end]
    assert "for pre,sym,d,rot,cur in prelim[:12]:" in scanner
    assert "earnings_profile(" not in scanner
    assert "merged_historical_earnings_dates(" not in scanner
    assert 'postearnings-opportunities-v4:' in scanner


def test_options_hydration_does_not_recompute_history():
    start = APP_SOURCE.index("def api_postearnings_option(ticker)")
    end = APP_SOURCE.index('@app.get("/api/earnings-history/<ticker>")', start)
    endpoint = APP_SOURCE[start:end]
    assert 'request.args.get("setup_type")' in endpoint
    assert "earnings_profile(" not in endpoint
    assert "merged_historical_earnings_dates(" not in endpoint
    assert 'setup_type:x.setup_type||""' in APP_SOURCE
    assert "Promise.all([worker(),worker()])" in APP_SOURCE


def test_scan_holdings_prefers_persistent_cache(monkeypatch):
    saved = ([{"ticker": "META", "name": "Meta"}] * 5, "issuer", "2026-09-14T00:00:00Z")
    monkeypatch.setattr(appmod, "_load_holdings_cache", lambda etf: saved)
    monkeypatch.setattr(appmod, "get_fund_holdings", lambda etf: (_ for _ in ()).throw(AssertionError("live called")))

    holdings, source = appmod.get_fund_holdings_scan("XLC")

    assert holdings[0]["ticker"] == "META"
    assert source.startswith("Cached holdings")


def test_alpaca_multi_symbol_daily_close_parser(monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "bars": {
                    "SPY": [{"t": "2026-09-11T04:00:00Z", "c": 700.0}],
                    "CRWD": [{"t": "2026-09-11T04:00:00Z", "c": 530.0}],
                },
                "next_page_token": None,
            }

    monkeypatch.setattr(appmod, "ALPACA_API_KEY", "key")
    monkeypatch.setattr(appmod, "ALPACA_API_SECRET", "secret")
    monkeypatch.setattr(appmod, "alpaca_get", lambda *args, **kwargs: Response())

    frame = appmod.alpaca_multi_daily_closes(["SPY", "CRWD"], "18mo")

    assert list(frame.columns) == ["SPY", "CRWD"]
    assert frame.iloc[-1]["SPY"] == 700.0
    assert frame.iloc[-1]["CRWD"] == 530.0
