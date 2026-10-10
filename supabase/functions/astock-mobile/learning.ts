import {BRIDGE_ID} from './auth.ts';

export async function learningState(rest) {
  const bridge=encodeURIComponent(BRIDGE_ID);
  const [training,recorder,shadow,reports]=await Promise.all([
    rest(`ml_training_status_v1?bridge_id=eq.${bridge}&select=state,message,last_heartbeat_at,last_attempt_at,priority_samples,pooled_samples,priority_result,pooled_result,learning_progress&limit=1`),
    rest(`l2_recorder_status_v1?bridge_id=eq.${bridge}&select=updated_at,market_open,sample_counts_today,labeled_counts_today,status&limit=1`),
    rest(`ml_shadow_status_v1?bridge_id=eq.${bridge}&select=state,last_heartbeat_at,predictions_today,settled_today&limit=1`),
    rest(`ml_training_reports_v1?bridge_id=eq.${bridge}&scope=like.ALL*&select=scope,generated_at,protocol,report&order=generated_at.desc&limit=3`)
  ]);
  const train=training?.[0]||{},rec=recorder?.[0]||{},sh=shadow?.[0]||{};
  const results=(reports||[]).map(r=>({scope:r.scope,generated_at:r.generated_at,protocol:r.protocol,
    models:Object.entries(r.report?.models||{}).map(([name,model])=>({name,
      readiness:model.readiness,metrics:model.selected_threshold_test_nonoverlap}))}));
  return {ok:true,training:train,recorder:{updated_at:rec.updated_at,market_open:rec.market_open,
    sample_counts_today:rec.sample_counts_today,labeled_counts_today:rec.labeled_counts_today,
    data_mode:rec.status?.ml_data_mode||'L1_BASELINE'},shadow:sh,model_results:results,
    research_only:true,auto_deployed:false,market_timezone:'Asia/Shanghai'};
}
