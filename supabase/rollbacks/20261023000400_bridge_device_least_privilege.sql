-- Switch the PC back to its secret key in ~/.a_stock_qmt/secrets.toml before running this.
begin;
do $$
declare t text;
begin
  foreach t in array array['qmt_live_cache', 'qmt_l2_cache', 'l2_recorder_status_v1', 'ml_training_status_v1',
      'ml_shadow_status_v1', 'ml_training_reports_v1', 'ml_shadow_samples_v1', 'forward_eval_samples_v2',
      'collection_health_v1', 'qmt_watch_requests', 'mobile_macd_cache'] loop
    execute format('drop policy if exists astock_device_select on public.%I', t);
    execute format('drop policy if exists astock_device_insert on public.%I', t);
    execute format('drop policy if exists astock_device_update on public.%I', t);
    execute format('revoke all on public.%I from authenticated', t);
  end loop;
end $$;
drop function if exists public.astock_device_bridge_id();
commit;
