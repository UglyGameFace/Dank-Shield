-- Run on the isolated new DankShield project ONLY.
-- All mutations are ephemeral and must ROLLBACK.
begin;
do $member_test$
declare
  fake_guild text := '999999999999999997';
  fake_user text := '999999999999999998';
  batch uuid := gen_random_uuid();
  event_payload jsonb;
  n bigint;
  before_versions integer;
  before_events integer;
  cfg_guild text;
  category_id uuid;
begin
  if exists(select 1 from public.member_activity_ledger where guild_id=fake_guild and user_id=fake_user)
  then raise exception 'Synthetic test user already exists';end if;
  if has_function_privilege('anon','public.record_member_activity_batch(uuid,jsonb)','EXECUTE')
  or has_function_privilege('authenticated','public.record_member_activity_batch(uuid,jsonb)','EXECUTE')
  or has_table_privilege('anon','public.guild_members','SELECT')
  then raise exception 'Member state is exposed to untrusted API roles';end if;
  event_payload:=jsonb_build_array(jsonb_build_object(
   'guild_id',fake_guild,'user_id',fake_user,'activity_type','message',
   'occurred_at',now()::text,'first_occurred_at',now()::text,
   'channel_id','777777777777777777','process_id','migration-smoke',
   'event_count',3));
  perform public.record_member_activity_batch(batch,event_payload);
  perform public.record_member_activity_batch(batch,event_payload);
  select event_count into n from public.member_activity_ledger
    where guild_id=fake_guild and user_id=fake_user;
  if n<>3 then raise exception 'Activity batch replay counted twice';end if;
  perform public.ack_member_activity_batches(array[batch]);
  if exists(select 1 from public.member_activity_batch_receipts where batch_id=batch)
    then raise exception 'Activity receipt acknowledgement failed';end if;

  select count(*) into before_versions from public.guild_config_versions;
  select guild_id into cfg_guild from public.guild_configs order by guild_id limit 1;
  update public.guild_configs set guild_name=coalesce(guild_name,'')||' migration-rollback'
    where guild_id=cfg_guild;
  select id into category_id from public.ticket_categories where guild_id=cfg_guild order by id limit 1;
  if category_id is null then
    select id into category_id from public.ticket_categories order by id limit 1;
  end if;
  update public.ticket_categories set sort_order=sort_order+1 where id=category_id;
  if (select count(*) from public.guild_config_versions)<>before_versions+2
  then raise exception 'Config-version audit triggers failed';end if;

  select count(*) into before_events from public.activity_feed_events;
  insert into public.member_joins
  select * from jsonb_populate_record(null::public.member_joins,
   (select to_jsonb(j)||jsonb_build_object('id',gen_random_uuid(),'user_id',fake_user)
    from public.member_joins j order by id limit 1));
  if (select count(*) from public.activity_feed_events)<>before_events+1
  then raise exception 'Member join audit event missing';end if;
end $member_test$;
rollback;
