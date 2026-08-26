from pathlib import Path
import re

p=Path('app.py')
s=p.read_text()

# Backend: never return a structurally impossible directional trade plan.
# We validate trigger/invalidation/targets before the object can reach scoring/UI.
needle='''    r20=_ctx_return(c,20);r50=_ctx_return(c,50);trend_strength=int(spot>sma20)+int(sma20>sma50)+int(r20 is not None and r20>0)+int(r50 is not None and r50>0)\n    return {"available":True,"spot":round(spot,2),"atr14":round(atr,2),"direction":direction,"trend_strength":trend_strength,"sma20":round(sma20,2),"sma50":round(sma50,2),"trigger":round(trigger,2) if trigger is not None else None,"confirmation":round(confirmation,2) if confirmation is not None else None,"invalidation":round(invalidation,2) if invalidation is not None else None,"hard_fail":round(hard_fail,2) if hard_fail is not None else None,"target1":round(target1,2) if target1 is not None else None,"target2":round(target2,2) if target2 is not None else None,"rr_to_target2":round(reward/risk,2) if risk else None,"return_20d":round(r20,2) if r20 is not None else None,"return_50d":round(r50,2) if r50 is not None else None}\n'''
replacement='''    # Integrity gate: directional plans must have levels in the correct order.\n    plan_valid=True;plan_error=None\n    if direction=="bullish" and trigger is not None:\n        plan_valid=(invalidation is not None and target1 is not None and target2 is not None and invalidation < trigger < target1 <= target2)\n        if not plan_valid: plan_error="Invalid bullish level ordering"\n    elif direction=="bearish" and trigger is not None:\n        plan_valid=(invalidation is not None and target1 is not None and target2 is not None and invalidation > trigger > target1 >= target2)\n        if not plan_valid: plan_error="Invalid bearish level ordering"\n    if not plan_valid:\n        trigger=confirmation=invalidation=hard_fail=target1=target2=None;risk=reward=None\n    r20=_ctx_return(c,20);r50=_ctx_return(c,50);trend_strength=int(spot>sma20)+int(sma20>sma50)+int(r20 is not None and r20>0)+int(r50 is not None and r50>0)\n    return {"available":True,"spot":round(spot,2),"atr14":round(atr,2),"direction":direction,"trend_strength":trend_strength,"sma20":round(sma20,2),"sma50":round(sma50,2),"trigger":round(trigger,2) if trigger is not None else None,"confirmation":round(confirmation,2) if confirmation is not None else None,"invalidation":round(invalidation,2) if invalidation is not None else None,"hard_fail":round(hard_fail,2) if hard_fail is not None else None,"target1":round(target1,2) if target1 is not None else None,"target2":round(target2,2) if target2 is not None else None,"rr_to_target2":round(reward/risk,2) if risk else None,"plan_valid":plan_valid,"plan_error":plan_error,"return_20d":round(r20,2) if r20 is not None else None,"return_50d":round(r50,2) if r50 is not None else None}\n'''
if needle not in s: raise SystemExit('structure return anchor not found')
s=s.replace(needle,replacement,1)

# Frontend scoring: an invalid/missing structural plan cannot receive structure/execution credit.
needle2='''function setupCompleteness(x,e){\n const c=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null,opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],st=stratSignalMap[x.ticker];\n const checks={RRG:!!(e?.alignment&&e.alignment!=="NONE"),Value:!!va,STRAT:!!st,Options:!!opt,Context:!!c,Catalyst:!!(c?.catalyst&&c.catalyst.risk!=="Unknown"),Macro:!!c?.macro_risk};\n'''
replacement2='''function setupCompleteness(x,e){\n const c=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null,opt=optionScanMap[x.ticker],va=valueAcceptanceMap[x.ticker],st=stratSignalMap[x.ticker];\n const structure=c?.structure||{};\n const planOk=structure.plan_valid!==false && Number.isFinite(Number(structure.trigger)) && Number.isFinite(Number(structure.invalidation)) && Number.isFinite(Number(structure.target2));\n const checks={RRG:!!(e?.alignment&&e.alignment!=="NONE"),Value:!!va,STRAT:!!st,Options:!!opt,Context:!!c,TradePlan:planOk,Catalyst:!!(c?.catalyst&&c.catalyst.risk!=="Unknown"),Macro:!!c?.macro_risk};\n'''
if needle2 not in s: raise SystemExit('completeness anchor not found')
s=s.replace(needle2,replacement2,1)

# Top Setup cards: use current price-aware trigger text and explicitly suppress stale/invalid structure plans.
old=''' const opt=optionScanMap[x.ticker]||{};\n const spot=Number(opt.spot??va?.close);\n const vah=Number(va?.vah),val=Number(va?.val);\n let trigger="Load chart for VAH / VAL";'''
new=''' const opt=optionScanMap[x.ticker]||{};\n const ctx=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null;\n const structure=ctx?.structure||{};\n const spot=Number(opt.spot??va?.close??structure.spot);\n const vah=Number(va?.vah),val=Number(va?.val);\n let trigger="Load chart for VAH / VAL";\n if(structure.plan_valid===false) trigger="Trade plan invalidated by level-integrity check · re-resolve structure";'''
if old not in s: raise SystemExit('trigger spot anchor not found')
s=s.replace(old,new,1)
# Don't overwrite the integrity warning with VA logic.
s=s.replace(''' if(va){\n   if(va.direction==="bullish"''',''' if(va && structure.plan_valid!==false){\n   if(va.direction==="bullish"''',1)

# Hard-pass must not promote a known-invalid structural plan.
old3=''' const hardPass=rrgPass && !fOut && !tOut &&\n   (liq==="Liquid"||liq==="Tradable") &&\n   va?.strength!=="REJECTION";'''
new3=''' const ctx=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null;\n const structureOk=ctx?.structure?.plan_valid!==false;\n const hardPass=rrgPass && !fOut && !tOut &&\n   (liq==="Liquid"||liq==="Tradable") &&\n   va?.strength!=="REJECTION" && structureOk;'''
if old3 not in s: raise SystemExit('hardPass anchor not found')
s=s.replace(old3,new3,1)

s=s.replace('APP_VERSION = "25.4"','APP_VERSION = "25.5"',1)
p.write_text(s)
print('v25.5 trade-plan integrity patch applied')
