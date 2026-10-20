-- Execute ONLY in the dedicated new DankShield Supabase project.
-- Runs a configuration mutation and stale-CAS test with a guaranteed rollback.
begin;
do $test$
declare
  gid text;
  changed jsonb;
  stale jsonb;
  current_payload jsonb;
begin
  if (select count(*) from public.guild_configs) = 0 then
    raise exception 'Guild config snapshot is missing';
  end if;
  if exists (
    select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname in
      ('guild_configs','guild_security_settings','guild_security_actor_reputation')
      and not c.relrowsecurity
  ) then
    raise exception 'One critical table lacks RLS';
  end if;
  if has_table_privilege('anon','public.guild_configs','SELECT')
     or has_table_privilege('authenticated','public.guild_configs','SELECT')
     or has_function_privilege('anon',
           'public.patch_dank_guild_config(text,jsonb,text[],jsonb)','EXECUTE')
  then
    raise exception 'Critical configuration exposed to public clients';
  end if;
  select guild_id into gid from public.guild_configs order by guild_id limit 1;
  changed := public.patch_dank_guild_config(
    gid,
    jsonb_build_object('fresh_schema_smoke_test',jsonb_build_object('test',true)),
    array[]::text[],
    jsonb_build_object('fresh_schema_smoke_test',null)
  );
  if changed ->> '__atomic_patch_applied' is distinct from 'true' then
    raise exception 'Atomic update failed';
  end if;
  select settings into current_payload from public.guild_configs where guild_id=gid;
  if current_payload #>> '{fresh_schema_smoke_test,test}' is distinct from 'true' then
    raise exception 'Atomic update was not stored';
  end if;
  stale := public.patch_dank_guild_config(
    gid,
    jsonb_build_object('fresh_schema_smoke_test',jsonb_build_object('test',false)),
    array[]::text[],
    jsonb_build_object('fresh_schema_smoke_test',jsonb_build_object('test','other'))
  );
  if stale ->> '__atomic_patch_applied' is distinct from 'false' then
    raise exception 'Compare-and-swap accepted stale data';
  end if;
end $test$;
rollback;
