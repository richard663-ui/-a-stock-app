-- Cloud-side collection watchdog.
--
-- A sleeping or powered-off PC cannot report its own outage, so the database
-- checks the recorder heartbeat during Shanghai sessions and pushes one alert
-- per hour to a webhook stored in Vault:
--   select vault.create_secret('https://ntfy.sh/<long-random-topic>', 'astock_alert_webhook_url');
--   select vault.create_secret('ntfy', 'astock_alert_webhook_format');   -- ntfy | json
-- Without the secret the watchdog only records state (no push).
-- The local collection monitor also publishes its live status to collection_health_v1.
-- Known limit: weekday holidays are not excluded yet (a PC switched off on a
-- holiday produces one alert per hour during would-be session hours).
begin;
create table if not exists public.collection_health_v1 (
  bridge_id text primary key,
  updated_at timestamptz not null default now(),
  monitor_version text,
  overall text not null,
  phase text,
  trading_day boolean,
  findings jsonb not null default '[]'::jsonb,
  symbols jsonb not null default '{}'::jsonb,
  processes jsonb not null default '{}'::jsonb
);
create table if not exists public.collection_watchdog_state (
  bridge_id text primary key,
  last_alert_at timestamptz,
  last_status text,
  checked_at timestamptz
);
alter table public.collection_health_v1 enable row level security;
alter table public.collection_watchdog_state enable row level security;
revoke all on public.collection_health_v1, public.collection_watchdog_state from public, anon, authenticated;
grant all on public.collection_health_v1, public.collection_watchdog_state to service_role;

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

create or replace function public.astock_collection_watchdog(
  p_bridge_id text default 'family-qmt-01',
  p_stale_minutes int default 5,
  p_repeat_minutes int default 60
) returns text
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  local_ts timestamp := now() at time zone 'Asia/Shanghai';
  minute_of_day int := extract(hour from local_ts)::int * 60 + extract(minute from local_ts)::int;
  in_session boolean := extract(isodow from local_ts) < 6
    and ((minute_of_day >= 555 and minute_of_day < 690) or (minute_of_day >= 780 and minute_of_day < 900));
  last_seen timestamptz;
  last_alert timestamptz;
  url text;
  fmt text;
  msg text;
  base text;
  topic text;
begin
  if not in_session then
    return 'outside_session';
  end if;
  select greatest(
      coalesce((select updated_at from public.l2_recorder_status_v1 where bridge_id = p_bridge_id), '-infinity'),
      coalesce((select updated_at from public.collection_health_v1 where bridge_id = p_bridge_id), '-infinity'))
    into last_seen;
  if last_seen > now() - make_interval(mins => p_stale_minutes) then
    insert into public.collection_watchdog_state (bridge_id, last_status, checked_at) values (p_bridge_id, 'ok', now())
      on conflict (bridge_id) do update set last_status = 'ok', checked_at = now();
    return 'ok';
  end if;
  select last_alert_at into last_alert from public.collection_watchdog_state where bridge_id = p_bridge_id;
  if last_alert is not null and last_alert > now() - make_interval(mins => p_repeat_minutes) then
    return 'stale_already_alerted';
  end if;
  select decrypted_secret into url from vault.decrypted_secrets where name = 'astock_alert_webhook_url';
  select coalesce((select decrypted_secret from vault.decrypted_secrets where name = 'astock_alert_webhook_format'), 'json') into fmt;
  msg := format('PC heartbeat stale since %s Shanghai during the session: the laptop may be asleep, off or offline. Market data is not being recorded.',
                coalesce(to_char(last_seen at time zone 'Asia/Shanghai', 'MM-DD HH24:MI'), 'unknown'));
  if url like 'https://%' then
    if fmt = 'ntfy' then
      base := regexp_replace(url, '/[^/]+$', '');
      topic := regexp_replace(url, '^.*/', '');
      perform net.http_post(url := base,
        body := jsonb_build_object('topic', topic, 'title', 'AStock collection offline', 'message', msg, 'priority', 5));
    else
      perform net.http_post(url := url,
        body := jsonb_build_object('title', 'AStock collection offline', 'message', msg, 'severity', 'CRITICAL'));
    end if;
  end if;
  insert into public.collection_watchdog_state (bridge_id, last_alert_at, last_status, checked_at)
    values (p_bridge_id, now(), case when url like 'https://%' then 'alert_sent' else 'stale_no_webhook' end, now())
    on conflict (bridge_id) do update
      set last_alert_at = excluded.last_alert_at, last_status = excluded.last_status, checked_at = now();
  return case when url like 'https://%' then 'stale_alert_sent' else 'stale_no_webhook' end;
end $$;
revoke all on function public.astock_collection_watchdog(text, int, int) from public, anon, authenticated;

-- Every 5 minutes, 01:00-07:59 UTC (09:00-15:59 Shanghai), Monday-Friday.
select cron.schedule('astock-collection-watchdog', '*/5 1-7 * * 1-5',
                     $cron$select public.astock_collection_watchdog()$cron$);
commit;
