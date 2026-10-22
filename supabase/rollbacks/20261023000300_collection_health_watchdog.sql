-- Extensions pg_cron/pg_net are left installed (harmless); remove in the dashboard if unwanted.
begin;
select cron.unschedule('astock-collection-watchdog') where exists (select 1 from cron.job where jobname = 'astock-collection-watchdog');
drop function if exists public.astock_collection_watchdog(text, int, int);
drop table if exists public.collection_watchdog_state;
drop table if exists public.collection_health_v1;
commit;
