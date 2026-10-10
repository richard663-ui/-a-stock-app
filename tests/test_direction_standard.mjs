import assert from 'node:assert/strict';
import {prepareTicks,timeWindow,historicalCutoff} from '../supabase/functions/astock-mobile/tick-window.ts';
import {stableModel,scoreView,confidenceFor} from '../supabase/functions/astock-mobile/market.ts';
import {buildPredictionRows,settlePrediction,recordAndSettle} from '../supabase/functions/astock-mobile/evaluation.ts';

const now=Date.parse('2026-10-09T01:45:00Z');
function ticks(seconds,end=now){
  return Array.from({length:seconds+1},(_,i)=>({time:end-(seconds-i)*1000,
    lastPrice:10+i*.001,bidPrice:[10+i*.001-.005],askPrice:[10+i*.001+.005],
    bidVol:[100,80],askVol:[30,40],volume:1000+i,amount:1000000+i*1000}));
}
assert.equal(stableModel(ticks(60)).ready60,false);
assert.deepEqual(stableModel(ticks(60)).score60_samples.slice(1),[null,null,null]);
assert.equal(stableModel(ticks(75)).ready60,true);
assert.equal(stableModel(ticks(149)).ready120,false);
assert.equal(stableModel(ticks(150)).ready120,true);
assert.equal(historicalCutoff(ticks(5),15).length,0);
assert.equal(timeWindow([{captured_at:new Date(now).toISOString()}],60).length,0);
assert.equal(stableModel([{captured_at:new Date(now).toISOString(),lastPrice:10}]).ready60,false);
const repeated=Array(100).fill(ticks(1)[1]);
assert.equal(prepareTicks(repeated).ticks.length,1);
assert.equal(stableModel(repeated).ready60,false);
const gap=[...ticks(75,now-30000),...ticks(5)];
assert.equal(prepareTicks(gap).window_resets,1);
assert.equal(stableModel(gap).ready60,false);
const lunch=[...ticks(150,Date.parse('2026-10-09T03:29:59Z')),
  ...ticks(5,Date.parse('2026-10-09T05:00:05Z'))];
assert.equal(stableModel(lunch).ready120,false);
const reset=ticks(100);reset[90]={...reset[90],volume:1,amount:1};
assert.equal(stableModel(reset).ready60,false);
assert.equal(scoreView(15).direction,'UP');assert.equal(scoreView(-15).direction,'DOWN');
assert.equal(scoreView(14).direction,'WATCH');assert.equal(scoreView(70).label,'偏涨｜较强');
const model=stableModel(ticks(150));
const context={freshness_seconds:0,timeframes:{m1:{state_score:100,transition_score:1}}};
assert.equal(confidenceFor(model,context).macd_context_used,true);
for(const age of [121,null,-1,NaN,999]){
  const invalidContext=confidenceFor(model,{...context,freshness_seconds:age});
  assert.equal(invalidContext.macd_alignment,0);
  assert.equal(invalidContext.macd_transition_alignment,0);
  assert.equal(invalidContext.macd_context_used,false);
}

const state={fresh:true,market_open:true,price:10,bid1:9.99,ask1:10.01,
  data_time:new Date(now-1000).toISOString(),symbol:'SAMPLE.SH',model_version:'isolated-test',
  window_ready_60:true,window_ready_120:false,
  one_minute:{direction:'UP',confidence_score:80},two_minute:{direction:'WATCH',label:'数据补齐中'},
  forecast_context:{score_60:70,score_120:0}};
const rows=buildPredictionRows(state,'isolated-test',now);
assert.equal(rows.length,2);assert.equal(rows[1].block_reason,'数据补齐中');
assert.equal(buildPredictionRows({...state,fresh:false},'isolated-test',now).length,0);
assert.equal(buildPredictionRows({...state,market_open:false},'isolated-test',now).length,0);
const future={time:now+60000,lastPrice:10.04,bidPrice:[10.005],askPrice:[10.045]};
const settled=settlePrediction(rows[0],[future],now+60000);
assert.equal(settled.is_correct,true);
assert.ok(settled.up_ask_bid_net_bp<0,'Right price direction can still lose after spread/cost.');
assert.equal(settlePrediction(rows[0],[future],now+59999),null);
assert.equal(settlePrediction(rows[0],[],now+71000).evaluation_status,'EXPIRED');
assert.equal(settlePrediction(rows[0],[{...future,lastPrice:10.001}],now+60000).actual_direction,'FLAT');
assert.equal(settlePrediction({...rows[0],neutral_band_bp:20},[{...future,lastPrice:10.02}],now+60000).actual_direction,'FLAT');
assert.equal(settlePrediction({...rows[0],neutral_band_bp:20},[{...future,lastPrice:9.98}],now+60000).actual_direction,'FLAT');
const watch={...rows[0],direction:'WATCH',window_ready:false};
assert.equal(settlePrediction(watch,[future],now+60000).is_correct,null);
const down={...rows[0],direction:'DOWN'};
assert.equal(settlePrediction(down,[{...future,lastPrice:9.9,bidPrice:[9.89]}],now+60000).up_ask_bid_net_bp,null);
const late={...rows[0],issued_at:'2026-10-09T03:29:50Z',target_time:'2026-10-09T03:30:50Z'};
assert.equal(settlePrediction(late,ticks(150,Date.parse('2026-10-09T05:00:00Z')),
  Date.parse('2026-10-09T05:00:00Z')).evaluation_status,'EXPIRED');
let calls=[];
await recordAndSettle(async(path,init)=>{calls.push({path,init});return [];},state,[], 'isolated-test',now);
assert.equal(calls.length,2);
assert.ok(calls[1].init.headers.Prefer.includes('ignore-duplicates'),'Frozen first prediction must not be overwritten.');
console.log('Exchange windows, direction standards and prospective evaluation tests PASS');
