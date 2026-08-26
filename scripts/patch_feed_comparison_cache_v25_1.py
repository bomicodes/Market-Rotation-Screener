from pathlib import Path

p = Path("app.py")
s = p.read_text()
old = '''@app.get("/api/feed-comparison/<ticker>")
def api_feed_comparison(ticker):
    try:
        if not ALPACA_API_KEY or not ALPACA_API_SECRET:
            return jsonify({"ok":False,"error":"Alpaca is not configured."}),400
        return jsonify({"ok":True,**feed_comparison_payload(ticker)})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500
'''
new = '''@app.get("/api/feed-comparison/<ticker>")
def api_feed_comparison(ticker):
    try:
        if not ALPACA_API_KEY or not ALPACA_API_SECRET:
            return jsonify({"ok":False,"error":"Alpaca is not configured."}),400
        key=f"feed-comparison-v1:{ticker.upper().strip()}"
        payload=cached(key,lambda:feed_comparison_payload(ticker),ttl=90)
        return jsonify({"ok":True,**payload})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500
'''
if old not in s:
    raise SystemExit("feed comparison endpoint marker not found")
s = s.replace(old, new, 1)
p.write_text(s)
print("patched feed comparison cache")
