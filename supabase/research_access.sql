-- All existing clients use server-side service_role and signed phone sessions.
begin;
alter table public.ml_training_status_v1
  add column if not exists learning_progress jsonb not null default '{}'::jsonb;
alter table public.l2_recorder_status_v1 enable row level security;
alter table public.ml_shadow_samples_v1 enable row level security;
alter table public.ml_shadow_status_v1 enable row level security;
revoke all on public.l2_recorder_status_v1, public.ml_shadow_samples_v1,
  public.ml_shadow_status_v1 from public, anon, authenticated;
grant all on public.l2_recorder_status_v1, public.ml_shadow_samples_v1,
  public.ml_shadow_status_v1 to service_role;
alter view public.forward_eval_daily_stats_v3 set (security_invoker = true);
alter view public.forward_eval_daily_v2 set (security_invoker = true);
alter view public.forward_eval_score_bins_v2 set (security_invoker = true);
revoke all on public.forward_eval_daily_stats_v3, public.forward_eval_daily_v2,
  public.forward_eval_score_bins_v2 from public, anon, authenticated;
grant select on public.forward_eval_daily_stats_v3, public.forward_eval_daily_v2,
  public.forward_eval_score_bins_v2 to service_role;
commit;
