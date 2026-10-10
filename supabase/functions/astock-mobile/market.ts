import {exchangeTime, timeWindow, historicalCutoff, completeWindow, prepareTicks} from './tick-window.ts';
export const SCORE_STANDARD = {version:'direction-standard-v1',up_min:15,down_max:-15,medium_strength:40,strong_strength:70,required_support:3,history_samples:4};
export function last(a){ return Array.isArray(a)&&a.length?a[a.length-1]:null; }
export function n(v,d=0){ const x=Number(v); return Number.isFinite(x)?x:d; }
export function arr(v){ return Array.isArray(v)?v.map(Number).filter(Number.isFinite):[]; }
function clamp(x,a,b){ return Math.max(a,Math.min(b,x)); }
function norm(x,s){ return s>0?clamp(x/s,-1,1):0; }
function median(a){ const x=a.filter(Number.isFinite).slice().sort((p,q)=>p-q); if(!x.length)return 0; const m=Math.floor(x.length/2); return x.length%2?x[m]:(x[m-1]+x[m])/2; }
export function marketOpen(){ const d=new Date(Date.now()+8*3600*1000),day=d.getUTCDay(); if(day===0||day===6)return false; const m=d.getUTCHours()*60+d.getUTCMinutes(); return (m>=570&&m<690)||(m>=780&&m<900); }
const tickTs=exchangeTime;
const windowTicks=timeWindow;
function momentumPct(ticks,seconds){ const p=windowTicks(ticks,seconds);if(p.length<2)return 0;const p0=n(p[0]?.lastPrice,0),p1=n(last(p)?.lastPrice,0);return p0>0&&p1>0?(p1/p0-1)*100:0; }
export function coverageSeconds(ticks){ if(!ticks||ticks.length<2)return 0;const a=tickTs(ticks[0]),b=tickTs(last(ticks));return a>0&&b>=a?(b-a)/1000:0; }
function best(t){ const bp=arr(t?.bidPrice),ap=arr(t?.askPrice),bv=arr(t?.bidVol),av=arr(t?.askVol);return{bid1:n(bp[0]??t?.bidPrice1,0),ask1:n(ap[0]??t?.askPrice1,0),bv1:n(bv[0]??t?.bidVol1,0),av1:n(av[0]??t?.askVol1,0)}; }
function bookPressure(t){ const bv=arr(t?.bidVol).slice(0,5),av=arr(t?.askVol).slice(0,5),w=[1,.78,.58,.40,.25];let b=0,a=0;for(let i=0;i<5;i++){b+=(bv[i]||0)*w[i];a+=(av[i]||0)*w[i];}return b+a>0?100*b/(b+a):50; }
function avgBook(ticks,seconds){ const p=windowTicks(ticks,seconds);return p.length?p.reduce((s,t)=>s+bookPressure(t),0)/p.length:50; }
function recentVwap(ticks,seconds=60){ const p=windowTicks(ticks,seconds);if(p.length<2)return 0;const v0=n(p[0]?.volume,0),v1=n(last(p)?.volume,0),a0=n(p[0]?.amount,0),a1=n(last(p)?.amount,0),dv=Math.max(0,v1-v0),da=Math.max(0,a1-a0);return dv>0&&da>0?da/(dv*100):0; }
function tradeFlow(ticks,seconds){ const p=windowTicks(ticks,seconds);let buy=0,sell=0,events=0,prevSign=0;for(let i=1;i<p.length;i++){const dv=Math.max(0,n(p[i]?.volume,0)-n(p[i-1]?.volume,0));if(dv<=0)continue;events++;const price=n(p[i]?.lastPrice,0),prevPrice=n(p[i-1]?.lastPrice,0),q=best(p[i-1]);let sign=0;if(q.ask1>0&&price>=q.ask1)sign=1;else if(q.bid1>0&&price<=q.bid1)sign=-1;else if(price>prevPrice)sign=1;else if(price<prevPrice)sign=-1;else sign=prevSign;prevSign=sign;if(sign>0)buy+=dv;else if(sign<0)sell+=dv;}const tot=buy+sell;return{buy_pct:tot>0?100*buy/tot:50,events}; }
function scoreCore(ticks){ const latest=last(ticks)||{},price=n(latest.lastPrice,0),r10=momentumPct(ticks,10),r30=momentumPct(ticks,30),r60=momentumPct(ticks,60),r120=momentumPct(ticks,120),q=best(latest),spreadPct=price>0&&q.ask1>=q.bid1&&q.bid1>0?(q.ask1-q.bid1)/price*100:0,noise=Math.max(.02,spreadPct*1.5),buyPressure=avgBook(ticks,12),priorWindow=windowTicks(ticks,35),recent=windowTicks(ticks,12),prior=priorWindow.length>recent.length?priorWindow.slice(0,priorWindow.length-recent.length):priorWindow,priorPressure=prior.length?prior.reduce((s,t)=>s+bookPressure(t),0)/prior.length:buyPressure,pressureChange=buyPressure-priorPressure,flow20=tradeFlow(ticks,20),flow60=tradeFlow(ticks,60),eventConf=clamp(flow60.events/8,0,1),rvwap=recentVwap(ticks,60),aboveVwap=price>0&&rvwap>0?(price/rvwap-1)*100:0;let micro=0;if(q.bid1>0&&q.ask1>0&&q.bv1+q.av1>0&&price>0){const mp=(q.ask1*q.bv1+q.bid1*q.av1)/(q.bv1+q.av1);micro=(mp/price-1)*100;} const m60=25*(.20*norm(r10,Math.max(.06,noise*1.8))+.35*norm(r30,Math.max(.10,noise*3))+.45*norm(r60,Math.max(.16,noise*4.5))),m120=25*(.25*norm(r30,Math.max(.12,noise*3.2))+.35*norm(r60,Math.max(.20,noise*5))+.40*norm(r120,Math.max(.30,noise*7))),f20=norm(flow20.buy_pct-50,25),f60=norm(flow60.buy_pct-50,25),fg60=25*eventConf*(.65*f20+.35*f60),fg120=25*eventConf*(.35*f20+.65*f60),bl=norm(buyPressure-50,25),bc=norm(pressureChange,20),book60=15*(.70*bl+.30*bc),book120=15*(.65*bl+.35*bc),vs=rvwap>0?norm(aboveVwap,Math.max(.10,noise*3.5)):0,ms=norm(micro,Math.max(.03,noise*.75)),loc60=15*(.75*vs+.25*ms),loc120=10*(.85*vs+.15*ms);let ex60=0,ex120=0;const stretch=Math.max(.18,noise*5); if(r60>=Math.max(.12,noise*4)&&r30<=r60*.45&&r10<=0)ex60-=6;else if(r60<=-Math.max(.12,noise*4)&&r30>=r60*.45&&r10>=0)ex60+=6;if(r120>=Math.max(.18,noise*6)&&r60<=r120*.50&&r30<=0)ex120-=6;else if(r120<=-Math.max(.18,noise*6)&&r60>=r120*.50&&r30>=0)ex120+=6; if(Math.max(r30,r60)>noise*2&&flow60.buy_pct>=55&&flow20.buy_pct<=flow60.buy_pct-15){ex60-=8;ex120-=6;}else if(Math.min(r30,r60)<-noise*2&&flow60.buy_pct<=45&&flow20.buy_pct>=flow60.buy_pct+15){ex60+=8;ex120+=6;}if(r30>noise*1.5&&(pressureChange<=-12||buyPressure<=42)){ex60-=5;ex120-=4;}else if(r30<-noise*1.5&&(pressureChange>=12||buyPressure>=58)){ex60+=5;ex120+=4;}if(aboveVwap>=stretch&&r10<=0){ex60-=5;ex120-=4;}else if(aboveVwap<=-stretch&&r10>=0){ex60+=5;ex120+=4;}ex60=clamp(ex60,-20,20);ex120=clamp(ex120,-18,18); const groups60={momentum:m60,flow:fg60,book:book60,location:loc60,exhaustion:ex60},groups120={momentum:m120,flow:fg120,book:book120,location:loc120,exhaustion:ex120},raw60=Math.round(clamp(Object.values(groups60).reduce((a,b)=>a+b,0),-100,100)),raw120=Math.round(clamp(Object.values(groups120).reduce((a,b)=>a+b,0),-100,100));return{price,score60_core:raw60,score120_core:raw120,groups60,groups120,r10,r30,r60,r120,buy_pct:flow60.buy_pct,buy_pct_20s:flow20.buy_pct,flow_events:flow60.events,buy_pressure_pct:buyPressure,pressure_change_pct:pressureChange,recent_vwap:rvwap,above_vwap_pct:aboveVwap,microprice_bias_pct:micro,noise_pct:noise,exhaustion_60:ex60,exhaustion_120:ex120}; }
export function stableModel(input){
  const health=prepareTicks(input),ticks=health.ticks,current=scoreCore(ticks);
  function samples(horizon,offsets){
    return offsets.map(offset=>{
      const part=historicalCutoff(ticks,offset);
      return completeWindow(part,horizon)?scoreCore(part)[`score${horizon}_core`]:null;
    });
  }
  const s60=samples(60,[0,5,10,15]),s120=samples(120,[0,10,20,30]);
  function result(values){
    const valid=values.filter(x=>x!==null),up=valid.filter(x=>x>=SCORE_STANDARD.up_min).length,
      down=valid.filter(x=>x<=SCORE_STANDARD.down_max).length,ready=valid.length===4;
    let score=ready?Math.round(median(valid)):0;
    if((score>=15&&up<3)||(score<=-15&&down<3))score=0;
    return{score,ready,support:{up,down},dispersion:ready?Math.max(...valid)-Math.min(...valid):null};
  }
  const a=result(s60),b=result(s120);
  return{...current,score60:a.score,score120:b.score,ready60:a.ready,ready120:b.ready,
    score60_raw_current:current.score60_core,score120_raw_current:current.score120_core,
    score60_samples:s60,score120_samples:s120,score60_dispersion:a.dispersion,score120_dispersion:b.dispersion,
    direction_support_60:a.support,direction_support_120:b.support,
    window_health:{...health,ticks:undefined,valid_tick_count:ticks.length,coverage_seconds:coverageSeconds(ticks)}};
}
function macdSignal(tf){ const x=Number(tf?.state_score); return Number.isFinite(x)?clamp(x/100,-1,1):null; }
function macdTransition(tf){ const x=Number(tf?.transition_score); return Number.isFinite(x)?clamp(x,-1,1):null; }
function weightedContext(ctx,dir,transition=false){ const weights=transition?{m1:.45,m5:.35,m15:.20}:{m1:.24,m5:.30,m15:.24,m30:.12,m60:.07,day:.02,week:.01};let s=0,w=0; for(const [k,wt] of Object.entries(weights)){const tf=ctx?.timeframes?.[k];const v=transition?macdTransition(tf):macdSignal(tf);if(v===null)continue;s+=wt*v;w+=wt;} return w>0?clamp(dir*s/w,-1,1):0; }
function groupAlignment(groups,dir){ const caps={momentum:25,flow:25,book:15,location:15};let s=0,w=0;for(const [k,cap] of Object.entries(caps)){const v=Number(groups?.[k]);if(!Number.isFinite(v))continue;s+=cap*clamp(dir*v/cap,-1,1);w+=cap;}return w?clamp(s/w,-1,1):0; }
function confidenceLabel(c){ if(c>=85)return'顶级候选｜待样本验证';if(c>=75)return'强';if(c>=60)return'较高';if(c>=45)return'一般';return'低'; }
export function confidenceFor(model,ctx,horizon=60){
  const score=horizon===120?model.score120:model.score60,dir=score>=15?1:score<=-15?-1:0;
  if(!dir)return{score:0,label:'中性',direction:'WATCH',calibrated_probability:false,empirical:false};
  const supportObj=horizon===120?model.direction_support_120:model.direction_support_60,
    support=(dir>0?n(supportObj?.up,0):n(supportObj?.down,0))/4,
    dispersion=horizon===120?model.score120_dispersion:model.score60_dispersion,
    disp=dispersion==null?100:n(dispersion,100),
    dispQ=1-clamp(disp/(horizon===120?70:55),0,1),strength=clamp(Math.abs(score)/40,0,1),
    groups=horizon===120?model.groups120:model.groups60,gAlign=groupAlignment(groups,dir);
  const age=ctx?.freshness_seconds,contextFresh=age!=null&&Number.isFinite(Number(age))
    &&Number(age)>=0&&Number(age)<=120,
    mAlign=contextFresh?weightedContext(ctx,dir,false):0,
    tAlign=contextFresh?weightedContext(ctx,dir,true):0,
    ex=n(horizon===120?model.exhaustion_120:model.exhaustion_60,0),risk=clamp(-dir*ex/(horizon===120?18:20),0,1);
  let c=30+10*strength+16*support+10*dispQ+14*gAlign+16*mAlign+6*tAlign-16*risk;
  if(!contextFresh)c-=6;
  c=Math.round(clamp(c,0,100));
  return{score:c,label:confidenceLabel(c),direction:dir>0?'UP':'DOWN',
    direction_strength:Math.round(strength*100),persistence:Math.round(support*100),
    dispersion_quality:Math.round(dispQ*100),group_alignment:Number(gAlign.toFixed(3)),
    macd_alignment:Number(mAlign.toFixed(3)),macd_transition_alignment:Number(tAlign.toFixed(3)),
    macd_context_used:contextFresh,exhaustion_risk:Number(risk.toFixed(3)),
    calibrated_probability:false,empirical:false};
}
export function scoreView(score){const s=Number(score),r=SCORE_STANDARD;if(s>=r.strong_strength)return{direction:'UP',label:'偏涨｜较强'};if(s>=r.medium_strength)return{direction:'UP',label:'偏涨｜中等'};if(s>=r.up_min)return{direction:'UP',label:'轻微偏涨'};if(s<=-r.strong_strength)return{direction:'DOWN',label:'偏跌｜较强'};if(s<=-r.medium_strength)return{direction:'DOWN',label:'偏跌｜中等'};if(s<=r.down_max)return{direction:'DOWN',label:'轻微偏跌'};return{direction:'WATCH',label:'震荡｜中性'};}
export function forecast(score,ready,open,fresh,modelVersion,confidence){if(!open)return{direction:'WATCH',label:'休市',agreement:Math.abs(score),high_confidence:false,locked:false,confidence_score:0};if(!fresh)return{direction:'WATCH',label:'数据延迟',agreement:Math.abs(score),high_confidence:false,locked:false,confidence_score:0};if(!ready)return{direction:'WATCH',label:'数据补齐中',agreement:Math.abs(score),high_confidence:false,locked:false,confidence_score:0};const v=scoreView(score);return{...v,agreement:Math.abs(score),high_confidence:false,strong_signal:Math.abs(score)>=70,calibrated:false,locked:false,remaining_seconds:0,source:modelVersion,confidence_score:n(confidence?.score,0),confidence_label:confidence?.label||'--',confidence_empirical:false};}
