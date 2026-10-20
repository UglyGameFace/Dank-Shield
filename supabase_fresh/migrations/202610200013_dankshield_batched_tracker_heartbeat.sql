-- New dedicated DankShield Supabase candidate ONLY. Additive RPC, not invoked
-- by the live Discord runtime. Each call updates at most 250 guild tracker rows.
create or replace function public.heartbeat_member_activity_trackers(
    p_guild_ids text[],
    p_process_id text,
    p_heartbeat_at timestamptz,
    p_max_gap_seconds integer default 180
) returns integer
language plpgsql security invoker set search_path = pg_catalog, public, pg_temp
as $batch$
declare
    v_count integer;
    v_affected integer;
begin
    v_count := cardinality(p_guild_ids);
    if p_guild_ids is null or coalesce(array_ndims(p_guild_ids),0) <> 1
       or v_count not between 1 and 250 then
        raise exception 'heartbeat batch requires 1..250 guild IDs';
    end if;
    if exists (
        select 1 from unnest(p_guild_ids) as input(guild_id)
        where input.guild_id is null or input.guild_id !~ '^[0-9]{1,20}$'
    ) then
        raise exception 'heartbeat batch contains invalid guild ID';
    end if;
    if (select count(distinct input.guild_id)
        from unnest(p_guild_ids) as input(guild_id)) <> v_count then
        raise exception 'heartbeat batch contains duplicate guild IDs';
    end if;
    if p_process_id is null or btrim(p_process_id) = ''
       or length(p_process_id) > 128 then
        raise exception 'invalid heartbeat process ID';
    end if;
    if p_heartbeat_at is null then
        raise exception 'heartbeat timestamp is required';
    end if;
    if p_max_gap_seconds is null or p_max_gap_seconds not between 60 and 3600 then
        raise exception 'heartbeat gap must be 60..3600 seconds';
    end if;
    insert into public.member_activity_tracker_state (
        guild_id, process_id, continuous_since, last_heartbeat_at, updated_at
    )
    select input.guild_id, p_process_id, p_heartbeat_at, p_heartbeat_at, p_heartbeat_at
    from unnest(p_guild_ids) as input(guild_id)
    on conflict (guild_id) do update
    set continuous_since = case
        when public.member_activity_tracker_state.process_id <> excluded.process_id
            or public.member_activity_tracker_state.last_heartbeat_at
               < excluded.last_heartbeat_at - make_interval(secs => p_max_gap_seconds)
        then excluded.last_heartbeat_at
        else public.member_activity_tracker_state.continuous_since
    end,
        process_id = excluded.process_id,
        last_heartbeat_at = excluded.last_heartbeat_at,
        updated_at = excluded.updated_at
    where excluded.last_heartbeat_at >= public.member_activity_tracker_state.last_heartbeat_at;
    get diagnostics v_affected = row_count;
    return v_affected;
end $batch$;

revoke all on function public.heartbeat_member_activity_trackers(text[],text,timestamptz,integer)
    from public, anon, authenticated;
grant execute on function public.heartbeat_member_activity_trackers(text[],text,timestamptz,integer)
    to service_role;
