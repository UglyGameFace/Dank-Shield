-- Isolated and offline rollback-only activity feed trigger tests.
begin;
do $security$
begin
if not (select relrowsecurity from pg_class where oid='public.activity_feed_events'::regclass)
or has_table_privilege('anon','public.activity_feed_events','SELECT')
or has_table_privilege('authenticated','public.activity_feed_events','INSERT')
or not has_table_privilege('service_role','public.activity_feed_events','INSERT')
then raise exception 'Private feed table contract broken';end if;
if not exists(select 1 from pg_trigger where tgrelid='public.activity_feed_events'::regclass
and tgname='trg_sync_activity_feed_meta_columns' and tgenabled='O')
then raise exception 'New-event feed normalization trigger absent';end if;
end $security$;
set local role service_role;
insert into public.activity_feed_events(guild_id,event_family,event_type,metadata,meta)
values ('990000000000000000','member','test_new_event','{"canonical":true}'::jsonb,
'{"historical":true}'::jsonb);
do $live$
begin
if not exists(select 1 from public.activity_feed_events
where guild_id='990000000000000000'
and metadata='{"canonical":true}'::jsonb
and meta='{"canonical":true}'::jsonb
and search_text='')
then raise exception 'New-event normalization missing';end if;
end $live$;
reset role;
alter table public.activity_feed_events
disable trigger trg_sync_activity_feed_meta_columns;
set local role service_role;
insert into public.activity_feed_events(guild_id,event_family,event_type,metadata,meta)
values ('990000000000000001','member','test_historical_event','{"old_metadata":1}'::jsonb,
'{"old_meta":2}'::jsonb);
do $old$
begin
if not exists(select 1 from public.activity_feed_events
where guild_id='990000000000000001'
and metadata='{"old_metadata":1}'::jsonb
and meta='{"old_meta":2}'::jsonb)
then raise exception 'Historical metadata was silently rewritten';end if;
end $old$;
reset role;
alter table public.activity_feed_events
enable trigger trg_sync_activity_feed_meta_columns;
do $reenabled$
begin
if not exists(select 1 from pg_trigger where tgrelid='public.activity_feed_events'::regclass
and tgname='trg_sync_activity_feed_meta_columns' and tgenabled='O')
then raise exception 'Feed synchronization trigger was not restored';end if;
end $reenabled$;
rollback;
