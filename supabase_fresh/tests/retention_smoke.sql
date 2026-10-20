-- Apply to the isolated new DankShield Supabase candidate ONLY.
-- It must return no errors and leave no persistent test data.
begin;
do $retention_test$
declare
  fake_guild text := '999999999999999994';
  other_guild text := '999999999999999993';
  config_guild text;
  category uuid;
  manual_id bigint;
  n integer;
  tok_expired text := 'old' || replace(gen_random_uuid()::text,'-','');
  tok_used text := 'used' || replace(gen_random_uuid()::text,'-','');
  tok_recent text := 'recent' || replace(gen_random_uuid()::text,'-','');
  tok_active text := 'active' || replace(gen_random_uuid()::text,'-','');
begin
  if exists(select 1 from public.guild_config_versions where guild_id in(fake_guild,other_guild))
  then raise exception 'Synthetic guild IDs already in use';end if;
  insert into public.guild_config_versions(guild_id,config_table,snapshot,is_manual,created_at)
    select fake_guild,'guild_configs',jsonb_build_object('v',i),false,now()-make_interval(mins=>i)
    from generate_series(1,55) i;
  insert into public.guild_config_versions(guild_id,config_table,snapshot,is_manual,created_at)
    values(fake_guild,'guild_configs','{"keep":"manual"}',true,now()-interval '90 days')
    returning version_id into manual_id;
  insert into public.guild_config_versions(guild_id,config_table,snapshot,is_manual)
    values(other_guild,'guild_configs','{}',false);
  n:=dank_maintenance.prune_nonmanual_config_versions(fake_guild,'guild_configs');
  if n<>5 or
    (select count(*) from public.guild_config_versions where guild_id=fake_guild and not is_manual)<>50 or
    not exists(select 1 from public.guild_config_versions where version_id=manual_id) or
    (select count(*) from public.guild_config_versions where guild_id=other_guild)<>1
  then raise exception 'Version prune scope/manual protection failed';end if;

  -- Verify both authoritative AFTER triggers call the bounded helper.
  select guild_id into config_guild from public.guild_configs order by guild_id limit 1;
  if config_guild is null then raise exception 'No config fixture on candidate';end if;
  insert into public.guild_config_versions(guild_id,config_table,snapshot,is_manual,created_at)
    select config_guild,'guild_configs',jsonb_build_object('v',i),false,now()-make_interval(mins=>i)
    from generate_series(1,55) i;
  update public.guild_configs set guild_name=coalesce(guild_name,'')||' retention-smoke'
    where guild_id=config_guild;
  if (select count(*) from public.guild_config_versions where guild_id=config_guild
      and config_table='guild_configs' and not is_manual)<>50
  then raise exception 'Guild trigger did not prune old automatic versions';end if;

  select id into category from public.ticket_categories where guild_id=config_guild order by id limit 1;
  if category is null then raise exception 'No ticket category for synthetic trigger test';end if;
  insert into public.guild_config_versions(guild_id,config_table,snapshot,is_manual,created_at)
    select config_guild,'ticket_categories',
           jsonb_build_object('guild_id',config_guild,'rows','[]'::jsonb),false,
           now()-make_interval(mins=>i) from generate_series(1,55) i;
  update public.ticket_categories set sort_order=sort_order+1 where id=category;
  if (select count(*) from public.guild_config_versions where guild_id=config_guild
      and config_table='ticket_categories' and not is_manual)<>50
  then raise exception 'Ticket-category trigger did not prune old automatic versions';end if;

  insert into public.verification_tokens(token,webhook_url,expires_at,used)
  values(tok_expired,'https://example.org/synthetic',now()-interval '4 days',false),
        (tok_used,'https://example.org/synthetic',now()-interval '4 days',true),
        (tok_recent,'https://example.org/synthetic',now()-interval '1 hour',false),
        (tok_active,'https://example.org/synthetic',now()+interval '2 hours',false);
  if dank_maintenance.purge_expired_verification_tokens(1)<>1
     or dank_maintenance.purge_expired_verification_tokens(5)<>1
  then raise exception 'Token cleanup did not enforce batch limit';end if;
  if (select count(*) from public.verification_tokens where token in(tok_expired,tok_used))<>0
  or (select count(*) from public.verification_tokens where token in(tok_recent,tok_active))<>2
  then raise exception 'Token expiry retention incorrect';end if;
  if has_schema_privilege('anon','dank_maintenance','USAGE')
  or has_function_privilege('anon','dank_maintenance.purge_expired_verification_tokens(integer)','EXECUTE')
  or has_function_privilege('authenticated','dank_maintenance.purge_expired_verification_tokens(integer)','EXECUTE')
  then raise exception 'Public retention function exposed';end if;
end $retention_test$;
rollback;
