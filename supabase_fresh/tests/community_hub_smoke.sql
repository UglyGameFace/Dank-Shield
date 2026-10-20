-- Dedicated offline project only. Writes inside transaction ROLLBACK.
begin;
do $inventory$
declare
 v_tables integer;
 v_rpcs integer;
begin
 select count(*) into v_tables from pg_tables
 where schemaname='public' and tablename like 'dank_community_%';
 if v_tables<>16 then raise exception 'Community Hub table count expected 16 found %',v_tables;end if;
 select count(*) into v_rpcs from pg_proc p
 join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='public' and p.proname like 'community_hub_%';
 if v_rpcs<>24 then raise exception 'Community Hub RPC count expected 24 found %',v_rpcs;end if;
 if exists(
 select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='public' and p.proname like 'community_hub_%'
   and (p.prosecdef or has_function_privilege('anon',p.oid,'EXECUTE')
     or has_function_privilege('authenticated',p.oid,'EXECUTE')
     or not has_function_privilege('service_role',p.oid,'EXECUTE'))
 ) then raise exception 'Community Hub function role boundary violated';end if;
 if (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r'
    and c.relname like 'dank_community_%' and c.relrowsecurity)<>16 then
  raise exception 'Hub table RLS missing';end if;
 if exists (select 1 from information_schema.tables
  where table_schema='public' and table_name='dank_community_hub_settings'
    and table_type<>'BASE TABLE') then raise exception 'Hub settings is not a base table';end if;
 if has_table_privilege('anon','public.dank_community_hub_settings','SELECT')
    or has_table_privilege('authenticated','public.dank_community_sessions','INSERT')
    or not has_table_privilege('service_role','public.dank_community_sessions','INSERT') then
   raise exception 'Hub table permission boundary violated';end if;
end $inventory$;
set local role service_role;
do $runtime$
declare
 s jsonb;
 replay jsonb;
 sid uuid;
 changed jsonb;
 link jsonb;
begin
 if exists(select 1 from public.dank_community_hub_settings
  where guild_id in ('990000000000000000','990000000000000001')) then
  raise exception 'Unexpected synthetic guild collision';end if;
 s := public.community_hub_create_session(
  '990000000000000000','990000000000000010','new-database-test',
  'Minecraft','',6,'optional','casual','public');
 sid := (s->>'id')::uuid;
 if sid is null or s->>'state'<>'creating' then raise exception 'Session creation failed';end if;
 replay := public.community_hub_create_session(
  '990000000000000000','990000000000000010','new-database-test',
  'Minecraft','',6,'optional','casual','public');
 if replay->>'id'<>s->>'id' then raise exception 'Session creation replay created duplicate';end if;
 changed := public.community_hub_transition_session(
  sid,'990000000000000000','990000000000000010','publish');
 if changed->>'state'<>'open' then raise exception 'Session publish failed';end if;
 changed := public.community_hub_join_session(
  sid,'990000000000000000','990000000000000011');
 if changed->>'role'<>'member' then raise exception 'Member join failed';end if;
 if (select count(*) from public.dank_community_sessions where guild_id='990000000000000000')<>1
   or exists(select 1 from public.dank_community_sessions where guild_id='990000000000000001') then
  raise exception 'Duplicate or cross-server sessions';end if;
 if not exists (select 1 from public.dank_community_session_members
  where guild_id='990000000000000000' and user_id='990000000000000011') then
  raise exception 'Active member session row missing';end if;
 link := public.community_hub_create_link_code(
  '990000000000000000','990000000000000010',repeat('a',64),'ABCD');
 if link->>'state'<>'pending' then raise exception 'HubLink creation failed';end if;
 if (select external_the100_enabled from public.dank_community_hub_settings
   where guild_id='990000000000000000')<>false then
  raise exception 'External listing integration unexpectedly enabled';end if;
end $runtime$;
reset role;
rollback;
