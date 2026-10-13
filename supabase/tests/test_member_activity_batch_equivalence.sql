-- Run in staging: psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f this_file.sql
-- All fixtures, including load measurements, are enclosed by ROLLBACK.
-- This tests PostgreSQL work. It does not measure Supabase HTTP round-trips.
begin;
delete from public.member_activity_ledger where guild_id in ('899001','899002','899010','899011');
delete from public.member_activity_batch_receipts
where batch_id in (
 '00000000-0000-0000-0000-000000000001',
 '00000000-0000-0000-0000-000000000002',
 '00000000-0000-0000-0000-000000000003',
 '00000000-0000-0000-0000-000000000004'
);

do $$
declare
    t1 timestamptz := '2026-10-13T12:00:00+00';
    t2 timestamptz := '2026-10-13T12:05:00+00';
    old_row public.member_activity_ledger%rowtype;
    new_row public.member_activity_ledger%rowtype;
    rejected boolean;
begin
    -- Same logical history: two messages, one reaction at a newer timestamp.
    perform public.record_member_activity('899001','777001','message',t1,'100','proc-a');
    perform public.record_member_activity('899001','777001','message',t1,'100','proc-a');
    perform public.record_member_activity('899001','777001','reaction',t2,'101','proc-a');
    select * into strict old_row from public.member_activity_ledger
      where guild_id='899001' and user_id='777001';

    perform public.record_member_activity_batch(
      '00000000-0000-0000-0000-000000000001'::uuid,
      jsonb_build_array(
        jsonb_build_object('guild_id','899002','user_id','777001',
          'activity_type','message','occurred_at',t1::text,
          'first_occurred_at',t1::text,'channel_id','100','process_id','proc-a',
          'event_count',2),
        jsonb_build_object('guild_id','899002','user_id','777001',
          'activity_type','reaction','occurred_at',t2::text,
          'first_occurred_at',t2::text,'channel_id','101','process_id','proc-a',
          'event_count',1)
      )
    );
    select * into strict new_row from public.member_activity_ledger
      where guild_id='899002' and user_id='777001';
    if old_row.last_activity_at is distinct from new_row.last_activity_at
       or old_row.last_activity_type is distinct from new_row.last_activity_type
       or old_row.last_message_at is distinct from new_row.last_message_at
       or old_row.last_reaction_at is distinct from new_row.last_reaction_at
       or old_row.last_channel_id is distinct from new_row.last_channel_id
       or old_row.first_observed_at is distinct from new_row.first_observed_at
       or old_row.event_count is distinct from new_row.event_count then
        raise exception 'ledger equivalence failed: % vs %', old_row, new_row;
    end if;
    raise notice 'ledger timestamp/type/count equivalence passed';

    -- Replay the exact same batch after an HTTP acknowledgement is lost.
    perform public.record_member_activity_batch(
      '00000000-0000-0000-0000-000000000001'::uuid,
      jsonb_build_array(jsonb_build_object(
        'guild_id','899002','user_id','777001','activity_type','message',
        'occurred_at',t2::text,'channel_id','300','process_id','proc-a',
        'event_count',99))
    );
    select * into strict new_row from public.member_activity_ledger
      where guild_id='899002' and user_id='777001';
    if new_row.event_count <> 3 or new_row.last_channel_id <> '101' then
        raise exception 'idempotency failed: repeat batch mutated ledger';
    end if;

    -- An invalid second entry rolls the whole batch AND its receipt back.
    rejected := false;
    begin
      perform public.record_member_activity_batch(
        '00000000-0000-0000-0000-000000000002'::uuid,
        jsonb_build_array(
          jsonb_build_object('guild_id','899002','user_id','777002',
            'activity_type','message','occurred_at',t1::text,
            'event_count',1),
          jsonb_build_object('guild_id','899002','user_id','777002',
            'activity_type','invalid','occurred_at',t1::text,
            'event_count',1)
        )
      );
    exception when others then rejected := true;
    end;
    if not rejected then raise exception 'bad activity type was accepted'; end if;
    if exists(select 1 from public.member_activity_ledger
              where guild_id='899002' and user_id='777002')
       or exists(select 1 from public.member_activity_batch_receipts
              where batch_id='00000000-0000-0000-0000-000000000002'::uuid) then
       raise exception 'rejected batch wrote partial state';
    end if;

    rejected := false;
    begin
      perform public.record_member_activity_batch(
        '00000000-0000-0000-0000-000000000003'::uuid,
        '{"not":"array"}'::jsonb
      );
    exception when others then rejected := true;
    end;
    if not rejected then raise exception 'non-array batch was accepted'; end if;

    -- Equal timestamps should respect event insertion order.
    perform public.record_member_activity_batch(
      '00000000-0000-0000-0000-000000000004'::uuid,
      jsonb_build_array(
        jsonb_build_object('guild_id','899002','user_id','777003',
          'activity_type','message','occurred_at',t1::text,'event_count',1),
        jsonb_build_object('guild_id','899002','user_id','777003',
          'activity_type','interaction','occurred_at',t1::text,'event_count',1)
      )
    );
    select * into strict new_row from public.member_activity_ledger
      where guild_id='899002' and user_id='777003';
    if new_row.last_activity_type <> 'interaction' or new_row.event_count <> 2 then
        raise exception 'equal timestamp ordering was lost';
    end if;
    raise notice 'idempotency, rollback and tie tests passed';

    -- SECURITY DEFINER functions must not inherit PUBLIC executable rights.
    if has_function_privilege(
        'anon', 'public.record_member_activity_batch(uuid,jsonb)', 'EXECUTE'
    ) or has_function_privilege(
        'authenticated', 'public.record_member_activity_batch(uuid,jsonb)',
        'EXECUTE'
    ) then
        raise exception 'batch RPC leaked execute privilege to public API roles';
    end if;
    if not has_function_privilege(
        'service_role', 'public.record_member_activity_batch(uuid,jsonb)',
        'EXECUTE'
    ) then
        raise exception 'service_role cannot execute batch RPC';
    end if;
end $;

-- Size-bounded 500-entry load run. Timed as PostgreSQL work, not HTTP calls.
\timing on
select public.record_member_activity_batch(
 '00000000-0000-0000-0000-000000000005'::uuid,
 (select jsonb_agg(jsonb_build_object(
    'guild_id','899010','user_id',(900000+g)::text,
    'activity_type','message','occurred_at',now()::text,
    'channel_id','123','process_id','load-test','event_count',1))
  from generate_series(1,500) g)
);
do $$
declare i int;
begin
    for i in 1..500 loop
        perform public.record_member_activity(
          '899011', (900000+i)::text, 'message', now(), '123', 'load-test'
        );
    end loop;
end $$;
\timing off
rollback;
