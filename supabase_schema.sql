-- Run this once in a dedicated Supabase project's SQL Editor.
-- The app and local bridge use the service-role/secret key stored only in secrets.

create table if not exists public.qmt_watch_requests (
    bridge_id text primary key,
    symbol text not null,
    requested_at timestamptz not null default now()
);

create table if not exists public.qmt_live_cache (
    bridge_id text not null,
    symbol text not null,
    status text not null default 'offline',
    updated_at timestamptz not null default now(),
    ticks jsonb not null default '[]'::jsonb,
    primary key (bridge_id, symbol)
);

create table if not exists public.qmt_l2_cache (
    bridge_id text not null,
    symbol text not null,
    status text not null default 'offline',
    updated_at timestamptz not null default now(),
    summary jsonb not null default '{}'::jsonb,
    capabilities jsonb not null default '{}'::jsonb,
    recent_transactions jsonb not null default '[]'::jsonb,
    recent_orders jsonb not null default '[]'::jsonb,
    quoteaux jsonb not null default '{}'::jsonb,
    orderqueue jsonb not null default '{}'::jsonb,
    primary key (bridge_id, symbol)
);

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create or replace function public.touch_requested_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  new.requested_at = now();
  return new;
end;
$$;

drop trigger if exists trg_qmt_cache_updated_at on public.qmt_live_cache;
create trigger trg_qmt_cache_updated_at
before update on public.qmt_live_cache
for each row execute function public.touch_updated_at();

drop trigger if exists trg_qmt_l2_updated_at on public.qmt_l2_cache;
create trigger trg_qmt_l2_updated_at
before update on public.qmt_l2_cache
for each row execute function public.touch_updated_at();

drop trigger if exists trg_qmt_request_updated_at on public.qmt_watch_requests;
create trigger trg_qmt_request_updated_at
before update on public.qmt_watch_requests
for each row execute function public.touch_requested_at();

alter table public.qmt_watch_requests enable row level security;
alter table public.qmt_live_cache enable row level security;
alter table public.qmt_l2_cache enable row level security;

-- Intentionally no anon/authenticated policies.
-- With RLS enabled and no public policies, browser clients cannot read these tables.
-- The server-side secret/service-role key is kept only in Streamlit Secrets and the
-- local persistent secrets.toml; it must never be committed to GitHub.
