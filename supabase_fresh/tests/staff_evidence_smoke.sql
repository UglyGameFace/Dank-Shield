-- Rollback-only permissions, guild-scoped cleanup defaults and moderation evidence.
begin;
do $assert$
declare tab text; op text;
begin
 foreach tab in array array['member_cleanup_settings','staff_action_logs'] loop
  if not (select relrowsecurity from pg_class where oid=to_regclass('public.'||tab))
  then raise exception 'Sensitive table RLS disabled';end if;
  foreach op in array array['SELECT','INSERT','UPDATE','DELETE'] loop
   if has_table_privilege('anon','public.'||tab,op)
   or has_table_privilege('authenticated','public.'||tab,op)
   or not has_table_privilege('service_role','public.'||tab,op)
   then raise exception 'Sensitive table permissions mismatch';end if;
  end loop;
  if has_table_privilege('service_role','public.'||tab,'TRUNCATE') then
   raise exception 'History/cleanup truncate prohibited';end if;
 end loop;
 if (select count(*) from information_schema.columns where table_schema='public' and table_name='member_cleanup_settings')<>6
 or (select count(*) from information_schema.columns where table_schema='public' and table_name='staff_action_logs')<>11
 then raise exception 'Source column contract mismatch';end if;
end $assert$;
-- Normal CI SQL starts with empty tables. The offline dedicated target may
-- contain staged staff evidence when this test is run separately post-stage.
set local role service_role;
insert into public.member_cleanup_settings(guild_id)
values('990000000000000000');
do $cleanup$
begin
 if not exists(select 1 from public.member_cleanup_settings
 where guild_id='990000000000000000'
 and require_queue_confirmation=true
 and allow_low_confidence_queue=false
 and default_queue_limit=10)
 then raise exception 'Member cleanup safe defaults changed';end if;
end $cleanup$;
insert into public.staff_action_logs(id,guild_id,actor_display,action,reason)
values(-990000000000000001,'990000000000000000','TEST ONLY','test-history','ROLLBACK ONLY');
do $history$
begin
 if (select count(*) from public.staff_action_logs
 where id=-990000000000000001 and action='test-history')<>1
 then raise exception 'Private historical moderation insert failed';end if;
end $history$;
reset role;
rollback;
