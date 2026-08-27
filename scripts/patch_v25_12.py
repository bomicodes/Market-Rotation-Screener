from pathlib import Path

p=Path('app.py')
s=p.read_text()
if 'APP_VERSION = "25.11"' not in s:
    raise SystemExit('expected v25.11 not found')
s=s.replace('APP_VERSION = "25.11"','APP_VERSION = "25.12"',1)
start=s.index('def alpaca_option_daily_bars(')
end=s.index('\ndef _premium_support_metrics', start)
block=s[start:end]
block=block.replace(',"feed":ALPACA_OPTIONS_FEED','')
lines=block.splitlines()
out=[]
skip=0
for line in lines:
    if skip:
        skip-=1
        continue
    if 'if r.status_code in (401,403)' in line and 'params.get("feed")' in line:
        skip=2
        continue
    out.append(line)
block='\n'.join(out)
if 'params["feed"]' in block or '"feed":ALPACA_OPTIONS_FEED' in block:
    raise SystemExit('feed parameter still present in option history block')
s=s[:start]+block+s[end:]
p.write_text(s)

r=Path('README.txt')
rs=r.read_text()
note=('v25.12 — ALPACA OPTION HISTORY QUERY FIX\n'
      '- Removed the unsupported `feed` query parameter from `/v1beta1/options/bars`; Alpaca rejects it with HTTP 400.\n'
      '- Removed the obsolete OPRA-to-indicative retry for historical option bars.\n'
      '- Premium Support can now reach the historical-bars response instead of failing on request validation.\n\n')
if not rs.startswith('v25.12'):
    r.write_text(note+rs)
