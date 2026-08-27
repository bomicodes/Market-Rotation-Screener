import app as appmod

def _bars(lows, closes=None, highs=None):
    closes=closes or [x*1.08 for x in lows]
    highs=highs or [max(l,c)*1.12 for l,c in zip(lows,closes)]
    return [{"l":l,"c":c,"h":h,"o":c,"v":100,"t":f"2026-08-{i+1:02d}T20:00:00Z"} for i,(l,c,h) in enumerate(zip(lows,closes,highs))]

def test_repeated_floor_scores_as_support():
    lows=[.70,.68,.72,.69,.71,.67,.70,.68,.69,.70,.68,.69]
    closes=[.82,.78,.80,.76,.77,.74,.75,.73,.72,.74,.76,.82]
    m=appmod._premium_support_metrics(_bars(lows,closes),.76)
    assert m["available"]
    assert m["support_touches"]>=2
    assert m["distance_from_support_pct"]<20
    assert m["score"]>=60

def test_far_from_floor_not_called_at_support():
    lows=[.40,.42,.41,.43,.44,.45,.46,.47,.48,.50]
    m=appmod._premium_support_metrics(_bars(lows),1.20)
    assert m["available"]
    assert m["state"]=="AWAY FROM SUPPORT"
    assert m["distance_from_support_pct"]>50
