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


def test_market_wide_earnings_work_is_bounded_to_returned_rows():
    assert 'alpaca_multi_daily_closes(price_symbols,"18mo")' in APP_SOURCE
    assert 'dl_prices(price_symbols,"18mo",repair_missing=False,attempts=1)' in APP_SOURCE
    assert "futs=[ex.submit(enrich,x) for x in prelim[:12]]" in APP_SOURCE
    assert 'postearnings-opportunities-v3:' in APP_SOURCE


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
