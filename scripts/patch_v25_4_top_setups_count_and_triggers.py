from pathlib import Path

p=Path('app.py')
s=p.read_text()

old=''' const rows=source.map(x=>({x,e:topSetupEvaluation(x)})).filter(z=>z.e.hardPass&&z.e.score>=55).sort((a,b)=>b.e.score-a.e.score).slice(0,2);'''
new=''' // Show a useful shortlist rather than only the top two. Keep the hard quality\n // gate, but surface up to six qualified names so the trader can compare setups.\n const qualified=source.map(x=>({x,e:topSetupEvaluation(x)})).filter(z=>z.e.hardPass&&z.e.score>=55).sort((a,b)=>b.e.score-a.e.score);\n const rows=qualified.slice(0,6);'''
if old not in s: raise SystemExit('Top Setups row-limit anchor not found')
s=s.replace(old,new,1)

old2=''' g.innerHTML=rows.map(({x,e},i)=>{const va=e.va,complete=setupCompleteness(x,e),label=e.score>=80&&va?.strength==="CONFIRMED"&&e.stratPass&&complete.complete?"A+ SETUP":"A-QUALITY WATCH",alignmentLabel=e.alignment==="EARLY"?"EARLY ALIGNMENT":"FULL ALIGNMENT",trigger=va?.direction==="bullish"?`Hold above VAH $${Number(va.vah).toFixed(2)}`:va?.direction==="bearish"?`Hold below VAL $${Number(va.val).toFixed(2)}`:va?`Watch VAH $${Number(va.vah).toFixed(2)} / VAL $${Number(va.val).toFixed(2)}`:"Load chart for VAH / VAL";return `'''
new2=''' g.innerHTML=rows.map(({x,e},i)=>{\n const va=e.va,complete=setupCompleteness(x,e),label=e.score>=80&&va?.strength==="CONFIRMED"&&e.stratPass&&complete.complete?"A+ SETUP":"A-QUALITY WATCH",alignmentLabel=e.alignment==="EARLY"?"EARLY ALIGNMENT":"FULL ALIGNMENT";\n // Trigger must be actionable from CURRENT price. A historical VAH/VAL that price\n // has already cleared by a meaningful amount is context, not a fresh entry trigger.\n const opt=optionScanMap[x.ticker]||{};\n const spot=Number(opt.spot??va?.close);\n const vah=Number(va?.vah),val=Number(va?.val);\n let trigger="Load chart for VAH / VAL";\n if(va){\n   if(va.direction==="bullish"&&Number.isFinite(vah)){\n     const ext=Number.isFinite(spot)&&spot>0?(spot-vah)/vah*100:null;\n     trigger=ext!=null&&ext>1.0\n       ?`Already ${ext.toFixed(1)}% above VAH $${vah.toFixed(2)} · wait for retest/hold or new base`\n       :`Hold above VAH $${vah.toFixed(2)}`;\n   }else if(va.direction==="bearish"&&Number.isFinite(val)){\n     const ext=Number.isFinite(spot)&&spot>0?(val-spot)/val*100:null;\n     trigger=ext!=null&&ext>1.0\n       ?`Already ${ext.toFixed(1)}% below VAL $${val.toFixed(2)} · wait for retest/rejection or new base`\n       :`Hold below VAL $${val.toFixed(2)}`;\n   }else if(Number.isFinite(vah)&&Number.isFinite(val)){\n     trigger=`Watch VAH $${vah.toFixed(2)} / VAL $${val.toFixed(2)}`;\n   }\n }\n return `'''
if old2 not in s: raise SystemExit('Top Setups trigger anchor not found')
s=s.replace(old2,new2,1)

# Increase finalist resolution so six cards can survive the final quality gate more often.
s=s.replace('''   }).slice(0,10);''','''   }).slice(0,16);''',1)

s=s.replace('APP_VERSION = "25.3"','APP_VERSION = "25.4"',1)
p.write_text(s)
