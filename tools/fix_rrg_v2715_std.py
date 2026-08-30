from pathlib import Path
p=Path('app.py')
s=p.read_text()
old='''    rs_mean = rs.rolling(n1).mean()
    rs_std = rs.rolling(max(std_window, n1)).std(ddof=1).replace(0, np.nan)
    ratio = 100.0 + (rs - rs_mean) / rs_std

    roc = ratio.pct_change() * 100.0
    roc_mean = roc.rolling(n2).mean()
    roc_std = roc.rolling(max(std_window, n2)).std(ddof=1).replace(0, np.nan)
'''
new='''    std_window = max(2, int(std_window))
    rs_mean = rs.rolling(n1).mean()
    rs_std = rs.rolling(std_window).std(ddof=1).replace(0, np.nan)
    ratio = 100.0 + (rs - rs_mean) / rs_std

    roc = ratio.pct_change() * 100.0
    roc_mean = roc.rolling(n2).mean()
    roc_std = roc.rolling(std_window).std(ddof=1).replace(0, np.nan)
'''
if s.count(old)!=1: raise SystemExit(f'expected 1 std block, got {s.count(old)}')
s=s.replace(old,new,1)
p.write_text(s)
print('fixed exact timeframe std window')
