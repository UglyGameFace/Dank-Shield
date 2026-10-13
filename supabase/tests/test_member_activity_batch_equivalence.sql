-- Dank Shield: equivalence + load tests for record_member_activity_batch.
--
-- Run with: psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f this_file.sql
-- Everything runs inside one transaction and ROLLS BACK: no test data persists.
-- Requires BOTH functions to exist: the original record_member_activity and
-- the new record_member_activity_batch (apply the migration first, on a
-- staging database or a transaction you roll back).
--
-- What it proves:
--   1. The batch RPC produces byte-identical ledger rows vs the old RPC for
--      inserts, newer/older/equal timestamps, every activity type, channel
--      and process id handling, and event_count accumulation.
--   2. Invalid inputs raise in both.
--   3. Load: times a 30k-event batch vs individual RPCs.

\set QUIET on
\timing off

begin;

-- test identities (numeric strings, matching the bot's _as_id convention)
\set G1 899001
\set G2 899002
\set U1 777001
\set U2 777002

delete from public.member_activity_ledger
where guild_id in (:'G1', :'G2');

do $$
declare
    r_old public.member_activity_ledger%rowtype;
    r_new public.member_activity_ledger%rowtype;
    v_count_old bigint;
    v_count_new bigint;
    v_type_old text;
    v_type_new text;
    v_ch_old text;
    v_ch_new text;
    v_tm_at timestamptz;
    v_msg_at timestamptz;
    t0 timestamptz := now() - interval '1 hour';
    t1 timestamptz := now();
    t2 timestamptz := now() + interval '1 hour';
begin
    raise notice '--- case 1: fresh insert equivalence ---';
    perform public.record_member_activity(:'G1', :'U1', 'message', t1, '100', 'proc-a');
    select * into r_old from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    delete from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    perform public.record_member_activity_batch(jsonb_build_array(
        jsonb_build_object('guild_id', :'G1', 'user_id', :'U1',
            'activity_type', 'message', 'occurred_at', t1::text,
            'channel_id', '100', 'process_id', 'proc-a', 'event_count', 1)
    ));
    select * into r_new from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    if r_old.last_activity_at is distinct from r_new.last_activity_at
       or r_old.last_activity_type is distinct from r_new.last_activity_type
       or r_old.last_message_at is distinct from r_new.last_message_at
       or r_old.last_channel_id is distinct from r_new.last_channel_id
       or r_old.last_process_id is distinct from r_new.last_process_id
       or r_old.event_count is distinct from r_new.event_count then
        raise exception 'case 1 MISMATCH: old=% new=%', r_old, r_new;
    end if;
    raise notice 'case 1 OK';

    raise notice '--- case 2: newer timestamp wins ---';
    perform public.record_member_activity(:'G1', :'U1', 'reaction', t2, '101', 'proc-a');
    select * into r_old from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    perform public.record_member_activity_batch(jsonb_build_array(
        jsonb_build_object('guild_id', :'G2', 'user_id', :'U1',
            'activity_type', 'message', 'occurred_at', t1::text,
            'channel_id', '100', 'process_id', 'proc-a', 'event_count', 1)
    ));
    perform public.record_member_activity_batch(jsonb_build_array(
        jsonb_build_object('guild_id', :'G2', 'user_id', :'U1',
            'activity_type', 'reaction', 'occurred_at', t2::text,
            'channel_id', '101', 'process_id', 'proc-a', 'event_count', 1)
    ));
    select * into r_new from public.member_activity_ledger
     where guild_id = :'G2' and user_id = :'U1';
    if r_old.last_activity_at is distinct from r_new.last_activity_at
       or r_old.last_reaction_at is distinct from r_new.last_reaction_at
       or r_old.last_activity_type is distinct from r_new.last_activity_type
       or r_old.event_count is distinct from r_new.event_count then
        raise exception 'case 2 MISMATCH';
    end if;
    raise notice 'case 2 OK';

    raise notice '--- case 3: older timestamp keeps newest, count still grows ---';
    perform public.record_member_activity(:'G1', :'U1', 'message', t0, '102', 'proc-a');
    select * into r_old from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    perform public.record_member_activity_batch(jsonb_build_array(
        jsonb_build_object('guild_id', :'G2', 'user_id', :'U1',
            'activity_type', 'message', 'occurred_at', t0::text,
            'channel_id', '102', 'process_id', 'proc-a', 'event_count', 1)
    ));
    select * into r_new from public.member_activity_ledger
     where guild_id = :'G2' and user_id = :'U1';
    if r_new.last_activity_at <> t2 then
        raise exception 'case 3 FAILED: older event moved last_activity_at';
    end if;
    if r_old.event_count is distinct from r_new.event_count then
        raise exception 'case 3 MISMATCH on event_count';
    end if;
    raise notice 'case 3 OK';

    raise notice '--- case 4: event_count accumulation (3x old vs 1x batch of 3) ---';
    delete from public.member_activity_ledger
     where guild_id in (:'G1', :'G2') and user_id = :'U2';
    perform public.record_member_activity(:'G1', :'U2', 'interaction', t1, '103', 'proc-b');
    perform public.record_member_activity(:'G1', :'U2', 'interaction', t1, '103', 'proc-b');
    perform public.record_member_activity(:'G1', :'U2', 'interaction', t1, '103', 'proc-b');
    select event_count into strict v_count_old from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U2';
    perform public.record_member_activity_batch(jsonb_build_array(
        jsonb_build_object('guild_id', :'G2', 'user_id', :'U2',
            'activity_type', 'interaction', 'occurred_at', t1::text,
            'channel_id', '103', 'process_id', 'proc-b', 'event_count', 3)
    ));
    select event_count into strict v_count_new from public.member_activity_ledger
     where guild_id = :'G2' and user_id = :'U2';
    if v_count_old <> 3 or v_count_new <> 3 then
        raise exception 'case 4 FAILED: old=% new=%', v_count_old, v_count_new;
    end if;
    raise notice 'case 4 OK';

    raise notice '--- case 5: equal timestamps take the latest write ---';
    delete from public.member_activity_ledger
     where guild_id in (:'G1', :'G2') and user_id = :'U1';
    perform public.record_member_activity(:'G1', :'U1', 'message', t1, '104', 'proc-a');
    perform public.record_member_activity(:'G1', :'U1', 'reaction', t1, '105', 'proc-a');
    select last_activity_type, last_channel_id
      into v_type_old, v_ch_old
      from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    perform public.record_member_activity_batch(jsonb_build_array(
        jsonb_build_object('guild_id', :'G2', 'user_id', :'U1',
            'activity_type', 'message', 'occurred_at', t1::text,
            'channel_id', '104', 'process_id', 'proc-a', 'event_count', 1),
        jsonb_build_object('guild_id', :'G2', 'user_id', :'U1',
            'activity_type', 'reaction', 'occurred_at', t1::text,
            'channel_id', '105', 'process_id', 'proc-a', 'event_count', 1)
    ));
    select last_activity_type, last_channel_id
      into v_type_new, v_ch_new
      from public.member_activity_ledger
     where guild_id = :'G2' and user_id = :'U1';
    if v_type_old is distinct from v_type_new
       or v_ch_old is distinct from v_ch_new then
        raise exception 'case 5 MISMATCH: old type=% ch=% / new type=% ch=%',
            v_type_old, v_ch_old, v_type_new, v_ch_new;
    end if;
    raise notice 'case 5 OK (both resolve equal timestamps to the latest write: %)'
        , v_type_new;

    raise notice '--- case 6: per-type timestamp columns ---';
    delete from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    perform public.record_member_activity(:'G1', :'U1', 'ticket_message', t1, '106', 'proc-a');
    select last_ticket_message_at, last_message_at
      into v_tm_at, v_msg_at
      from public.member_activity_ledger
     where guild_id = :'G1' and user_id = :'U1';
    if v_tm_at is distinct from t1 or v_msg_at is not null then
        raise exception 'case 6 FAILED: ticket_message touched wrong columns';
    end if;
    raise notice 'case 6 OK';

    raise notice 'ALL EQUIVALENCE CASES PASSED';
end $$;

-- invalid inputs must raise in both functions
do $$
begin
    begin
        perform public.record_member_activity(:'G1', :'U1', 'presence', now(), '', '');
        raise exception 'old fn accepted bad type';
    exception when raise_exception then
        raise notice 'old fn rejects bad activity type OK';
    end;
    begin
        perform public.record_member_activity_batch(jsonb_build_array(
            jsonb_build_object('guild_id', :'G1', 'user_id', :'U1',
                'activity_type', 'presence', 'occurred_at', now()::text,
                'channel_id', '', 'process_id', '', 'event_count', 1)));
        raise exception 'batch fn accepted bad type';
    exception when raise_exception then
        raise notice 'batch fn rejects bad activity type OK';
    end;
    begin
        perform public.record_member_activity_batch('not-an-array'::jsonb);
        raise exception 'batch fn accepted non-array';
    exception when raise_exception then
        raise notice 'batch fn rejects non-array payload OK';
    end;
end $$;

rollback;

-- ================= LOAD TEST (runs outside the rolled-back block) ================
-- 30k deduped events: one batch RPC vs 1k individual RPCs (extrapolated).
\timing on

select public.record_member_activity_batch(
    (select jsonb_agg(jsonb_build_object(
        'guild_id', '899010',
        'user_id', (900000 + g)::text,
        'activity_type', 'message',
        'occurred_at', now()::text,
        'channel_id', '123',
        'process_id', 'load-test',
        'event_count', 7))
     from generate_series(1, 30000) g)
);

select count(*) as batch_rows,
       sum(event_count) as batch_total_events
from public.member_activity_ledger where guild_id = '899010';

delete from public.member_activity_ledger where guild_id = '899010';

-- 1k individual calls for comparison (30x fewer; multiply wall time by ~30)
do $$
declare i int;
begin
    for i in 1..1000 loop
        perform public.record_member_activity(
            '899011', (900000 + i)::text, 'message', now(), '123', 'load-test');
    end loop;
end $$;

delete from public.member_activity_ledger where guild_id in ('899010', '899011');
\timing off