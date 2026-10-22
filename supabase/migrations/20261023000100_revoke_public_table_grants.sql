-- Defense in depth: no client uses the anon/authenticated roles directly. All
-- current access goes through the service role (local bridge, edge functions).
-- Several tables still carried Supabase's default ALL grants for anon and
-- authenticated, including TRUNCATE, which row-level security does not cover.
-- Zero behaviour change for current clients. Rollback:
-- supabase/rollbacks/20261023000100_revoke_public_table_grants.sql
begin;
do $$
declare t record;
begin
  for t in
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'v', 'm', 'p')
  loop
    execute format('revoke all on table public.%I from anon, authenticated', t.relname);
    execute format('grant all on table public.%I to service_role', t.relname);
  end loop;
end $$;
-- Tables created later must not silently inherit client grants either.
alter default privileges in schema public revoke all on tables from anon, authenticated;
commit;
