from pathlib import Path
p=Path('app.py')
s=p.read_text()
def once(old,new,label):
    global s
    n=s.count(old)
    if n!=1: raise SystemExit(f'{label}: expected 1 match, found {n}')
    s=s.replace(old,new,1)
once('APP_VERSION = "27.12"','APP_VERSION = "27.13"','version')
marker='''RRG_HISTORY_LEN = 200  # ~9-10 months of trading days for the main dashboard's
                        # timeline slider. Only the main sector/industry
                        # dashboard call passes this -- other rrg_rows/
                        # dual_rrg_rows callers (per-ticker deep-dives, sector
                        # drill-downs) leave history_len unset and see no
                        # payload growth.

'''
insert=marker+'''def despike_rs(rs, threshold_pct=4.0):
    """Correct isolated one-bar data glitches in a relative-strength series
    before they enter the RRG math.

    A single bad print, stale/delayed quote, or adjustment artifact can create
    an isolated spike that largely reverses on the next bar. This targets that
    specific jump-and-revert signature and replaces only the isolated middle
    point with the midpoint of its neighbors.
    """
    vals = np.array(rs.to_numpy(), copy=True)
    for i in range(1, len(vals) - 1):
        prev, cur, nxt = vals[i-1], vals[i], vals[i+1]
        if not (np.isfinite(prev) and np.isfinite(cur) and np.isfinite(nxt)):
            continue
        jump = (cur/prev - 1)*100 if prev else 0.0
        revert = (nxt/cur - 1)*100 if cur else 0.0
        if abs(jump) >= threshold_pct and np.sign(jump) != np.sign(revert) and abs(jump+revert) < abs(jump)*0.35:
            vals[i] = (prev+nxt)/2.0
    return pd.Series(vals, index=rs.index)

'''
once(marker,insert,'despike helper')
once('    rs = pd.Series((a / b) * 100.0, dtype=float)','    rs = despike_rs(pd.Series((a / b) * 100.0, dtype=float))','compute hook')
p.write_text(s)
print('patched app.py to v27.13')
