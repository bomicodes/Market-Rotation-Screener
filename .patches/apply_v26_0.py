from pathlib import Path
p=Path('app.py')
s=p.read_text()
s=s.replace('APP_VERSION = "25.37"','APP_VERSION = "26.0"',1)
marker='</script>\n"""'
assert marker in s
js=r'''

// -------------------- v26.0 Attention-First Trader Board --------------------
// Keep the full analytical stack underneath, but collapse the default Top Setup
// experience into independent domains: setup quality, tradeability, risk and plan.
const V26_BUDGET_KEY="v26MaxContractCost";
function v26Budget(){
  const raw=localStorage.getItem(V26_BUDGET_KEY);const n=Number(raw);
  return Number.isFinite(n)&&n>0?n:250;
}
function v26Money(v){const n=Number(v);return Number.isFinite(n)?`$${n.toFixed(0)}`:"—"}
function v26Decision(x){
  const e=topSetupEvaluation(x),c=(typeof institutionalContextMap!=="undefined")?institutionalContextMap[x.ticker]:null;
  const opt=optionScanMap[x.ticker]||{},pc=premiumSupportMap[x.ticker]?.best_contract||null;
  const budget=v26Budget(),mid=Number(pc?.mid),cost=Number.isFinite(mid)?mid*100:null;
  // Setup quality deliberately uses broad domains rather than adding every correlated indicator.
  const f=x.fast||x,t=x.trend||{},r20=c?.relative_strength?.["20"]||{};
  const rot=e.alignment==="FULL"?95:e.alignment==="EARLY"?88:60;
  const rs=(Number(r20.vs_spy)>0?50:20)+(Number(r20.vs_parent)>0?50:(c?.parent?20:35));
  const trend=Number(c?.structure?.trend_strength);const structure=Number.isFinite(trend)?Math.min(100,35+trend*16):65;
  const va=e.va;const confirmation=va?.strength==="CONFIRMED"?95:va?.strength==="DEVELOPING"?72:va?.strength==="REJECTION"?25:55;
  const setup=Math.round(.40*rot+.20*rs+.25*structure+.15*confirmation);
  let trade=0;
  trade+=opt.liquidity==="Liquid"?30:opt.liquidity==="Tradable"?24:8;
  if(cost!=null)trade+=cost<=budget?40:cost<=budget*1.25?20:4;else trade+=8;
  const ps=Number(pc?.premium_support_score);trade+=Number.isFinite(ps)?Math.max(3,Math.min(20,ps*.20)):8;
  trade+=opt.iv_state==="Cheap / Crushed"?10:opt.iv_state==="Normal"?7:opt.iv_state==="Juiced"?2:5;
  trade=Math.max(0,Math.min(100,Math.round(trade)));
  const s=c?.structure||{},cat=c?.catalyst||{},macro=c?.macro_risk||{};
  const risk=(cat.days_to_earnings!=null&&cat.days_to_earnings<=3)||macro.risk==="HIGH";
  let label="WATCH FOR ENTRY",cls="v26Watch";
  if(cost!=null&&cost>budget){label="GOOD SETUP · PREMIUM TOO EXPENSIVE";cls="v26Wait";}
  else if(setup>=80&&trade>=70&&!risk){label="A+ · TRADEABLE";cls="v26Go";}
  else if(setup>=72&&trade>=58&&!risk){label="A · WATCH / TRADEABLE";cls="v26Watch";}
  else if(risk){label="WAIT · EVENT RISK";cls="v26Wait";}
  else if(trade<50){label="WATCH · EXECUTION WEAK";cls="v26Wait";}
  return {e,c,opt,pc,budget,cost,setup,trade,label,cls,s,combined:Math.round(.68*setup+.32*trade)};
}
function v26EnsureControls(){
  const g=document.getElementById("topSetupsGrid");if(!g||document.getElementById("v26TraderBoard"))return;
  const board=document.createElement("div");board.id="v26TraderBoard";board.className="v26TraderBoard";
  board.innerHTML=`<div><b>2–5 MINUTE TRADER BOARD</b><div class="tiny">Underlying quality and option tradeability are scored separately. Deep confluence stays available below.</div></div><label>MAX CONTRACT <span>$</span><input id="v26BudgetInput" inputmode="numeric" type="number" min="25" step="25" value="${v26Budget()}"></label>`;
  g.parentNode.insertBefore(board,g);
  board.querySelector("#v26BudgetInput")?.addEventListener("change",e=>{const n=Math.max(25,Number(e.target.value)||250);localStorage.setItem(V26_BUDGET_KEY,String(n));e.target.value=n;renderTopSetups();});
}
function v26EnhanceTopSetups(){
  const g=document.getElementById("topSetupsGrid");if(!g)return;
  v26EnsureControls();
  const cards=[...g.querySelectorAll('[data-top-setup]')];
  const ranked=[];
  cards.forEach(card=>{
    const x=(globalTopSetupData||[]).find(z=>z.ticker===card.dataset.topSetup);if(!x)return;
    const d=v26Decision(x);ranked.push({card,d});
    card.querySelector('.v26DecisionStrip')?.remove();
    const strip=document.createElement('div');strip.className=`v26DecisionStrip ${d.cls}`;
    const contract=d.pc?`${d.pc.expiration||''} · ${Number(d.pc.strike).toFixed(0)}${String(d.pc.type||'').toLowerCase().startsWith('p')?'P':'C'} · ${d.cost==null?'—':v26Money(d.cost)}`:'premium loading / unavailable';
    const trigger=Number.isFinite(Number(d.s.trigger))?v26Money(d.s.trigger):'—',inv=Number.isFinite(Number(d.s.invalidation))?v26Money(d.s.invalidation):'—';
    strip.innerHTML=`<div class="v26DecisionTop"><b>${d.label}</b><span>${d.combined}</span></div><div class="v26ScoreRow"><span>SETUP QUALITY <b>${d.setup}/100</b></span><span>TRADEABILITY <b>${d.trade}/100</b></span></div><div class="v26Plan"><span>CONTRACT <b>${contract}</b></span><span>TRIGGER <b>${trigger}</b></span><span>INVALIDATION <b>${inv}</b></span></div>`;
    const head=card.querySelector('.topSetupHead');if(head?.nextSibling)card.insertBefore(strip,head.nextSibling);else card.prepend(strip);
    const inst=card.querySelector('.topSetupInstitutional');if(inst){inst.classList.add('v26DeepEvidence');if(!inst.closest('details')){const det=document.createElement('details');det.className='v26Why';const sum=document.createElement('summary');sum.textContent='Why? · full confluence evidence';inst.parentNode.insertBefore(det,inst);det.appendChild(sum);det.appendChild(inst);}}
  });
  ranked.sort((a,b)=>b.d.combined-a.d.combined).forEach(({card})=>g.appendChild(card));
}
const _renderTopSetupsV26=renderTopSetups;
renderTopSetups=function(){const out=_renderTopSetupsV26();try{v26EnhanceTopSetups()}catch(e){console.warn('v26 board',e)}return out};
const v26Style=document.createElement('style');v26Style.textContent=`
.v26TraderBoard{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;margin:8px 0 12px;border:1px solid #27445a;border-radius:11px;background:linear-gradient(135deg,#0b1923,#0a141d)}
.v26TraderBoard>b,.v26TraderBoard b{letter-spacing:.5px}.v26TraderBoard label{display:flex;align-items:center;gap:5px;font-size:10px;font-weight:800;color:#91a4b8;white-space:nowrap}.v26TraderBoard input{width:82px;padding:7px;background:#071018;border:1px solid #31516a;color:#eaf2f8;border-radius:7px}
.v26DecisionStrip{margin:9px 0;padding:11px;border-radius:9px;border:1px solid #2a4052;background:#09141d}.v26Go{border-left:4px solid #2edb71}.v26Watch{border-left:4px solid #4aa3ff}.v26Wait{border-left:4px solid #f59e0b}.v26DecisionTop,.v26ScoreRow,.v26Plan{display:flex;align-items:center;justify-content:space-between;gap:8px}.v26DecisionTop>b{font-size:12px}.v26DecisionTop>span{font-size:18px;font-weight:850}.v26ScoreRow{margin-top:8px;justify-content:flex-start}.v26ScoreRow span{font-size:9px;color:#8195a8}.v26ScoreRow b{font-size:12px;color:#edf5fb;margin-left:4px}.v26Plan{margin-top:9px;display:grid;grid-template-columns:1.5fr 1fr 1fr}.v26Plan span{font-size:8px;color:#7890a4}.v26Plan b{display:block;color:#dfeaf2;font-size:10px;margin-top:2px}.v26Why{margin-top:7px;border-top:1px solid #1b2b38;padding-top:7px}.v26Why summary{cursor:pointer;color:#7fa5c2;font-size:10px;font-weight:750}.v26DeepEvidence{margin-top:8px}
@media(max-width:760px){.v26TraderBoard{align-items:flex-start;flex-direction:column}.v26TraderBoard label{width:100%;justify-content:space-between}.v26Plan{grid-template-columns:1fr 1fr}.v26Plan span:first-child{grid-column:1/-1}.v26ScoreRow{justify-content:space-between}.v26DecisionTop{align-items:flex-start}.v26DecisionTop>b{max-width:78%}}
`;document.head.appendChild(v26Style);
setTimeout(()=>{try{v26EnsureControls();v26EnhanceTopSetups()}catch(e){}},0);
'''
s=s.replace(marker,js+'\n'+marker,1)
p.write_text(s)
