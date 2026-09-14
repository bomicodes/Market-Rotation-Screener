from pathlib import Path


APP_SOURCE = (Path(__file__).parents[1] / "app.py").read_text()


def test_stock_rrg_click_opens_all_ticker_modules_without_scrolling():
    handler_start = APP_SOURCE.index('function installRRGInteractions(id)')
    handler_end = APP_SOURCE.index('installRRGInteractions("sectorChart")', handler_start)
    handler = APP_SOURCE[handler_start:handler_end]

    assert 'id==="stockChart"' in handler
    assert 'openSectorStockTicker(ticker,{scroll:false})' in handler


def test_stock_rows_are_not_intercepted_by_focus_only_capture_handler():
    assert 'function installStockSummaryFocusOnly()' not in APP_SOURCE
    assert 'openSectorStockTicker(row.dataset.liveTicker,{scroll:true})' in APP_SOURCE


def test_shared_ticker_loader_fans_out_chart_strat_and_options():
    loader_start = APP_SOURCE.index('async function openSectorStockTicker(')
    loader_end = APP_SOURCE.index('\nfunction liveWatchKey(', loader_start)
    loader = APP_SOURCE[loader_start:loader_end]

    assert 'loadChartPreview(ticker)' in loader
    assert 'loadStrat(ticker)' in loader
    assert 'loadOptionsTicker(ticker,{scroll:false})' in loader


def test_selected_options_request_has_one_bounded_rate_limit_retry():
    loader_start = APP_SOURCE.index('async function loadOptionsTicker(')
    loader_end = APP_SOURCE.index('\nasync function scanVisibleOptions(', loader_start)
    loader = APP_SOURCE[loader_start:loader_end]

    assert 'attempts:2' in loader
    assert 'rateLimitWaitMs:10000' in loader
    assert 'attempts:3' not in loader
