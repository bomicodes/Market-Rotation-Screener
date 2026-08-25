from pathlib import Path
import re

p = Path('app.py')
t = p.read_text()

# Replace the manually maintained version pill with a runtime placeholder.
t, n1 = re.subn(
    r'<span class="versionPill">v[^<]+</span>',
    '<span class="versionPill">{{APP_VERSION_PLACEHOLDER}}</span>',
    t,
    count=1,
)
if n1 != 1 and '{{APP_VERSION_PLACEHOLDER}}' not in t:
    raise SystemExit('version pill target not found')

old = '''@app.get("/")
def home():
    # Important: rendering the shell performs no external network requests.
    shell = "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='theme-color' content='#0b0e11'><meta name='apple-mobile-web-app-capable' content='yes'><meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'><title>Market Rotation Screener</title></head><body>" + str(HTML) + "</body></html>"
    return Response(shell, mimetype="text/html")'''

new = '''@app.get("/")
def home():
    # Important: rendering the shell performs no external network requests.
    # The version pill is substituted from APP_VERSION (the single source of
    # truth already used by /health and /api/diagnostics) rather than being a
    # second, manually-typed copy — a hand-edited literal here had drifted out
    # of sync with the real deployed version, showing a stale badge in the UI.
    page = str(HTML).replace("{{APP_VERSION_PLACEHOLDER}}", f"v{APP_VERSION}")
    shell = "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='theme-color' content='#0b0e11'><meta name='apple-mobile-web-app-capable' content='yes'><meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'><title>Market Rotation Screener</title></head><body>" + page + "</body></html>"
    return Response(shell, mimetype="text/html")'''

if old in t:
    t = t.replace(old, new, 1)
elif 'page = str(HTML).replace("{{APP_VERSION_PLACEHOLDER}}", f"v{APP_VERSION}")' not in t:
    raise SystemExit('home() shell target not found')

p.write_text(t)
