create table if not exists public.mobile_prediction_log_v1 (
  id text primary key,
  bridge_id text not null,
  symbol text not null,
  model_version text not null,
  evaluation_standard text not null,
  sample_bucket timestamptz not null,
  issued_at timestamptz not null,
  reference_time timestamptz not null,
  target_time timestamptz not null,
  horizon_seconds integer not null check (horizon_seconds in (60,120)),
  direction text not null check (direction in ('UP','DOWN','WATCH')),
  direction_score numeric not null,
  structural_confidence numeric not null,
  window_ready boolean not null,
  block_reason text not null default '',
  reference_price numeric not null check (reference_price > 0),
  reference_bid numeric not null check (reference_bid > 0),
  reference_ask numeric not null check (reference_ask >= reference_bid),
  neutral_band_bp numeric not null check (neutral_band_bp >= 2),
  evaluation_status text not null check (evaluation_status in ('PENDING','EVALUATED','EXPIRED')),
  exit_time timestamptz,
  exit_price numeric,
  exit_bid numeric,
  future_return_bp numeric,
  actual_direction text check (actual_direction in ('UP','DOWN','FLAT')),
  is_correct boolean,
  up_ask_bid_net_bp numeric,
  down_avoided_loss_bp numeric,
  evaluation_issue text not null default '',
  check (target_time > issued_at),
  check (reference_time <= issued_at),
  unique (bridge_id,symbol,model_version,horizon_seconds,sample_bucket)
);
create index if not exists mobile_prediction_pending_idx
  on public.mobile_prediction_log_v1 (bridge_id,symbol,issued_at)
  where evaluation_status = 'PENDING';
alter table public.mobile_prediction_log_v1 enable row level security;
revoke all on public.mobile_prediction_log_v1 from public,anon,authenticated;
grant select,insert,update on public.mobile_prediction_log_v1 to service_role;

create or replace view public.mobile_prediction_summary_v1
  with (security_invoker = true) as
select bridge_id,model_version,evaluation_standard,horizon_seconds,direction,
  count(*) as logged_count,
  count(*) filter (where window_ready) as ready_count,
  count(*) filter (where evaluation_status = 'PENDING') as pending_count,
  count(*) filter (where evaluation_status = 'EXPIRED') as expired_count,
  count(*) filter (where is_correct is not null) as evaluated_directional_count,
  count(*) filter (where is_correct) as correct_count,
  avg(is_correct::int) * 100 as descriptive_accuracy_pct,
  count(distinct (issued_at at time zone 'Asia/Shanghai')::date)
    filter (where is_correct is not null) as evaluated_days,
  count(distinct symbol) filter (where is_correct is not null) as evaluated_stocks,
  avg(up_ask_bid_net_bp) filter (where window_ready) as avg_up_ask_bid_net_bp,
  avg(down_avoided_loss_bp) filter (where window_ready) as avg_down_avoided_loss_bp,
  min(issued_at) as first_prediction_at,max(issued_at) as latest_prediction_at
from public.mobile_prediction_log_v1
group by bridge_id,model_version,evaluation_standard,horizon_seconds,direction;
revoke all on public.mobile_prediction_summary_v1 from public,anon,authenticated;
grant select on public.mobile_prediction_summary_v1 to service_role;
