-- The edge functions fail open (with a log line) when the gate is missing.
begin;
drop function if exists public.astock_login_gate(text, int, interval, int, interval);
drop table if exists public.auth_login_attempts;
commit;
