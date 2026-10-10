import {BRIDGE_ID,MODEL_VERSION,cors,json,makeSession,rest,validPassword,validSessionToken} from './auth.ts';
import {arr,forecast,last,marketOpen,n,stableModel,confidenceFor,SCORE_STANDARD} from './market.ts';
import {getMacdContext} from './macd.ts';
import {feedHealth} from './feed-health.ts';
import {learningState} from './learning.ts';
import {validQuote,exchangeTime} from './tick-window.ts';
import {evaluationState,recordAndSettle} from './evaluation.ts';

function symbolFromCode(code) {
  if(code.startsWith('6')||code.startsWith('5')||code.startsWith('9')) return code+'.SH';
  if(code.startsWith('4')||code.startsWith('8')) return code+'.BJ';
  return code+'.SZ';
}

export async function handler(req) {
  try {
    const url=new URL(req.url), path=url.pathname;
    if(req.method==='OPTIONS') return new Response(null,{status:204,headers:cors(req)});
    if(path.endsWith('/health')) return json(req,{ok:true,service:'astock-mobile-api',version:12,
      model:MODEL_VERSION,direction_confidence_split:true,macd_structure_calibration:true,
      calibrated_probability:false,exchange_timestamp_guard:true,learning_status:true,
      strict_time_windows:true,prospective_prediction_log:true});
    if(path.endsWith('/login')&&req.method==='POST') {
      const body=await req.json().catch(()=>({}));
      if(!await validPassword(String(body.password||''))) return json(req,{ok:false},401);
      return json(req,{ok:true,session:await makeSession()});
    }
    if(path.endsWith('/')) return json(req,{ok:true,service:'astock-mobile-api',frontend:'GitHub Pages',version:12});
    if(!await validSessionToken(req.headers.get('x-astock-session')||'')) return json(req,{error:'unauthorized'},401);
    if(path.endsWith('/learning')) return json(req,await learningState(rest));
    if(path.endsWith('/evaluation')) return json(req,await evaluationState(rest,BRIDGE_ID,MODEL_VERSION));
    if(path.endsWith('/switch')&&req.method==='POST') {
      const body=await req.json().catch(()=>({})), code=String(body.code||'').trim();
      if(!/^\d{6}$/.test(code)) return json(req,{error:'bad code'},400);
      const symbol=symbolFromCode(code), now=new Date().toISOString();
      await rest('qmt_watch_requests?on_conflict=bridge_id',{method:'POST',
        headers:{Prefer:'resolution=merge-duplicates,return=minimal'},
        body:JSON.stringify({bridge_id:BRIDGE_ID,symbol,requested_at:now})});
      return json(req,{ok:true,symbol,requested_at:now});
    }
    if(path.endsWith('/context')) {
      const watch=await rest(`qmt_watch_requests?bridge_id=eq.${encodeURIComponent(BRIDGE_ID)}&select=symbol&limit=1`);
      const symbol=(Array.isArray(watch)&&watch[0]?.symbol)||'000400.SZ';
      return json(req,{ok:true,...await getMacdContext(symbol,url.searchParams.get('force')==='1')});
    }
    if(path.endsWith('/state')) {
      const watch=await rest(`qmt_watch_requests?bridge_id=eq.${encodeURIComponent(BRIDGE_ID)}&select=symbol,requested_at&limit=1`);
      const symbol=(Array.isArray(watch)&&watch[0]?.symbol)||'000400.SZ';
      const requestedAt=(Array.isArray(watch)&&watch[0]?.requested_at)||null;
      // Real-time requests read cached MACD only; the separate context route refreshes it.
      const [liveRows,l2Rows,ctx]=await Promise.all([
        rest(`qmt_live_cache?bridge_id=eq.${encodeURIComponent(BRIDGE_ID)}&symbol=eq.${encodeURIComponent(symbol)}&select=symbol,status,updated_at,ticks&limit=1`),
        rest(`qmt_l2_cache?bridge_id=eq.${encodeURIComponent(BRIDGE_ID)}&symbol=eq.${encodeURIComponent(symbol)}&select=status,updated_at,summary,capabilities&limit=1`),
        getMacdContext(symbol,false,false)
      ]);
      const live=Array.isArray(liveRows)?liveRows[0]:null;
      const l2=Array.isArray(l2Rows)?l2Rows[0]:null;
      const ticks=Array.isArray(live?.ticks)?live.ticks:[], tick=last(ticks)||{};
      const price=n(tick.lastPrice,NaN), prev=n(tick.lastClose,NaN), volume=n(tick.volume), amount=n(tick.amount);
      const open=n(tick.open,NaN), high=n(tick.high,NaN), low=n(tick.low,NaN);
      const pct=Number.isFinite(price)&&Number.isFinite(prev)&&prev>0?(price/prev-1)*100:NaN;
      const vwap=volume>0&&amount>0?amount/(volume*100):NaN;
      const vwapd=Number.isFinite(vwap)&&vwap>0&&Number.isFinite(price)?(price/vwap-1)*100:NaN;
      const bp=arr(tick.bidPrice),ap=arr(tick.askPrice),bv=arr(tick.bidVol),av=arr(tick.askVol);
      const bs=bv.reduce((a,b)=>a+b,0),as=av.reduce((a,b)=>a+b,0),book=bs+as>0?100*bs/(bs+as):NaN;
      const bid1=n(bp[0]??tick.bidPrice1,NaN),ask1=n(ap[0]??tick.askPrice1,NaN);
      const spread=Number.isFinite(bid1)&&Number.isFinite(ask1)&&ask1>=bid1?ask1-bid1:NaN;
      const mid=Number.isFinite(bid1)&&Number.isFinite(ask1)?(bid1+ask1)/2:NaN;
      const spreadBp=Number.isFinite(spread)&&Number.isFinite(mid)&&mid>0?spread/mid*10000:NaN;
      const rangePos=Number.isFinite(price)&&Number.isFinite(high)&&Number.isFinite(low)&&high>low?(price-low)/(high-low)*100:NaN;
      const health=feedHealth(live),fresh=health.fresh,openNow=marketOpen();
      const model=stableModel(ticks),quoteValid=validQuote(tick)&&model.price===price
        &&exchangeTime(tick)===model.window_health.latest_valid_exchange_time;
      const ready60=fresh&&openNow&&quoteValid&&model.ready60;
      const ready120=fresh&&openNow&&quoteValid&&model.ready120;
      const conf60=confidenceFor(model,ctx,60),conf120=confidenceFor(model,ctx,120);
      const one=forecast(model.score60,ready60,openNow,fresh,MODEL_VERSION,conf60);
      const two=forecast(model.score120,ready120,openNow,fresh,MODEL_VERSION,conf120);
      const summary=l2?.summary||{},l2metrics=summary.metrics||{};
      const l2Age=(Date.now()-Date.parse(l2?.updated_at||''))/1000;
      const trueL2=summary.ok===true&&Number.isFinite(l2Age)&&l2Age>=-2&&l2Age<5
        &&l2?.status==='online'&&fresh;
      const state={
        symbol,requested_at:requestedAt,price,pct,vwap,vwap_distance_pct:vwapd,book_buy_pct:book,
        bid1,ask1,spread,spread_bp:spreadBp,open,high,low,amount,range_position_pct:rangePos,
        momentum_30s_pct:model.r30,momentum_60s_pct:model.r60,momentum_120s_pct:model.r120,
        one_minute:one,two_minute:two,
        realtime_tendency:{direction:one.direction,label:one.label,score:model.score60},
        selective_validation:{deprecated:true,reason:'Direction and structural confidence remain uncalibrated.'},
        forecast_context:{
          score_standard:SCORE_STANDARD,window_health:model.window_health,
          macd_summary:ctx?.summary||null,macd_resonance:ctx?.resonance||null,
          macd_score:ctx?.score??null,macd_age_seconds:ctx?.cache_age_seconds??null,
          raw_score_60:model.score60_raw_current,score_60:model.score60,
          raw_score_120:model.score120_raw_current,score_120:model.score120,
          confidence_60:conf60.score,confidence_label_60:conf60.label,
          confidence_120:conf120.score,confidence_label_120:conf120.label,
          confidence_components_60:conf60,confidence_components_120:conf120,
          groups_60:model.groups60,groups_120:model.groups120,
          exhaustion_60:model.exhaustion_60,exhaustion_120:model.exhaustion_120,
          score_samples_60:model.score60_samples,score_samples_120:model.score120_samples,
          dispersion_60:model.score60_dispersion,dispersion_120:model.score120_dispersion,
          direction_support_60:model.direction_support_60,direction_support_120:model.direction_support_120,
          calibrated_probability:false,confidence_empirical:false
        },
        validation:summary.validation||{},true_l2:trueL2,l2_status:l2?.status||'missing',
        active_buy_pct:trueL2?n(l2metrics.active_buy_pct,50):null,
        big_buy_pct:trueL2?n(l2metrics.big_buy_pct,50):null,
        depth10_buy_pct:trueL2?n(l2metrics.depth10_buy_pct,50):null,
        ...health,model_version:MODEL_VERSION,market_timezone:'Asia/Shanghai',
        market_open:openNow,window_ready_60:ready60,window_ready_120:ready120,
        prediction_logging:'Asynchronous, representative 10-second snapshots; not every screen refresh.'
      };
      const job=recordAndSettle(rest,state,ticks,BRIDGE_ID).catch(error=>console.error('Prediction journal unavailable:',String(error)));
      if(typeof EdgeRuntime!=='undefined')EdgeRuntime.waitUntil(job);
      else await job;
      return json(req,state);
    }
    return json(req,{error:'not found'},404);
  } catch(e) {
    return json(req,{error:'temporary backend error',detail:String(e instanceof Error?e.message:e)},500);
  }
}
Deno.serve(handler);
