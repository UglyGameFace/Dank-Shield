-- Isolated new DankShield project viamriafkbqhquvrsnua.
-- Restore member activity tracker and batched ledger/RPC behavior.
-- SECURITY DEFINER functions strictly grant execute to service_role only.
-- No old batch receipts / ephemeral owner-process locks imported.
CREATE OR REPLACE FUNCTION public.ack_member_activity_batches(p_batch_ids uuid[])
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
begin
    if p_batch_ids is null or array_length(p_batch_ids, 1) > 128 then
        raise exception 'expected up to 128 acknowledged batch IDs';
    end if;
    delete from public.member_activity_batch_receipts
    where batch_id = any(p_batch_ids);
end;
$function$;
alter function public.ack_member_activity_batches(uuid[]) set search_path=pg_catalog,public,pg_temp;
revoke all on function public.ack_member_activity_batches(uuid[]) from public,anon,authenticated;
grant execute on function public.ack_member_activity_batches(uuid[]) to service_role;
CREATE OR REPLACE FUNCTION public.fail_member_activity_tracker(p_guild_id text, p_process_id text, p_failed_at timestamp with time zone, p_error text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
begin
    insert into public.member_activity_tracker_state (
        guild_id,
        process_id,
        continuous_since,
        last_heartbeat_at,
        last_error_at,
        last_error,
        event_writes_failed,
        updated_at
    )
    values (
        p_guild_id,
        p_process_id,
        p_failed_at,
        p_failed_at,
        p_failed_at,
        left(coalesce(p_error, 'unknown tracker failure'), 1000),
        1,
        p_failed_at
    )
    on conflict (guild_id)
    do update set
        process_id = excluded.process_id,
        continuous_since = excluded.continuous_since,
        last_heartbeat_at = excluded.last_heartbeat_at,
        last_error_at = excluded.last_error_at,
        last_error = excluded.last_error,
        event_writes_failed =
            public.member_activity_tracker_state.event_writes_failed + 1,
        updated_at = excluded.updated_at;
end;
$function$;
alter function public.fail_member_activity_tracker(text,text,timestamp with time zone,text) set search_path=pg_catalog,public,pg_temp;
revoke all on function public.fail_member_activity_tracker(text,text,timestamp with time zone,text) from public,anon,authenticated;
grant execute on function public.fail_member_activity_tracker(text,text,timestamp with time zone,text) to service_role;
CREATE OR REPLACE FUNCTION public.heartbeat_member_activity_tracker(p_guild_id text, p_process_id text, p_heartbeat_at timestamp with time zone, p_max_gap_seconds integer)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
begin
    insert into public.member_activity_tracker_state (
        guild_id,
        process_id,
        continuous_since,
        last_heartbeat_at,
        updated_at
    )
    values (
        p_guild_id,
        p_process_id,
        p_heartbeat_at,
        p_heartbeat_at,
        p_heartbeat_at
    )
    on conflict (guild_id)
    do update set
        continuous_since = case
            when public.member_activity_tracker_state.process_id
                    <> excluded.process_id
                or public.member_activity_tracker_state.last_heartbeat_at
                    < p_heartbeat_at
                      - make_interval(
                            secs => greatest(
                                coalesce(p_max_gap_seconds, 180),
                                60
                            )
                        )
            then p_heartbeat_at
            else public.member_activity_tracker_state.continuous_since
        end,
        process_id = excluded.process_id,
        last_heartbeat_at = excluded.last_heartbeat_at,
        updated_at = excluded.updated_at;
end;
$function$;
alter function public.heartbeat_member_activity_tracker(text,text,timestamp with time zone,integer) set search_path=pg_catalog,public,pg_temp;
revoke all on function public.heartbeat_member_activity_tracker(text,text,timestamp with time zone,integer) from public,anon,authenticated;
grant execute on function public.heartbeat_member_activity_tracker(text,text,timestamp with time zone,integer) to service_role;
CREATE OR REPLACE FUNCTION public.record_member_activity(p_guild_id text, p_user_id text, p_activity_type text, p_occurred_at timestamp with time zone, p_channel_id text, p_process_id text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
begin
    if p_guild_id is null or btrim(p_guild_id) = '' then
        raise exception 'guild_id is required';
    end if;

    if p_user_id is null or btrim(p_user_id) = '' then
        raise exception 'user_id is required';
    end if;

    if p_activity_type not in (
        'message',
        'reaction',
        'interaction',
        'ticket_message'
    ) then
        raise exception 'unsupported direct activity type: %', p_activity_type;
    end if;

    insert into public.member_activity_ledger (
        guild_id,
        user_id,
        last_activity_at,
        last_activity_type,
        last_message_at,
        last_reaction_at,
        last_interaction_at,
        last_ticket_message_at,
        last_channel_id,
        last_process_id,
        event_count,
        first_observed_at,
        updated_at
    )
    values (
        p_guild_id,
        p_user_id,
        p_occurred_at,
        p_activity_type,
        case when p_activity_type = 'message'
            then p_occurred_at end,
        case when p_activity_type = 'reaction'
            then p_occurred_at end,
        case when p_activity_type = 'interaction'
            then p_occurred_at end,
        case when p_activity_type = 'ticket_message'
            then p_occurred_at end,
        nullif(p_channel_id, ''),
        nullif(p_process_id, ''),
        1,
        p_occurred_at,
        p_occurred_at
    )
    on conflict (guild_id, user_id)
    do update set
        last_activity_at = greatest(
            public.member_activity_ledger.last_activity_at,
            excluded.last_activity_at
        ),
        last_activity_type = case
            when excluded.last_activity_at
                >= public.member_activity_ledger.last_activity_at
            then excluded.last_activity_type
            else public.member_activity_ledger.last_activity_type
        end,
        last_message_at = case
            when p_activity_type = 'message'
            then greatest(
                coalesce(
                    public.member_activity_ledger.last_message_at,
                    p_occurred_at
                ),
                p_occurred_at
            )
            else public.member_activity_ledger.last_message_at
        end,
        last_reaction_at = case
            when p_activity_type = 'reaction'
            then greatest(
                coalesce(
                    public.member_activity_ledger.last_reaction_at,
                    p_occurred_at
                ),
                p_occurred_at
            )
            else public.member_activity_ledger.last_reaction_at
        end,
        last_interaction_at = case
            when p_activity_type = 'interaction'
            then greatest(
                coalesce(
                    public.member_activity_ledger.last_interaction_at,
                    p_occurred_at
                ),
                p_occurred_at
            )
            else public.member_activity_ledger.last_interaction_at
        end,
        last_ticket_message_at = case
            when p_activity_type = 'ticket_message'
            then greatest(
                coalesce(
                    public.member_activity_ledger.last_ticket_message_at,
                    p_occurred_at
                ),
                p_occurred_at
            )
            else public.member_activity_ledger.last_ticket_message_at
        end,
        last_channel_id = case
            when excluded.last_activity_at
                >= public.member_activity_ledger.last_activity_at
            then excluded.last_channel_id
            else public.member_activity_ledger.last_channel_id
        end,
        last_process_id = excluded.last_process_id,
        event_count = public.member_activity_ledger.event_count + 1,
        updated_at = greatest(
            public.member_activity_ledger.updated_at,
            excluded.updated_at
        );
end;
$function$;
alter function public.record_member_activity(text,text,text,timestamp with time zone,text,text) set search_path=pg_catalog,public,pg_temp;
revoke all on function public.record_member_activity(text,text,text,timestamp with time zone,text,text) from public,anon,authenticated;
grant execute on function public.record_member_activity(text,text,text,timestamp with time zone,text,text) to service_role;
CREATE OR REPLACE FUNCTION public.record_member_activity_batch(p_batch_id uuid, p_events jsonb)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
declare
    e jsonb;
    v_guild_id text;
    v_user_id text;
    v_activity_type text;
    v_occurred_at timestamptz;
    v_channel_id text;
    v_process_id text;
    v_event_count bigint;
begin
    if p_batch_id is null then
        raise exception 'p_batch_id is required';
    end if;
    if p_events is null or jsonb_typeof(p_events) != 'array' then
        raise exception 'p_events must be a JSON array';
    end if;
    if jsonb_array_length(p_events) < 1
        or jsonb_array_length(p_events) > 500 then
        raise exception 'p_events must contain between 1 and 500 entries';
    end if;

    insert into public.member_activity_batch_receipts(batch_id)
    values (p_batch_id) on conflict do nothing;
    if not found then
        return;
    end if;

    for e in select * from jsonb_array_elements(p_events)
    loop
        v_guild_id := nullif(btrim(e->>'guild_id'), '');
        v_user_id := nullif(btrim(e->>'user_id'), '');
        v_activity_type := btrim(e->>'activity_type');
        v_occurred_at := (e->>'occurred_at')::timestamptz;
        v_channel_id := nullif(btrim(e->>'channel_id'), '');
        v_process_id := nullif(btrim(e->>'process_id'), '');
        v_event_count := greatest(coalesce((e->>'event_count')::bigint, 1), 1);

        if v_guild_id is null then
            raise exception 'guild_id is required';
        end if;

        if v_user_id is null then
            raise exception 'user_id is required';
        end if;

        if v_activity_type not in (
            'message',
            'reaction',
            'interaction',
            'ticket_message'
        ) then
            raise exception 'unsupported direct activity type: %', v_activity_type;
        end if;

        insert into public.member_activity_ledger (
            guild_id,
            user_id,
            last_activity_at,
            last_activity_type,
            last_message_at,
            last_reaction_at,
            last_interaction_at,
            last_ticket_message_at,
            last_channel_id,
            last_process_id,
            event_count,
            first_observed_at,
            updated_at
        )
        values (
            v_guild_id,
            v_user_id,
            v_occurred_at,
            v_activity_type,
            case when v_activity_type = 'message'
                then v_occurred_at end,
            case when v_activity_type = 'reaction'
                then v_occurred_at end,
            case when v_activity_type = 'interaction'
                then v_occurred_at end,
            case when v_activity_type = 'ticket_message'
                then v_occurred_at end,
            v_channel_id,
            v_process_id,
            v_event_count,
            coalesce((e->>'first_occurred_at')::timestamptz, v_occurred_at),
            v_occurred_at
        )
        on conflict (guild_id, user_id)
        do update set
            last_activity_at = greatest(
                public.member_activity_ledger.last_activity_at,
                excluded.last_activity_at
            ),
            last_activity_type = case
                when excluded.last_activity_at
                    >= public.member_activity_ledger.last_activity_at
                then excluded.last_activity_type
                else public.member_activity_ledger.last_activity_type
            end,
            last_message_at = case
                when v_activity_type = 'message'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_message_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_message_at
            end,
            last_reaction_at = case
                when v_activity_type = 'reaction'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_reaction_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_reaction_at
            end,
            last_interaction_at = case
                when v_activity_type = 'interaction'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_interaction_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_interaction_at
            end,
            last_ticket_message_at = case
                when v_activity_type = 'ticket_message'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_ticket_message_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_ticket_message_at
            end,
            last_channel_id = case
                when excluded.last_activity_at
                    >= public.member_activity_ledger.last_activity_at
                then excluded.last_channel_id
                else public.member_activity_ledger.last_channel_id
            end,
            last_process_id = excluded.last_process_id,
            event_count = public.member_activity_ledger.event_count
                + v_event_count,
            first_observed_at = least(
                public.member_activity_ledger.first_observed_at,
                excluded.first_observed_at
            ),
            updated_at = greatest(
                public.member_activity_ledger.updated_at,
                excluded.updated_at
            );
    end loop;
end;
$function$;
alter function public.record_member_activity_batch(uuid,jsonb) set search_path=pg_catalog,public,pg_temp;
revoke all on function public.record_member_activity_batch(uuid,jsonb) from public,anon,authenticated;
grant execute on function public.record_member_activity_batch(uuid,jsonb) to service_role;
CREATE OR REPLACE FUNCTION public.resume_member_activity_tracker(p_guild_id text, p_previous_process_id text, p_new_process_id text, p_previous_heartbeat_at timestamp with time zone, p_resumed_at timestamp with time zone)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
    changed_rows integer := 0;
begin
    if p_guild_id is null or btrim(p_guild_id) = '' then
        raise exception 'guild_id is required';
    end if;

    if (
        p_previous_process_id is null
        or btrim(p_previous_process_id) = ''
    ) then
        raise exception 'previous_process_id is required';
    end if;

    if (
        p_new_process_id is null
        or btrim(p_new_process_id) = ''
    ) then
        raise exception 'new_process_id is required';
    end if;

    if (
        p_previous_heartbeat_at is null
        or p_resumed_at is null
        or p_resumed_at < p_previous_heartbeat_at
    ) then
        raise exception 'invalid reconciliation timestamps';
    end if;

    update public.member_activity_tracker_state
    set
        process_id = p_new_process_id,
        last_heartbeat_at = p_resumed_at,
        last_error_at = null,
        last_error = null,
        event_writes_failed = 0,
        updated_at = p_resumed_at
    where guild_id = p_guild_id
      and process_id = p_previous_process_id
      and last_heartbeat_at = p_previous_heartbeat_at
      and continuous_since is not null
      and coalesce(event_writes_failed, 0) = 0
      and coalesce(last_error, '') = '';

    get diagnostics changed_rows = row_count;

    return changed_rows = 1;
end;
$function$;
alter function public.resume_member_activity_tracker(text,text,text,timestamp with time zone,timestamp with time zone) set search_path=pg_catalog,public,pg_temp;
revoke all on function public.resume_member_activity_tracker(text,text,text,timestamp with time zone,timestamp with time zone) from public,anon,authenticated;
grant execute on function public.resume_member_activity_tracker(text,text,text,timestamp with time zone,timestamp with time zone) to service_role;
CREATE OR REPLACE FUNCTION public.start_member_activity_tracker(p_guild_id text, p_process_id text, p_started_at timestamp with time zone)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
begin
    insert into public.member_activity_tracker_state (
        guild_id,
        process_id,
        continuous_since,
        last_heartbeat_at,
        last_error_at,
        last_error,
        event_writes_failed,
        updated_at
    )
    values (
        p_guild_id,
        p_process_id,
        p_started_at,
        p_started_at,
        null,
        null,
        0,
        p_started_at
    )
    on conflict (guild_id)
    do update set
        process_id = excluded.process_id,
        continuous_since = excluded.continuous_since,
        last_heartbeat_at = excluded.last_heartbeat_at,
        last_error_at = null,
        last_error = null,
        event_writes_failed = 0,
        updated_at = excluded.updated_at;
end;
$function$;
alter function public.start_member_activity_tracker(text,text,timestamp with time zone) set search_path=pg_catalog,public,pg_temp;
revoke all on function public.start_member_activity_tracker(text,text,timestamp with time zone) from public,anon,authenticated;
grant execute on function public.start_member_activity_tracker(text,text,timestamp with time zone) to service_role;
