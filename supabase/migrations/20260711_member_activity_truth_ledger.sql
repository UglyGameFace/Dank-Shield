-- Dank Shield authoritative member-activity truth ledger
--
-- Only direct actions performed by a Discord member are recorded:
-- message, reaction, interaction, and ticket_message.
--
-- The tracker state records continuous observation health. Any process restart,
-- stale heartbeat, or failed write restarts the trustworthy coverage window.

create table if not exists public.member_activity_ledger (
    guild_id text not null,
    user_id text not null,
    last_activity_at timestamptz not null,
    last_activity_type text not null,
    last_message_at timestamptz,
    last_reaction_at timestamptz,
    last_interaction_at timestamptz,
    last_ticket_message_at timestamptz,
    last_channel_id text,
    last_process_id text,
    event_count bigint not null default 0,
    first_observed_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id)
);

create table if not exists public.member_activity_tracker_state (
    guild_id text primary key,
    process_id text not null,
    continuous_since timestamptz not null,
    last_heartbeat_at timestamptz not null,
    last_error_at timestamptz,
    last_error text,
    event_writes_failed bigint not null default 0,
    updated_at timestamptz not null default now()
);

create index if not exists idx_member_activity_ledger_guild_last
    on public.member_activity_ledger (guild_id, last_activity_at desc);

create index if not exists idx_member_activity_tracker_heartbeat
    on public.member_activity_tracker_state (last_heartbeat_at);

create or replace function public.record_member_activity(
    p_guild_id text,
    p_user_id text,
    p_activity_type text,
    p_occurred_at timestamptz,
    p_channel_id text,
    p_process_id text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
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
$$;

create or replace function public.start_member_activity_tracker(
    p_guild_id text,
    p_process_id text,
    p_started_at timestamptz
)
returns void
language plpgsql
security definer
set search_path = public
as $$
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
$$;

create or replace function public.heartbeat_member_activity_tracker(
    p_guild_id text,
    p_process_id text,
    p_heartbeat_at timestamptz,
    p_max_gap_seconds integer
)
returns void
language plpgsql
security definer
set search_path = public
as $$
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
$$;

create or replace function public.fail_member_activity_tracker(
    p_guild_id text,
    p_process_id text,
    p_failed_at timestamptz,
    p_error text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
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
$$;

alter table public.member_activity_ledger
    enable row level security;

alter table public.member_activity_tracker_state
    enable row level security;

drop policy if exists member_activity_ledger_service_role_all
    on public.member_activity_ledger;

create policy member_activity_ledger_service_role_all
on public.member_activity_ledger
for all
to service_role
using (true)
with check (true);

drop policy if exists member_activity_tracker_service_role_all
    on public.member_activity_tracker_state;

create policy member_activity_tracker_service_role_all
on public.member_activity_tracker_state
for all
to service_role
using (true)
with check (true);

grant execute on function public.record_member_activity(
    text,
    text,
    text,
    timestamptz,
    text,
    text
) to service_role;

grant execute on function public.start_member_activity_tracker(
    text,
    text,
    timestamptz
) to service_role;

grant execute on function public.heartbeat_member_activity_tracker(
    text,
    text,
    timestamptz,
    integer
) to service_role;

grant execute on function public.fail_member_activity_tracker(
    text,
    text,
    timestamptz,
    text
) to service_role;
