-- Synthetic profiles and card state are rolled back.
begin;
do $contract$
begin
  if (select count(*) from pg_tables where schemaname='public'
    and tablename in ('dank_profile_users','dank_profile_guild_settings','dank_live_profile_cards')) <>3 then
    raise exception 'Missing source profile tables';
  end if;
  if (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname in
     ('dank_profile_users','dank_profile_guild_settings','dank_live_profile_cards')
    and c.relrowsecurity) <>3 then raise exception 'Profile RLS missing'; end if;
  if (select pg_get_constraintdef(oid) from pg_constraint
   where conrelid='public.dank_live_profile_cards'::regclass and contype='p')
      <> 'PRIMARY KEY (guild_id, channel_id, user_id)' then
   raise exception 'Per-member live card PK missing';
  end if;
  if has_table_privilege('anon','public.dank_profile_users','SELECT')
     or has_table_privilege('authenticated','public.dank_profile_guild_settings','SELECT')
     or has_table_privilege('anon','public.dank_live_profile_cards','SELECT')
     or not has_table_privilege('service_role','public.dank_profile_users','INSERT') then
    raise exception 'Private member profile grants violated'; end if;
end $contract$;

set local role service_role;
insert into public.dank_profile_users(user_id,preferences,platforms)
values('990000000000000011','{"live_cards_enabled":true}'::jsonb,
 '{"x":{"shared":false,"username":"TEST_PRIVATE_ONLY"}}'::jsonb);
insert into public.dank_profile_guild_settings(guild_id,user_id,settings)
values('990000000000000001','990000000000000011',
 '{"show_platforms":false,"live_captions_auto_opt_in":false}'::jsonb);
insert into public.dank_live_profile_cards(
 guild_id,channel_id,user_id,message_id,trigger_message_id)
values
 ('990000000000000001','990000000000000021','990000000000000011','100','101'),
 ('990000000000000001','990000000000000021','990000000000000012','200','201');
insert into public.dank_live_profile_cards(
 guild_id,channel_id,user_id,message_id,trigger_message_id)
values ('990000000000000001','990000000000000021','990000000000000011','300','301')
on conflict (guild_id,channel_id,user_id) do update
set message_id=excluded.message_id,trigger_message_id=excluded.trigger_message_id;
do $runtime$
begin
  if (select count(*) from public.dank_live_profile_cards
      where guild_id='990000000000000001'
        and channel_id='990000000000000021') <>2 then
    raise exception 'Multiple member cards in channel lost'; end if;
  if (select message_id from public.dank_live_profile_cards
      where guild_id='990000000000000001'
        and channel_id='990000000000000021'
        and user_id='990000000000000011') <>'300' then
    raise exception 'Per-member card upsert conflict failed'; end if;
  if (select preferences->>'live_cards_enabled' from public.dank_profile_users
      where user_id='990000000000000011') <> 'true' then
    raise exception 'Profile user settings not retained';end if;
  if (select settings->>'show_platforms' from public.dank_profile_guild_settings
      where guild_id='990000000000000001'
        and user_id='990000000000000011') <> 'false' then
    raise exception 'Server privacy override missing';end if;
end $runtime$;
delete from public.dank_live_profile_cards
where guild_id='990000000000000001' and channel_id='990000000000000021'
  and user_id='990000000000000011';
do $delete$
begin
  if (select count(*) from public.dank_live_profile_cards
      where guild_id='990000000000000001'
        and channel_id='990000000000000021') <>1 then
    raise exception 'Delete one card affected another member';end if;
end $delete$;
reset role;
rollback;
