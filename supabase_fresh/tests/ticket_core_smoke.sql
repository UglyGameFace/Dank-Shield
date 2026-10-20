-- Manual, rollback-only integration contract for the *new* Supabase project.
-- Never use this script to authorize a production cutover by itself.
begin;
do $ticket_smoke$
declare
  t_id uuid;
  gid text;
  before_events integer;
  recorded integer;
  previous_num bigint;
  ticket_floor bigint;
  first_num integer;
  second_num integer;
begin
  if (select count(*) from public.tickets) = 0
     or (select count(*) from public.ticket_counters) = 0 then
    raise exception 'Required source tickets or counters have not been imported';
  end if;
  if exists (
    select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname in
      ('tickets','ticket_categories','ticket_panels','ticket_notes',
       'ticket_messages','ticket_counters','activity_feed_events')
      and not c.relrowsecurity
  ) then
    raise exception 'A ticket or audit table lacks row-level security';
  end if;
  if has_table_privilege('anon','public.tickets','SELECT')
     or has_table_privilege('authenticated','public.ticket_notes','INSERT')
     or has_function_privilege('anon','public.reserve_ticket_number(text)','EXECUTE')
     or has_function_privilege('authenticated','public.activity_feed_log(text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,timestamptz)','EXECUTE')
  then
    raise exception 'Publicly accessible ticket/audit contract';
  end if;

  select id,guild_id into t_id,gid from public.tickets
  where status='open' order by created_at desc limit 1;
  if t_id is null then raise exception 'No open ticket for an isolated test'; end if;

  select count(*) into before_events from public.activity_feed_events;
  insert into public.ticket_messages(ticket_id,author_id,author_name,content)
    values(t_id,'111111111111111111','DB smoke','ROLLBACK ONLY');
  insert into public.ticket_notes(ticket_id,staff_id,staff_name,content)
    values(t_id,'111111111111111111','DB smoke','ROLLBACK ONLY');
  update public.tickets set status='closed' where id=t_id;
  select count(*) into recorded from public.activity_feed_events
  where guild_id=gid and event_type in ('ticket_staff_reply','ticket_note','ticket_closed');
  if recorded < 3 or (select count(*) from public.activity_feed_events)-before_events < 3 then
    raise exception 'Ticket events failed to reach the per-guild activity feed';
  end if;

  select guild_id,last_ticket_number into gid,previous_num
  from public.ticket_counters order by guild_id limit 1;
  select coalesce(max(ticket_number),0) into ticket_floor
  from public.tickets where guild_id=gid;
  first_num:=public.reserve_ticket_number(gid);
  second_num:=public.reserve_ticket_number(gid);
  if first_num<>greatest(previous_num,ticket_floor)+1 or second_num<>first_num+1 then
    raise exception 'Atomic ticket counter floor/claim failed';
  end if;
end $ticket_smoke$;
rollback;
