from pathlib import Path

p=Path('app.py')
s=p.read_text()

def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)

once('APP_VERSION = "27.9"','APP_VERSION = "27.10"','version')

old='''def sma(arr, n):
    return pd.Series(arr, dtype=float).rolling(n).mean().to_numpy()

def compute_rrg(bench, asset, n1=10, n2=5):
'''
new='''def sma(arr, n):
    return pd.Series(arr, dtype=float).rolling(n).mean().to_numpy()

RRG_STD_WINDOW = 63  # ~1 trading quarter; shared here and in compute_rrg's default
                      # so the warm-up guard below can't silently drift out of sync.

def compute_rrg(bench, asset, n1=10, n2=5, std_window=RRG_STD_WINDOW):
'''
once(old,new,'signature')

old='''    Previous version used a plain ratio-of-SMA (100 * RS/SMA(RS)) with no
    volatility scaling, which let every sector swing the same amount for the
    same % move regardless of that sector's own volatility -- this produced
    visibly noisier, more jagged tails than standard JdK RRG tools. The
    z-score version below scales each sector's deviation by its own recent
    volatility, which is what makes standard RRG tails read as smooth arcs
    rather than jagged zigzags.

    RS-Ratio     = 100 + (RS - SMA(RS, n1)) / STDEV(RS, n1)
    RS-Momentum  = 100 + (ROC - SMA(ROC, n2)) / STDEV(ROC, n2)
                   where ROC is the period-over-period % change of RS-Ratio.
'''
new='''    Previous (pre-z-score) version used a plain ratio-of-SMA (100 * RS/SMA(RS))
    with no volatility scaling, which let every sector swing the same amount
    for the same % move regardless of that sector's own volatility.

    IMPORTANT: the mean window (n1/n2) and the standard-deviation window are
    deliberately decoupled. A first pass used n1/n2 for both the mean AND the
    std, which looked right at Trend's 25/12 window but made Fast's 10/5
    window noticeably WORSE than the old formula -- a rolling std computed
    from only 5-10 points is itself a high-variance estimate, so dividing by
    it amplifies noise instead of damping it. std_window=63 (~1 trading
    quarter, a standard volatility-estimation convention) stabilizes the
    denominator on both Fast and Trend, while n1/n2 still control how quickly
    the centerline itself reacts -- which is what should differ between the
    two, not how noisy the normalization is.

    RS-Ratio     = 100 + (RS - SMA(RS, n1)) / STDEV(RS, std_window)
    RS-Momentum  = 100 + (ROC - SMA(ROC, n2)) / STDEV(ROC, std_window)
                   where ROC is the period-over-period % change of RS-Ratio.
'''
once(old,new,'docstring')

once('rs_std = rs.rolling(n1).std(ddof=1).replace(0, np.nan)','rs_std = rs.rolling(max(std_window, n1)).std(ddof=1).replace(0, np.nan)','rs std')
once('roc_std = roc.rolling(n2).std(ddof=1).replace(0, np.nan)','roc_std = roc.rolling(max(std_window, n2)).std(ddof=1).replace(0, np.nan)','roc std')

old='''        pair = prices[[bench_ticker,ticker]].dropna()
        if len(pair) < max(40, n1+n2+tail+5):
            continue
'''
new='''        pair = prices[[bench_ticker,ticker]].dropna()
        # Was max(40, n1+n2+tail+5) -- sized for the old ratio-of-SMA formula.
        # The z-score formula's rs_std/roc_std each need RRG_STD_WINDOW valid
        # observations before producing a single non-NaN point, and momentum
        # needs that satisfied twice in sequence (ratio's std window, then
        # roc's std window on top of it) -- roughly 2*RRG_STD_WINDOW points
        # before the FIRST valid momentum value exists, before even counting
        # the requested tail length.
        min_needed = max(2*RRG_STD_WINDOW + tail + 10, n1+n2+tail+5)
        if len(pair) < min_needed:
            continue
'''
once(old,new,'warmup guard')

p.write_text(s)
print('patched app.py to v27.10')
