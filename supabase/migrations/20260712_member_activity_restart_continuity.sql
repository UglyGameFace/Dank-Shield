begin;

create or replace function public.resume_member_activity_tracker(
    p_guild_id text,
    p_previous_process_id text,
    p_new_process_id text,
    p_previous_heartbeat_at timestamptz,
    p_resumed_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
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
$$;

revoke all on function public.resume_member_activity_tracker(
    text,
    text,
    text,
    timestamptz,
    timestamptz
) from public;

grant execute on function public.resume_member_activity_tracker(
    text,
    text,
    text,
    timestamptz,
    timestamptz
) to service_role;

comment on function public.resume_member_activity_tracker(
    text,
    text,
    text,
    timestamptz,
    timestamptz
) is
'Atomically transfers an activity tracker state to a new process after a complete fail-closed restart reconciliation while preserving continuous_since.';

commit;
