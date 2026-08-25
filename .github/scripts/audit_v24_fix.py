from pathlib import Path
import re

p=Path('app.py')
t=p.read_text()
t=t.replace('APP_VERSION = "24.0"','APP_VERSION = "24.0.1"',1)

old='''        base=_safe_float(r.get("spot")) or float(px["Close"].iloc[pos])
        for h in stats:
          if pos+h<len(px): stats[h].append((float(px["Close"].iloc[pos+h])/base-1)*100)
        end=min(len(px),pos+11)
        if end>pos+1:
          seg=px.iloc[pos+1:end]; mfe.append((float(seg["High"].max())/base-1)*100); mae.append((float(seg["Low"].min())/base-1)*100)'''
new='''        base=_safe_float(r.get("spot")) or float(px["Close"].iloc[pos])
        bias=str(r.get("bias") or "neutral").lower()
        direction_sign=-1.0 if bias.startswith("bear") else 1.0
        for h in stats:
          if pos+h<len(px):
            raw_ret=(float(px["Close"].iloc[pos+h])/base-1)*100
            stats[h].append(raw_ret*direction_sign)
        end=min(len(px),pos+11)
        if end>pos+1:
          seg=px.iloc[pos+1:end]
          if direction_sign>0:
            favorable=(float(seg["High"].max())/base-1)*100
            adverse=(float(seg["Low"].min())/base-1)*100
          else:
            favorable=-(float(seg["Low"].min())/base-1)*100
            adverse=-(float(seg["High"].max())/base-1)*100
          mfe.append(favorable); mae.append(adverse)'''
if old not in t: raise SystemExit('history block not found')
t=t.replace(old,new,1)

pattern=r'function gexImplicationFor\(ticker,ctx\)\{.*?\}\nfunction histExpectancyLabel'
repl='''function gexImplicationFor(ticker,ctx){const o=(activeOptionsData?.ticker===ticker)?activeOptionsData:optionScanMap[ticker],p=o?.positioning;if(!p?.available)return {label:"Pending",detail:"Load GEX/options positioning"};const dir=ctx?.structure?.direction||"neutral",reg=String(p.gamma_regime||"");let label=reg.includes("Negative")?"Amplifying regime":reg.includes("Positive")?"Dampening regime":"Mixed gamma";if(!["bullish","bearish"].includes(dir))return {label,detail:`${reg||"Dealer gamma available"} · no directional structure confirmed`,room:null};const spot=Number(o?.spot||ctx?.structure?.spot),wall=dir==="bearish"?Number(p.put_wall):Number(p.call_wall);let room=null;if(Number.isFinite(spot)&&spot>0&&Number.isFinite(wall))room=dir==="bearish"?(spot-wall)/spot*100:(wall-spot)/spot*100;let detail=reg||"Dealer gamma unavailable";if(room!=null)detail+=` · ${room.toFixed(1)}% room to ${dir==="bearish"?"put":"call"} wall`;if(reg.includes("Positive")&&room!=null&&room>=0&&room<=1.5)detail+=" · breakout headwind";else if(reg.includes("Negative")&&room!=null&&room>2)detail+=" · continuation can accelerate";return {label,detail,room}}
function histExpectancyLabel'''
t,n=re.subn(pattern,repl,t,count=1,flags=re.S)
if n!=1: raise SystemExit('gex function not found')

old='if(Number(c.rotation_persistence)>=80)score+=4;else if(Number(c.rotation_persistence)<50)score-=4;'
new='if(c.rotation_persistence!=null&&Number(c.rotation_persistence)>=80)score+=4;else if(c.rotation_persistence!=null&&Number(c.rotation_persistence)<50)score-=4;'
if old not in t: raise SystemExit('persistence scoring not found')
t=t.replace(old,new,1)

old='["Catalyst",cat.days_to_earnings!=null&&cat.days_to_earnings<=3?1:cat.days_to_earnings!=null&&cat.days_to_earnings<=10?5:9]'
new='["Catalyst",cat.days_to_earnings==null?5:cat.days_to_earnings<=3?1:cat.days_to_earnings<=10?5:9]'
if old not in t: raise SystemExit('catalyst factor not found')
t=t.replace(old,new,1)
t=t.replace("'No confirmed upcoming earnings'","'No confirmed earnings date available'",1)

p.write_text(t)
