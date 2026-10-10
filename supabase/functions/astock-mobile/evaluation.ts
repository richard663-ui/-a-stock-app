import {exchangeTime, sessionKey, validQuote} from './tick-window.ts';

export const EVALUATION_STANDARD = {
  version:'mobile-forward-standard-v1',sampling_seconds:10,
  horizons_seconds:[60,120],neutral_floor_bp:2,exit_tolerance_seconds:10,
  assumed_roundtrip_cost_bp:2,down_interpretation:'holding_risk_not_short_trade',
  probabilities_calibrated:false,automatic_promotion:false
};

export function buildPredictionRows(state,bridgeId,now=Date.now()) {
  if(!state.fresh || !state.market_open || !(state.price>0)
      || !(state.bid1>0) || !(state.ask1>=state.bid1))return [];
  const timestamp=Date.parse(state.data_time||'');
  if(!Number.isFinite(timestamp)||now-timestamp>12000||now<timestamp)return [];
  const bucket=Math.floor(now/10000)*10000;
  return [60,120].map(horizon=>{
    const prediction=horizon===60?state.one_minute:state.two_minute;
    const ready=horizon===60?state.window_ready_60:state.window_ready_120;
    return {
      id:[bridgeId,state.symbol,state.model_version,horizon,bucket].join(':'),
      bridge_id:bridgeId,symbol:state.symbol,model_version:state.model_version,
      evaluation_standard:EVALUATION_STANDARD.version,
      sample_bucket:new Date(bucket).toISOString(),issued_at:new Date(now).toISOString(),
      reference_time:state.data_time,target_time:new Date(now+horizon*1000).toISOString(),
      horizon_seconds:horizon,direction:prediction.direction,
      direction_score:horizon===60?state.forecast_context.score_60:state.forecast_context.score_120,
      structural_confidence:prediction.confidence_score||0,window_ready:!!ready,
      block_reason:ready?'':prediction.label,
      reference_price:state.price,reference_bid:state.bid1,reference_ask:state.ask1,
      neutral_band_bp:Math.max(2,(state.ask1-state.bid1)/((state.ask1+state.bid1)/2)*10000),
      evaluation_status:'PENDING'
    };
  });
}

export function settlePrediction(row,ticks,now=Date.now()) {
  const issued=Date.parse(row.issued_at),target=Date.parse(row.target_time);
  if(!Number.isFinite(issued)||!Number.isFinite(target))return null;
  // Observe an actual subsequent quote; never estimate future bid from today's spread.
  const exit=(ticks||[]).filter(tick=>{
    const time=exchangeTime(tick);
    return validQuote(tick)&&time>=target&&time<=target+10000&&time<=now
      &&sessionKey(time)===sessionKey(issued);
  }).sort((a,b)=>exchangeTime(a)-exchangeTime(b))[0];
  if(!exit)return now>target+10000?{evaluation_status:'EXPIRED',
    evaluation_issue:'No observed same-session quote within target + 10 seconds.'}:null;
  const price=Number(exit.lastPrice),bid=Number(exit.bidPrice?.[0]??exit.bidPrice1);
  const ret=(price/row.reference_price-1)*10000,band=row.neutral_band_bp;
  const actual=ret>band+1e-8?'UP':ret < -band-1e-8?'DOWN':'FLAT';
  return {
    evaluation_status:'EVALUATED',exit_time:new Date(exchangeTime(exit)).toISOString(),
    exit_price:price,exit_bid:bid,future_return_bp:ret,actual_direction:actual,
    is_correct:row.window_ready&&['UP','DOWN'].includes(row.direction)?actual===row.direction:null,
    up_ask_bid_net_bp:row.direction==='UP'?(bid/row.reference_ask-1)*10000-2:null,
    down_avoided_loss_bp:row.direction==='DOWN'?(1-bid/row.reference_bid)*10000:null,
    evaluation_issue:''
  };
}

export async function recordAndSettle(rest,state,ticks,bridgeId,now=Date.now()) {
  const prefix=`mobile_prediction_log_v1?bridge_id=eq.${encodeURIComponent(bridgeId)}`
    +`&symbol=eq.${encodeURIComponent(state.symbol)}&evaluation_status=eq.PENDING`;
  const pending=await rest(prefix+'&order=issued_at.asc&limit=100');
  for(const row of Array.isArray(pending)?pending:[]){
    const result=settlePrediction(row,ticks,now);
    if(result)await rest(`mobile_prediction_log_v1?id=eq.${encodeURIComponent(row.id)}&evaluation_status=eq.PENDING`,
      {method:'PATCH',body:JSON.stringify(result)});
  }
  const rows=buildPredictionRows(state,bridgeId,now);
  if(rows.length)await rest('mobile_prediction_log_v1?on_conflict=id',{
    method:'POST',headers:{Prefer:'resolution=ignore-duplicates,return=minimal'},body:JSON.stringify(rows)});
}

export async function evaluationState(rest,bridgeId,modelVersion) {
  const rows=await rest(`mobile_prediction_summary_v1?bridge_id=eq.${encodeURIComponent(bridgeId)}`
    +`&model_version=eq.${encodeURIComponent(modelVersion)}&order=horizon_seconds.asc,direction.asc&limit=100`);
  return {model_version:modelVersion,standard:EVALUATION_STANDARD,
    summaries:Array.isArray(rows)?rows:[],research_only:true,calibrated_probability:false,
    sample_scope:'First observed response per stock/model/horizon/10-second bucket; only while monitored.',
    accuracy_warning:'Overlapping samples are correlated. Point accuracy is descriptive, not a validated win probability.'};
}
