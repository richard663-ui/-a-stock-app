-- Stage 2 of the key migration: let the Windows PC use a dedicated Supabase Auth
-- user instead of a database secret key.
--
-- The device user is created in Dashboard -> Authentication -> Users (no public
-- sign-up), then tagged server-side (users cannot edit app_metadata):
--   update auth.users set raw_app_meta_data = raw_app_meta_data || '{"astock_bridge_id":"family-qmt-01"}'
--   where email = '<device email>';
-- Policies below allow that user to read/write only rows whose bridge_id equals
-- its tag, on exactly the tables the recorders use. No DELETE, no TRUNCATE, no
-- other tables, no schema access. A leaked device credential is revoked by
-- banning or deleting the user. Rollback: supabase/rollbacks/...least_privilege.sql
begin;
create or replace function public.astock_device_bridge_id() returns text
language sql stable
set search_path = ''
as $$ select nullif(auth.jwt() -> 'app_metadata' ->> 'astock_bridge_id', '') $$;
revoke all on function public.astock_device_bridge_id() from public, anon;
grant execute on function public.astock_device_bridge_id() to authenticated;

do $$
declare
  t text;
  write_tables text[] := array['qmt_live_cache', 'qmt_l2_cache', 'l2_recorder_status_v1',
    'ml_training_status_v1', 'ml_shadow_status_v1', 'ml_training_reports_v1',
    'ml_shadow_samples_v1', 'forward_eval_samples_v2', 'collection_health_v1'];
  read_tables text[] := array['qmt_watch_requests', 'mobile_macd_cache'];
  seq text;
begin
  foreach t in array write_tables loop
    execute format('grant select, insert, update on public.%I to authenticated', t);
    execute format('drop policy if exists astock_device_select on public.%I', t);
    execute format('drop policy if exists astock_device_insert on public.%I', t);
    execute format('drop policy if exists astock_device_update on public.%I', t);
    execute format('create policy astock_device_select on public.%I for select to authenticated '
                   'using (bridge_id = (select public.astock_device_bridge_id()))', t);
    execute format('create policy astock_device_insert on public.%I for insert to authenticated '
                   'with check (bridge_id = (select public.astock_device_bridge_id()))', t);
    execute format('create policy astock_device_update on public.%I for update to authenticated '
                   'using (bridge_id = (select public.astock_device_bridge_id())) '
                   'with check (bridge_id = (select public.astock_device_bridge_id()))', t);
    seq := pg_get_serial_sequence(format('public.%I', t), 'id');
    if seq is not null then
      execute format('grant usage on sequence %s to authenticated', seq);
    end if;
  end loop;
  foreach t in array read_tables loop
    execute format('grant select on public.%I to authenticated', t);
    execute format('drop policy if exists astock_device_select on public.%I', t);
    execute format('create policy astock_device_select on public.%I for select to authenticated '
                   'using (bridge_id = (select public.astock_device_bridge_id()))', t);
  end loop;
end $$;
commit;
