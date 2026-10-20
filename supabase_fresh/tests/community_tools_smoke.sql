-- All SQL test rows and modifications are rolled back.
begin;
do $check$
declare t text;
begin
 foreach t in array array['dank_stickies','dank_sticky_polls','dank_quiet_notices'] loop
  if to_regclass('public.'||t) is null then raise exception 'Missing Community Tools table';end if;
  if not (select relrowsecurity from pg_class where oid=to_regclass('public.'||t)) then raise exception 'Community Tools RLS off';end if;
  if has_table_privilege('anon','public.'||t,'SELECT')
    or has_table_privilege('authenticated','public.'||t,'INSERT')
    or has_table_privilege('service_role','public.'||t,'TRUNCATE')
    or not has_table_privilege('service_role','public.'||t,'INSERT')
  then raise exception 'Community Tools ACL mismatch';end if;
 end loop;
 if (select count(*) from information_schema.columns where table_schema='public' and table_name='dank_stickies')<>19 then raise exception 'Sticky columns changed';end if;
 if (select count(*) from information_schema.columns where table_schema='public' and table_name='dank_quiet_notices')<>14 then raise exception 'Quiet notice columns changed';end if;
 if (select count(*) from information_schema.columns where table_schema='public' and table_name='dank_sticky_polls')<>9 then raise exception 'Poll columns changed';end if;
end $check$;
set local role service_role;
insert into public.dank_stickies(guild_id,channel_id,content,mode)
values(990000000000000000,990000000000000010,'Synthetic sticky','plain');
insert into public.dank_quiet_notices(guild_id,channel_id,content)
values(990000000000000000,990000000000000010,'Synthetic notice');
do $rpc$
declare sticky jsonb;notice jsonb;result jsonb;
begin
 select public.save_dank_sticky_bundle(jsonb_build_object(
  'guild_id',990000000000000000::bigint,
  'channel_id',990000000000000010::bigint,
  'mode','plain','content','Updated synthetic sticky'
 ),null) into result;
 if result->'sticky'->>'content'<>'Updated synthetic sticky' then
  raise exception 'Sticky atomic save failed';end if;
 select public.record_dank_quiet_notice_activity(990000000000000000,now()) into notice;
 if notice->>'last_activity_at' is null then raise exception 'Quiet notice monotonic activity failed';end if;
 update public.dank_quiet_notices set last_notice_message_id=990000000000000020
 where guild_id=990000000000000000;
 select public.clear_dank_quiet_notice_delivery(990000000000000000,990000000000000021) into notice;
 if notice->>'last_notice_message_id'<>'990000000000000020' then
  raise exception 'Stale notice clear incorrectly succeeded';end if;
 select public.clear_dank_quiet_notice_delivery(990000000000000000,990000000000000020) into notice;
 if notice->>'last_notice_message_id' is not null then
  raise exception 'Matching notice clear failed';end if;
end $rpc$;
reset role;
rollback;
