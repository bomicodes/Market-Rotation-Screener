from pathlib import Path


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
    assert 'dl_prices(["SPY"]+reporters,"18mo",repair_missing=False,attempts=1)' in APP_SOURCE
    assert "futs=[ex.submit(enrich,x) for x in prelim[:12]]" in APP_SOURCE
    assert 'postearnings-opportunities-v3:' in APP_SOURCE
