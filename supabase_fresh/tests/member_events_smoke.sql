-- Candidate only: synthetic trigger and historical replay tests roll back.
begin;
do $scope$
begin
 if (select count(*) from information_schema.columns where table_schema='public' and table_name='member_events')<>13
 then raise exception 'Member event column mismatch';end if;
 if not (select relrowsecurity from pg_class where oid='public.member_events'::regclass)
 then raise exception 'Member event RLS absent';end if;
 if has_table_privilege('anon','public.member_events','SELECT')
 or has_table_privilege('authenticated','public.member_events','INSERT')
 or not has_table_privilege('service_role','public.member_events','INSERT')
 then raise exception 'Member events private ACL failure';end if;
 if not exists(select 1 from pg_trigger where tgrelid='public.member_events'::regclass and tgname='member_events_to_activity_feed_ai' and tgenabled='O')
 then raise exception 'Member timeline trigger missing';end if;
end $scope$;
set local role service_role;
insert into public.member_events(guild_id,user_id,event_type,title,reason)
values ('990000000000000000','990000000000000001','member_role_update','Synthetic event','synthetic only');
do $trigger$
begin
if (select count(*) from public.activity_feed_events where guild_id='990000000000000000' and related_table='member_events')<>1 then
raise exception 'Member event live activity feed trigger not functioning';
end if;
end $trigger$;
reset role;
-- Data backfills explicitly disable only the member_events trigger; no old
-- synthetic replay into the current activity feed timeline.
alter table public.member_events disable trigger member_events_to_activity_feed_ai;
insert into public.member_events(guild_id,user_id,event_type)
values('990000000000000001','990000000000000002','historic_backfill');
alter table public.member_events enable trigger member_events_to_activity_feed_ai;
do $history$
begin
if (select count(*) from public.activity_feed_events where guild_id='990000000000000001' and related_table='member_events')<>0
then raise exception 'History backfill replayed stale activity';end if;
end $history$;
rollback;
