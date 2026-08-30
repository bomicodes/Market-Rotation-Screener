from pathlib import Path
p=Path('app.py')
s=p.read_text()

def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.14"','APP_VERSION = "27.15"','version')

once('''RRG_STD_WINDOW = 63  # ~1 trading quarter; shared here and in compute_rrg's default
                      # so the warm-up guard below can't silently drift out of sync.
RRG_HISTORY_LEN = 200  # ~9-10 months of daily observations for the main dashboard.
RRG_WEEKLY_HISTORY_LEN = 104  # ~2 years of true weekly observations for the 1W timeline.
''','''RRG_STD_WINDOW = 63  # Daily: ~1 trading quarter.
RRG_WEEKLY_STD_WINDOW = 13  # Weekly equivalent of ~63 trading sessions.
RRG_DESPIKE_THRESHOLD_DAILY = 4.0
RRG_DESPIKE_THRESHOLD_WEEKLY = 9.0  # ~4% * sqrt(5), scaled for natural weekly movement.
RRG_HISTORY_LEN = 200  # ~9-10 months of daily observations for the main dashboard.
RRG_WEEKLY_HISTORY_LEN = 104  # ~2 years of true weekly observations for the 1W timeline.

def rrg_calibration(timeframe="1d"):
    """Return timeframe-aware RRG normalization/noise settings.

    1D preserves the calibration used before weekly support was introduced.
    1W uses time-equivalent volatility estimation and a wider isolated-spike
    threshold so legitimate weekly RS moves are not treated like daily glitches.
    """
    if timeframe == "1w":
        return RRG_WEEKLY_STD_WINDOW, RRG_DESPIKE_THRESHOLD_WEEKLY
    return RRG_STD_WINDOW, RRG_DESPIKE_THRESHOLD_DAILY
''','calibration constants')

once('''def compute_rrg(bench, asset, n1=10, n2=5, std_window=RRG_STD_WINDOW):
''','''def compute_rrg(bench, asset, n1=10, n2=5, std_window=None, timeframe="1d", despike_threshold_pct=None):
''','compute signature')

once('''    b = np.asarray(bench, dtype=float)
    a = np.asarray(asset, dtype=float)
    rs = despike_rs(pd.Series((a / b) * 100.0, dtype=float))
    rs_mean = rs.rolling(n1).mean()
    rs_std = rs.rolling(max(std_window, n1)).std(ddof=1).replace(0, np.nan)
''','''    b = np.asarray(bench, dtype=float)
    a = np.asarray(asset, dtype=float)
    calibrated_std, calibrated_despike = rrg_calibration(timeframe)
    if std_window is None:
        std_window = calibrated_std
    if despike_threshold_pct is None:
        despike_threshold_pct = calibrated_despike
    rs = despike_rs(pd.Series((a / b) * 100.0, dtype=float), threshold_pct=despike_threshold_pct)
    rs_mean = rs.rolling(n1).mean()
    rs_std = rs.rolling(max(std_window, n1)).std(ddof=1).replace(0, np.nan)
''','compute calibration body')

once('''def rrg_rows(prices, bench_ticker, members, n1=10, n2=5, tail=8, history_len=None):
    out = []
''','''def rrg_rows(prices, bench_ticker, members, n1=10, n2=5, tail=8, history_len=None, timeframe="1d"):
    out = []
    std_window, _ = rrg_calibration(timeframe)
''','rrg rows signature')

once('''        min_needed = max(2*RRG_STD_WINDOW + span + 10, n1+n2+span+5)
        if len(pair) < min_needed:
            continue
        ratio, mom = compute_rrg(pair[bench_ticker].values, pair[ticker].values, n1, n2)
''','''        min_needed = max(2*std_window + span + 10, n1+n2+span+5)
        if len(pair) < min_needed:
            continue
        ratio, mom = compute_rrg(pair[bench_ticker].values, pair[ticker].values, n1, n2, timeframe=timeframe)
''','rrg rows calibrated warmup')

once('''    fast = {r["ticker"]: r for r in rrg_rows(rrg_prices, bench_ticker, members, 10, 5, tail_fast, history_len)}
    trend = {r["ticker"]: r for r in rrg_rows(rrg_prices, bench_ticker, members, 25, 12, tail_trend, history_len)}
''','''    fast = {r["ticker"]: r for r in rrg_rows(rrg_prices, bench_ticker, members, 10, 5, tail_fast, history_len, timeframe)}
    trend = {r["ticker"]: r for r in rrg_rows(rrg_prices, bench_ticker, members, 25, 12, tail_trend, history_len, timeframe)}
''','dual threads timeframe')

# Update comments/docstring that described 63 as universal.
s=s.replace('''    IMPORTANT: the mean window (n1/n2) and the standard-deviation window are
    deliberately decoupled. A first pass used n1/n2 for both the mean AND the
    std, which looked right at Trend's 25/12 window but made Fast's 10/5
    window noticeably WORSE than the old formula -- a rolling std computed
    from only 5-10 points is itself a high-variance estimate, so dividing by
    it amplifies noise instead of damping it. std_window=63 (~1 trading
    quarter, a standard volatility-estimation convention) stabilizes the
    denominator on both Fast and Trend, while n1/n2 still control how quickly
    the centerline itself reacts -- which is what should differ between the
    two, not how noisy the normalization is.
''','''    IMPORTANT: the mean window (n1/n2) and the standard-deviation window are
    deliberately decoupled. The volatility window is timeframe-aware: 63
    observations on 1D and 13 observations on 1W, each representing roughly
    one trading quarter. Fast/Trend still control centerline sensitivity via
    n1/n2; changing 1D/1W changes observation periodicity and its calibration,
    not the underlying RS-Ratio / RS-Momentum formula.
''')
s=s.replace('''        # The z-score formula's rs_std/roc_std each need RRG_STD_WINDOW valid
        # observations before producing a single non-NaN point, and momentum
        # needs that satisfied twice in sequence (ratio's std window, then
        # roc's std window on top of it) -- roughly 2*RRG_STD_WINDOW points
''','''        # The z-score formula's rs_std/roc_std each need the calibrated std
        # window before producing a single non-NaN point, and momentum needs
        # that satisfied twice in sequence (ratio, then ROC normalization) --
        # roughly 2*std_window observations
''')

p.write_text(s)
print('patched app.py to v27.15')
