-- Restores the previous (overly broad) default grants. Only use if a client that
-- relied on anon/authenticated access was missed; row-level security still applies.
begin;
grant all on table public.forward_eval_samples_v2, public.ml_training_reports_v1,
  public.ml_training_status_v1, public.mobile_signal_history, public.mobile_signal_locks,
  public.qmt_l2_cache, public.qmt_live_cache, public.qmt_watch_requests to anon, authenticated;
alter default privileges in schema public grant all on tables to anon, authenticated;
commit;
