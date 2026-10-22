-- Phone login rate limiting used by supabase/functions/_shared/security.ts.
-- Buckets are keyed HMACs of the client address; raw IPs are never stored.
-- 5 failures per bucket per 15 minutes, or 30 failures overall per hour, block login.
begin;
create table if not exists public.auth_login_attempts (
  id bigint generated always as identity primary key,
  bucket text not null,
  success boolean not null,
  attempted_at timestamptz not null default now()
);
create index if not exists auth_login_attempts_bucket_idx on public.auth_login_attempts (bucket, attempted_at desc);
create index if not exists auth_login_attempts_time_idx on public.auth_login_attempts (attempted_at desc);
alter table public.auth_login_attempts enable row level security;
revoke all on public.auth_login_attempts from public, anon, authenticated;
grant select, insert, delete on public.auth_login_attempts to service_role;

create or replace function public.astock_login_gate(
  p_bucket text,
  p_max_bucket_failures int default 5,
  p_bucket_window interval default interval '15 minutes',
  p_max_global_failures int default 30,
  p_global_window interval default interval '1 hour'
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  bucket_failures int;
  global_failures int;
begin
  delete from public.auth_login_attempts where attempted_at < now() - interval '2 days';
  select count(*) into bucket_failures from public.auth_login_attempts
    where bucket = p_bucket and not success and attempted_at > now() - p_bucket_window;
  select count(*) into global_failures from public.auth_login_attempts
    where not success and attempted_at > now() - p_global_window;
  return jsonb_build_object(
    'allowed', bucket_failures < p_max_bucket_failures and global_failures < p_max_global_failures,
    'bucket_failures', bucket_failures,
    'global_failures', global_failures,
    'retry_after_seconds', case
      when bucket_failures >= p_max_bucket_failures then extract(epoch from p_bucket_window)::int
      when global_failures >= p_max_global_failures then 900 else 0 end);
end $$;
revoke all on function public.astock_login_gate(text, int, interval, int, interval) from public, anon, authenticated;
grant execute on function public.astock_login_gate(text, int, interval, int, interval) to service_role;
commit;
