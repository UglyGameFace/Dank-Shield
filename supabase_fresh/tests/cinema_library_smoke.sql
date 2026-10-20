-- All synthetic user/guild/media rows rollback. Execute on dedicated candidate only.
begin;
do $inventory$
declare t text;
begin
foreach t in array array[
 'dank_cinema_users','dank_cinema_user_media','dank_cinema_feed_discoveries',
 'dank_cinema_notifications','dank_cinema_feed_rules',
 'dank_cinema_user_feed_discoveries','dank_cinema_feed_source_health',
 'dank_cinema_watch_sessions','dank_cinema_lists','dank_cinema_list_items'
] loop
 if to_regclass('public.'||t) is null then raise exception 'Missing Cinema table %',t;end if;
 if not (select relrowsecurity from pg_class where oid=('public.'||t)::regclass) then
  raise exception 'Missing Cinema RLS %',t;end if;
 if has_table_privilege('anon','public.'||t,'SELECT')
    or has_table_privilege('authenticated','public.'||t,'INSERT')
    or has_table_privilege('service_role','public.'||t,'TRUNCATE')
    or not has_table_privilege('service_role','public.'||t,'UPDATE') then
  raise exception 'Cinema table role grants wrong %',t;end if;
end loop;
if not exists(select 1 from pg_constraint where conrelid='public.dank_cinema_watch_sessions'::regclass
 and conname='dank_cinema_watch_sessions_mode_check'
 and pg_get_constraintdef(oid) like '%standalone%') then
 raise exception 'Standalone session constraint absent';end if;
end $inventory$;
set local role service_role;
do $runtime$
declare v_list_id uuid;
begin
 if exists(select 1 from public.dank_cinema_users
  where user_id=990000000000000010::bigint) then
  raise exception 'Synthetic user collides with live data';end if;
 insert into public.dank_cinema_users(user_id,preferences)
 values(990000000000000010::bigint,'{"autoplay_next":false}'::jsonb);
 insert into public.dank_cinema_user_media(
  user_id,media_type,tmdb_id,season_number,episode_number,title,
  progress_seconds,duration_seconds,watchlisted)
 values(990000000000000010::bigint,'episode',98765,2,4,'Test Episode',111,2700,true);
 insert into public.dank_cinema_user_media(
  user_id,media_type,tmdb_id,season_number,episode_number,title,
  progress_seconds,duration_seconds,watchlisted)
 values(990000000000000010::bigint,'episode',98765,2,4,'Test Episode',222,2700,true)
 on conflict(user_id,media_type,tmdb_id,season_number,episode_number)
 do update set progress_seconds=excluded.progress_seconds;
 if (select progress_seconds from public.dank_cinema_user_media
  where user_id=990000000000000010::bigint and tmdb_id=98765)<>222 then
  raise exception 'Cinema resume upsert failed';end if;
 insert into public.dank_cinema_watch_sessions(
  user_id,session_key,guild_id,room_id,session_mode,media_type,tmdb_id,title)
 values(990000000000000010::bigint,'test-standalone',990000000000000000::bigint,
  'test-room','standalone','episode',98765,'Test Episode');
 insert into public.dank_cinema_feed_discoveries(
  guild_id,source_id,discovery_key,title,media_type,tmdb_id)
 values(990000000000000000::bigint,'test-feed','movie:456','Test Movie','movie',456);
 insert into public.dank_cinema_feed_rules(
  guild_id,owner_user_id,scope,rule_type,name,query,created_by)
 values(990000000000000000::bigint,990000000000000010::bigint,
  'user','saved_search','Test Rule','Test Show',990000000000000010::bigint);
 insert into public.dank_cinema_user_feed_discoveries(
  user_id,guild_id,source_id,discovery_key,title)
 values(990000000000000010::bigint,990000000000000000::bigint,
  'test-feed','test-discovery','Private Feed');
 insert into public.dank_cinema_feed_source_health(
  guild_id,source_id,refresh_count,success_count)
 values(990000000000000000::bigint,'test-feed',2,1);
 insert into public.dank_cinema_notifications(
  user_id,guild_id,kind,title,dedupe_key)
 values(990000000000000010::bigint,990000000000000000::bigint,
  'watch_party_invite','Test Invitation','test-notify');
 insert into public.dank_cinema_lists(user_id,name)
 values(990000000000000010::bigint,'Test List') returning id into v_list_id;
 insert into public.dank_cinema_list_items(list_id,user_id,media_type,tmdb_id,title)
 values(v_list_id,990000000000000010::bigint,'movie',456,'Test Movie');
 if not exists(select 1 from public.dank_cinema_list_items where list_id=v_list_id) then
   raise exception 'Custom list item not saved';end if;
 if exists(select 1 from public.dank_cinema_feed_rules
  where guild_id=990000000000000001::bigint) then
  raise exception 'Cross-server Cinema row contamination';end if;
end $runtime$;
reset role;
rollback;
