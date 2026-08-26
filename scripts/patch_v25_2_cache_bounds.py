from pathlib import Path

p=Path('app.py')
s=p.read_text()
orig=s

s=s.replace('APP_VERSION = "25.1"','APP_VERSION = "25.2"',1)

old='''CACHE = {}
CACHE_TTL = 60 * 15
_CACHE_LOCKS = {}
_CACHE_LOCKS_GUARD = threading.Lock()

def _cache_lock(key):
    # One lock per cache key so unrelated keys never block each other.
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CACHE_LOCKS[key] = lock
        return lock
'''
new='''CACHE = {}
CACHE_TTL = 60 * 15
# Keep the in-process cache bounded. Options/flow payloads can be large, and an
# unbounded dict lets normal ticker exploration slowly push a small Render
# instance toward its memory limit. Oldest entries are evicted first.
CACHE_MAX_ENTRIES = max(20, int(os.environ.get("CACHE_MAX_ENTRIES", "80")))
_CACHE_LOCKS = {}
_CACHE_LOCKS_GUARD = threading.Lock()

def _trim_cache(force=False):
    max_entries=CACHE_MAX_ENTRIES
    if not force and len(CACHE) <= max_entries:
        return 0
    target=max(1, int(max_entries * 0.80))
    remove_n=max(0, len(CACHE)-target)
    if remove_n <= 0:
        return 0
    def _stamp(item):
        try:return float(item[1][0])
        except Exception:return 0.0
    victims=sorted(CACHE.items(), key=_stamp)[:remove_n]
    for k,_ in victims:
        CACHE.pop(k,None)
    return len(victims)

def _cache_lock(key):
    # One lock per cache key so unrelated keys never block each other. Prune
    # before allocating another lock so both cached payloads and lock metadata
    # stay bounded over long-running sessions.
    _trim_cache()
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CACHE_LOCKS[key] = lock
        if len(_CACHE_LOCKS) > CACHE_MAX_ENTRIES * 2:
            for old_key in list(_CACHE_LOCKS):
                if old_key == key or old_key in CACHE:
                    continue
                old_lock=_CACHE_LOCKS.get(old_key)
                if old_lock is not None and not old_lock.locked():
                    _CACHE_LOCKS.pop(old_key,None)
                if len(_CACHE_LOCKS) <= CACHE_MAX_ENTRIES * 2:
                    break
        return lock
'''
if old not in s:
    raise SystemExit('cache block marker not found')
s=s.replace(old,new,1)

if s==orig:
    raise SystemExit('no changes made')
p.write_text(s)
print('patched bounded cache and v25.2')
