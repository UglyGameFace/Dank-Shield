-- Dank Shield Community Hub
-- Durable session lifecycle, managed-resource provenance, privacy-preserving
-- analytics, notifications, events, and opt-in partner federation.
--
-- Runtime access is service-role only. Discord users never access these tables
-- directly; every user action is re-authorized by the bot from the interaction.

begin;

create extension if not exists pgcrypto;

create table if not exists public.dank_community_hub_settings (
    guild_id text primary key,
    enabled boolean not null default true,
    mode text not null default 'minimal'
        check (mode in ('minimal','smart','managed')),
    maintenance_mode boolean not null default false,
    parent_category_id text,
    hub_channel_id text,
    staff_log_channel_id text,
    cleanup_grace_seconds integer not null default 300
        check (cleanup_grace_seconds between 60 and 86400),
    idle_timeout_seconds integer not null default 1800
        check (idle_timeout_seconds between 300 and 604800),
    max_session_lifetime_seconds integer not null default 43200
        check (max_session_lifetime_seconds between 1800 and 1209600),
    max_active_sessions integer not null default 20
        check (max_active_sessions between 1 and 100),
    max_active_sessions_per_member integer not null default 2
        check (max_active_sessions_per_member between 1 and 10),
    session_creation_cooldown_seconds integer not null default 30
        check (session_creation_cooldown_seconds between 5 and 3600),
    max_session_creations_per_hour_per_member integer not null default 10
        check (max_session_creations_per_hour_per_member between 1 and 100),
    max_reports_per_hour_per_member integer not null default 5
        check (max_reports_per_hour_per_member between 1 and 50),
    max_temporary_voice_rooms integer not null default 12
        check (max_temporary_voice_rooms between 0 and 50),
    max_session_capacity integer not null default 25
        check (max_session_capacity between 2 and 99),
    minimum_players_to_start integer not null default 1
        check (minimum_players_to_start between 1 and 99),
    require_ready_to_start boolean not null default false,
    ready_timeout_seconds integer not null default 300
        check (ready_timeout_seconds between 60 and 3600),
    notifications_enabled boolean not null default true,
    max_notifications_per_hour integer not null default 5
        check (max_notifications_per_hour between 1 and 50),
    notification_cooldown_seconds integer not null default 300
        check (notification_cooldown_seconds between 60 and 3600),
    max_notification_targets_per_dispatch integer not null default 50
        check (max_notification_targets_per_dispatch between 0 and 250),
    allow_member_creation boolean not null default true,
    auto_create_thread boolean not null default true,
    auto_create_voice boolean not null default true,
    archive_threads_on_end boolean not null default true,
    analytics_enabled boolean not null default true,
    presence_analytics_enabled boolean not null default false,
    partner_discovery_enabled boolean not null default false,
    detailed_retention_days integer not null default 30
        check (detailed_retention_days between 1 and 365),
    aggregate_retention_days integer not null default 365
        check (aggregate_retention_days between 30 and 1825),
    updated_by text,
    updated_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create table if not exists public.dank_community_sessions (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    parent_session_id uuid references public.dank_community_sessions(id) on delete set null,
    idempotency_key text not null,
    host_id text,
    game_name text not null,
    notes text not null default '',
    capacity integer not null default 6 check (capacity between 2 and 99),
    mic_preference text not null default 'optional'
        check (mic_preference in ('optional','preferred','required','no_mic')),
    play_style text not null default 'casual'
        check (play_style in ('casual','competitive','beginner','any')),
    privacy text not null default 'public'
        check (privacy in ('public','invite_only','locked')),
    state text not null default 'open'
        check (state in (
            'creating','open','forming','ready','active','paused','ending',
            'ended','archived','cleaned','abandoned','interrupted','recovering','failed'
        )),
    version integer not null default 1 check (version > 0),
    panel_channel_id text,
    panel_message_id text,
    thread_id text,
    voice_channel_id text,
    last_activity_at timestamptz not null default now(),
    started_at timestamptz,
    ended_at timestamptz,
    end_reason text,
    ended_by text,
    privacy_pruned_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, idempotency_key)
);

create index if not exists idx_dank_community_sessions_guild_state_activity
    on public.dank_community_sessions (guild_id, state, last_activity_at desc);
create index if not exists idx_dank_community_sessions_host_state
    on public.dank_community_sessions (guild_id, host_id, state);
create index if not exists idx_dank_community_sessions_panel
    on public.dank_community_sessions (guild_id, panel_message_id)
    where panel_message_id is not null;

create table if not exists public.dank_community_session_members (
    session_id uuid not null references public.dank_community_sessions(id) on delete cascade,
    guild_id text not null,
    user_id text not null,
    role text not null default 'member'
        check (role in ('host','cohost','member','waitlist')),
    ready boolean not null default false,
    ready_at timestamptz,
    joined_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    left_at timestamptz,
    primary key (session_id, user_id)
);

create index if not exists idx_dank_community_members_user_active
    on public.dank_community_session_members (guild_id, user_id, left_at, joined_at desc);
create index if not exists idx_dank_community_members_session_role
    on public.dank_community_session_members (session_id, role, joined_at);

create table if not exists public.dank_community_managed_resources (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    session_id uuid references public.dank_community_sessions(id) on delete set null,
    resource_type text not null
        check (resource_type in ('panel_message','thread','voice_channel','category')),
    ownership_token text not null unique,
    discord_id text,
    parent_discord_id text,
    state text not null default 'planned'
        check (state in ('planned','active','archived','deleted','unresolved','failed','converted')),
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);

create unique index if not exists uq_dank_community_resource_live_discord
    on public.dank_community_managed_resources (guild_id, resource_type, discord_id)
    where discord_id is not null and state not in ('deleted','converted');
create index if not exists idx_dank_community_resources_reconcile
    on public.dank_community_managed_resources (state, created_at)
    where state in ('planned','active','unresolved','failed');

create table if not exists public.dank_community_session_events (
    id bigserial primary key,
    guild_id text not null,
    session_id uuid references public.dank_community_sessions(id) on delete cascade,
    event_type text not null,
    actor_id text,
    subject_user_id text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_dank_community_events_guild_created
    on public.dank_community_session_events (guild_id, created_at desc);
create index if not exists idx_dank_community_events_session_created
    on public.dank_community_session_events (session_id, created_at);

create table if not exists public.dank_community_metrics_hourly (
    guild_id text not null,
    bucket_start timestamptz not null,
    sessions_created integer not null default 0,
    sessions_started integer not null default 0,
    sessions_ended integer not null default 0,
    joins integer not null default 0,
    leaves integer not null default 0,
    completed_sessions integer not null default 0,
    abandoned_sessions integer not null default 0,
    cleanup_failures integer not null default 0,
    voice_participants_peak integer not null default 0,
    gaming_presence_peak integer not null default 0,
    online_presence_peak integer not null default 0,
    primary key (guild_id, bucket_start)
);

create table if not exists public.dank_community_game_metrics_hourly (
    guild_id text not null,
    bucket_start timestamptz not null,
    game_key text not null,
    game_name text not null,
    active_players_peak integer not null default 0,
    sessions_created integer not null default 0,
    joins integer not null default 0,
    primary key (guild_id, bucket_start, game_key)
);

create table if not exists public.dank_community_notification_prefs (
    guild_id text not null,
    user_id text not null,
    game_key text not null,
    game_name text not null,
    enabled boolean not null default true,
    notify_open_groups boolean not null default true,
    notify_events boolean not null default true,
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id, game_key)
);

create table if not exists public.dank_community_notification_state (
    guild_id text not null,
    user_id text not null,
    window_started_at timestamptz not null default now(),
    sent_in_window integer not null default 0 check (sent_in_window >= 0),
    last_sent_at timestamptz,
    blocked_until timestamptz,
    failure_count integer not null default 0 check (failure_count >= 0),
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id)
);

create table if not exists public.dank_community_availability (
    guild_id text not null,
    user_id text not null,
    game_key text not null,
    game_name text not null,
    play_style text not null default 'any'
        check (play_style in ('casual','competitive','beginner','any')),
    mic_preference text not null default 'optional'
        check (mic_preference in ('optional','preferred','required','no_mic')),
    note text not null default '',
    expires_at timestamptz not null,
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id, game_key)
);

create index if not exists idx_dank_community_availability_active
    on public.dank_community_availability (guild_id, expires_at, game_key);

create table if not exists public.dank_community_user_blocks (
    guild_id text not null,
    blocker_user_id text not null,
    blocked_user_id text not null,
    created_at timestamptz not null default now(),
    primary key (guild_id, blocker_user_id, blocked_user_id),
    check (blocker_user_id <> blocked_user_id)
);

create index if not exists idx_dank_community_user_blocks_reverse
    on public.dank_community_user_blocks (guild_id, blocked_user_id, blocker_user_id);

create table if not exists public.dank_community_reports (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    session_id uuid references public.dank_community_sessions(id) on delete set null,
    reporter_user_id text not null,
    reason text not null,
    details text not null default '',
    state text not null default 'open'
        check (state in ('open','reviewing','resolved','dismissed')),
    resolved_by text,
    resolved_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_dank_community_reports_staff_queue
    on public.dank_community_reports (guild_id, state, created_at desc);

create table if not exists public.dank_community_events (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    title text not null,
    description text not null default '',
    game_name text not null default '',
    starts_at timestamptz not null,
    ends_at timestamptz,
    capacity integer check (capacity is null or capacity between 2 and 10000),
    state text not null default 'scheduled'
        check (state in ('scheduled','open','active','ended','cancelled')),
    created_by text not null,
    discord_scheduled_event_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.dank_community_event_attendees (
    event_id uuid not null references public.dank_community_events(id) on delete cascade,
    guild_id text not null,
    user_id text not null,
    response text not null default 'interested'
        check (response in ('interested','going','waitlist','declined','attended','no_show')),
    updated_at timestamptz not null default now(),
    primary key (event_id, user_id)
);

create table if not exists public.dank_community_partner_links (
    id uuid primary key default gen_random_uuid(),
    guild_a_id text not null,
    guild_b_id text not null,
    state text not null default 'pending'
        check (state in ('pending','active','revoked','rejected')),
    requested_by_guild_id text not null,
    requested_by_user_id text not null,
    aggregate_activity_shared boolean not null default false,
    session_discovery_shared boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (guild_a_id <> guild_b_id),
    check (guild_a_id < guild_b_id)
);
create unique index if not exists uq_dank_community_partner_pair
    on public.dank_community_partner_links (guild_a_id, guild_b_id);

-- Service-only data boundary.
alter table public.dank_community_hub_settings enable row level security;
alter table public.dank_community_sessions enable row level security;
alter table public.dank_community_session_members enable row level security;
alter table public.dank_community_managed_resources enable row level security;
alter table public.dank_community_session_events enable row level security;
alter table public.dank_community_metrics_hourly enable row level security;
alter table public.dank_community_game_metrics_hourly enable row level security;
alter table public.dank_community_notification_prefs enable row level security;
alter table public.dank_community_notification_state enable row level security;
alter table public.dank_community_availability enable row level security;
alter table public.dank_community_user_blocks enable row level security;
alter table public.dank_community_reports enable row level security;
alter table public.dank_community_events enable row level security;
alter table public.dank_community_event_attendees enable row level security;
alter table public.dank_community_partner_links enable row level security;

revoke all on table public.dank_community_hub_settings from public, anon, authenticated;
revoke all on table public.dank_community_sessions from public, anon, authenticated;
revoke all on table public.dank_community_session_members from public, anon, authenticated;
revoke all on table public.dank_community_managed_resources from public, anon, authenticated;
revoke all on table public.dank_community_session_events from public, anon, authenticated;
revoke all on table public.dank_community_metrics_hourly from public, anon, authenticated;
revoke all on table public.dank_community_game_metrics_hourly from public, anon, authenticated;
revoke all on table public.dank_community_notification_prefs from public, anon, authenticated;
revoke all on table public.dank_community_notification_state from public, anon, authenticated;
revoke all on table public.dank_community_availability from public, anon, authenticated;
revoke all on table public.dank_community_user_blocks from public, anon, authenticated;
revoke all on table public.dank_community_reports from public, anon, authenticated;
revoke all on table public.dank_community_events from public, anon, authenticated;
revoke all on table public.dank_community_event_attendees from public, anon, authenticated;
revoke all on table public.dank_community_partner_links from public, anon, authenticated;

grant select, insert, update, delete on table public.dank_community_hub_settings to service_role;
grant select, insert, update, delete on table public.dank_community_sessions to service_role;
grant select, insert, update, delete on table public.dank_community_session_members to service_role;
grant select, insert, update, delete on table public.dank_community_managed_resources to service_role;
grant select, insert, update, delete on table public.dank_community_session_events to service_role;
grant select, insert, update, delete on table public.dank_community_metrics_hourly to service_role;
grant select, insert, update, delete on table public.dank_community_game_metrics_hourly to service_role;
grant select, insert, update, delete on table public.dank_community_notification_prefs to service_role;
grant select, insert, update, delete on table public.dank_community_notification_state to service_role;
grant select, insert, update, delete on table public.dank_community_availability to service_role;
grant select, insert, update, delete on table public.dank_community_user_blocks to service_role;
grant select, insert, update, delete on table public.dank_community_reports to service_role;
grant select, insert, update, delete on table public.dank_community_events to service_role;
grant select, insert, update, delete on table public.dank_community_event_attendees to service_role;
grant select, insert, update, delete on table public.dank_community_partner_links to service_role;
grant usage, select on sequence public.dank_community_session_events_id_seq to service_role;

create or replace function public.community_hub_create_session(
    p_guild_id text,
    p_host_id text,
    p_idempotency_key text,
    p_game_name text,
    p_notes text,
    p_capacity integer,
    p_mic_preference text,
    p_play_style text,
    p_privacy text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_settings public.dank_community_hub_settings%rowtype;
    v_session public.dank_community_sessions%rowtype;
    v_host_active integer;
    v_guild_active integer;
    v_recent_creations integer;
    v_last_creation timestamptz;
begin
    if btrim(coalesce(p_guild_id,'')) = '' or btrim(coalesce(p_host_id,'')) = '' then
        raise exception 'guild and host are required';
    end if;
    if char_length(btrim(coalesce(p_game_name,''))) < 1 or char_length(btrim(p_game_name)) > 80 then
        raise exception 'game name must be between 1 and 80 characters';
    end if;
    if char_length(coalesce(p_notes,'')) > 500 then
        raise exception 'notes cannot exceed 500 characters';
    end if;

    perform pg_advisory_xact_lock(hashtext(p_guild_id));

    select * into v_session
    from public.dank_community_sessions
    where guild_id = p_guild_id and idempotency_key = p_idempotency_key;

    if found then
        if v_session.host_id <> p_host_id then
            raise exception 'idempotency key belongs to another session owner';
        end if;
        return to_jsonb(v_session);
    end if;

    insert into public.dank_community_hub_settings(guild_id)
    values (p_guild_id)
    on conflict (guild_id) do nothing;

    select * into v_settings
    from public.dank_community_hub_settings
    where guild_id = p_guild_id
    for update;

    if not v_settings.enabled or v_settings.maintenance_mode then
        raise exception 'community hub is not accepting new sessions';
    end if;
    if not v_settings.allow_member_creation then
        raise exception 'member session creation is disabled';
    end if;
    if p_capacity < 2 or p_capacity > v_settings.max_session_capacity then
        raise exception 'session capacity is outside this server''s allowed range';
    end if;

    select count(*) into v_guild_active
    from public.dank_community_sessions
    where guild_id = p_guild_id
      and state in ('creating','open','forming','ready','active','paused','ending','interrupted','recovering');

    if v_guild_active >= v_settings.max_active_sessions then
        raise exception 'server active-session limit reached';
    end if;

    select count(*) into v_host_active
    from public.dank_community_session_members m
    join public.dank_community_sessions s on s.id = m.session_id
    where m.guild_id = p_guild_id
      and m.user_id = p_host_id
      and m.left_at is null
      and m.role <> 'waitlist'
      and s.state in ('creating','open','forming','ready','active','paused','ending','interrupted','recovering');

    if v_host_active >= v_settings.max_active_sessions_per_member then
        raise exception 'member active-session limit reached';
    end if;

    select count(*), max(created_at)
      into v_recent_creations, v_last_creation
    from public.dank_community_sessions
    where guild_id = p_guild_id
      and host_id = p_host_id
      and created_at >= now() - interval '1 hour';

    if v_recent_creations >= v_settings.max_session_creations_per_hour_per_member then
        raise exception 'member hourly session creation limit reached';
    end if;
    if v_last_creation is not null
       and v_last_creation > now() - make_interval(secs => v_settings.session_creation_cooldown_seconds) then
        raise exception 'member session creation cooldown is active';
    end if;

    insert into public.dank_community_sessions(
        guild_id,idempotency_key,host_id,game_name,notes,capacity,
        mic_preference,play_style,privacy,state
    ) values (
        p_guild_id,p_idempotency_key,p_host_id,btrim(p_game_name),coalesce(p_notes,''),
        p_capacity,p_mic_preference,p_play_style,p_privacy,'creating'
    )
    returning * into v_session;

    insert into public.dank_community_session_members(
        session_id,guild_id,user_id,role,ready,ready_at
    )
    values (v_session.id,p_guild_id,p_host_id,'host',true,now());

    insert into public.dank_community_session_events(guild_id,session_id,event_type,actor_id,subject_user_id)
    values (p_guild_id,v_session.id,'session.created',p_host_id,p_host_id);

    return to_jsonb(v_session);
end;
$$;

create or replace function public.community_hub_join_session(
    p_session_id uuid,
    p_guild_id text,
    p_user_id text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session public.dank_community_sessions%rowtype;
    v_settings public.dank_community_hub_settings%rowtype;
    v_count integer;
    v_member_active integer;
    v_existing_role text;
    v_role text;
begin
    select * into v_session
    from public.dank_community_sessions
    where id = p_session_id and guild_id = p_guild_id
    for update;

    if not found then raise exception 'session not found'; end if;
    if v_session.state not in ('open','forming','ready','active') then
        raise exception 'session is not joinable';
    end if;
    if v_session.privacy = 'locked' then raise exception 'session is locked'; end if;

    select role into v_existing_role
    from public.dank_community_session_members
    where session_id = p_session_id and user_id = p_user_id and left_at is null;

    if v_existing_role is not null then
        return jsonb_build_object('session',to_jsonb(v_session),'role',v_existing_role,'already_joined',true);
    end if;

    if exists (
        select 1
        from public.dank_community_session_members m
        join public.dank_community_user_blocks b
          on b.guild_id = p_guild_id
         and (
              (b.blocker_user_id = p_user_id and b.blocked_user_id = m.user_id)
              or
              (b.blocked_user_id = p_user_id and b.blocker_user_id = m.user_id)
         )
        where m.session_id = p_session_id
          and m.left_at is null
          and m.role <> 'waitlist'
    ) then
        raise exception 'match safety exclusion prevents this join';
    end if;

    select * into v_settings
    from public.dank_community_hub_settings
    where guild_id = p_guild_id
    for update;
    if not found or not v_settings.enabled or v_settings.maintenance_mode then
        raise exception 'community hub is not accepting joins';
    end if;

    select count(*) into v_member_active
    from public.dank_community_session_members m
    join public.dank_community_sessions s on s.id = m.session_id
    where m.guild_id = p_guild_id
      and m.user_id = p_user_id
      and m.left_at is null
      and m.role <> 'waitlist'
      and s.state in ('creating','open','forming','ready','active','paused','ending','interrupted','recovering');

    if v_member_active >= v_settings.max_active_sessions_per_member then
        raise exception 'member active-session limit reached';
    end if;

    select count(*) into v_count
    from public.dank_community_session_members
    where session_id = p_session_id and left_at is null and role <> 'waitlist';

    v_role := case
        when v_count = 0 then 'host'
        when v_count >= v_session.capacity then 'waitlist'
        else 'member'
    end;

    insert into public.dank_community_session_members(
        session_id,guild_id,user_id,role,ready,ready_at,left_at,last_seen_at
    )
    values (
        p_session_id,p_guild_id,p_user_id,v_role,
        (v_role = 'host'),
        case when v_role = 'host' then now() else null end,
        null,now()
    )
    on conflict (session_id,user_id) do update
       set role = excluded.role,
           ready = excluded.ready,
           ready_at = excluded.ready_at,
           left_at = null,
           last_seen_at = now();

    if v_role = 'host' then
        update public.dank_community_sessions
           set host_id = p_user_id
         where id = p_session_id;
    end if;

    update public.dank_community_sessions
       set state = case
             when state = 'open' and v_count + 1 >= 2 then 'forming'
             else state
           end,
           last_activity_at = now(),
           updated_at = now(),
           version = version + 1
     where id = p_session_id
     returning * into v_session;

    insert into public.dank_community_session_events(guild_id,session_id,event_type,actor_id,subject_user_id,metadata)
    values (p_guild_id,p_session_id,'session.joined',p_user_id,p_user_id,jsonb_build_object('role',v_role));

    return jsonb_build_object('session',to_jsonb(v_session),'role',v_role);
end;
$$;

create or replace function public.community_hub_quick_match(
    p_guild_id text,
    p_user_id text,
    p_game_name text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session_id uuid;
    v_result jsonb;
begin
    if char_length(btrim(coalesce(p_game_name,''))) < 1 or char_length(btrim(p_game_name)) > 80 then
        raise exception 'game name must be between 1 and 80 characters';
    end if;

    select s.id
      into v_session_id
    from public.dank_community_sessions s
    where s.guild_id=p_guild_id
      and lower(btrim(s.game_name))=lower(btrim(p_game_name))
      and s.privacy='public'
      and s.state in ('open','forming','ready','active')
      and not exists (
          select 1
          from public.dank_community_session_members own
          where own.session_id=s.id
            and own.user_id=p_user_id
            and own.left_at is null
      )
      and not exists (
          select 1
          from public.dank_community_session_members m
          join public.dank_community_user_blocks b
            on b.guild_id=p_guild_id
           and (
                (b.blocker_user_id=p_user_id and b.blocked_user_id=m.user_id)
                or
                (b.blocked_user_id=p_user_id and b.blocker_user_id=m.user_id)
           )
          where m.session_id=s.id
            and m.left_at is null
            and m.role <> 'waitlist'
      )
      and (
          select count(*)
          from public.dank_community_session_members active_member
          where active_member.session_id=s.id
            and active_member.left_at is null
            and active_member.role <> 'waitlist'
      ) < s.capacity
    order by (
        select count(*)
        from public.dank_community_session_members active_member
        where active_member.session_id=s.id
          and active_member.left_at is null
          and active_member.role <> 'waitlist'
    ) desc,
    s.last_activity_at desc
    limit 1
    for update of s skip locked;

    if v_session_id is null then
        return null;
    end if;

    v_result := public.community_hub_join_session(v_session_id,p_guild_id,p_user_id);
    return v_result;
end;
$$;

create or replace function public.community_hub_leave_session(
    p_session_id uuid,
    p_guild_id text,
    p_user_id text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session public.dank_community_sessions%rowtype;
    v_member public.dank_community_session_members%rowtype;
    v_new_host text;
    v_active_count integer;
begin
    select * into v_session
    from public.dank_community_sessions
    where id = p_session_id and guild_id = p_guild_id
    for update;
    if not found then raise exception 'session not found'; end if;

    select * into v_member
    from public.dank_community_session_members
    where session_id = p_session_id and user_id = p_user_id and left_at is null
    for update;
    if not found then
        return jsonb_build_object('session',to_jsonb(v_session),'already_left',true,'needs_cleanup',false);
    end if;

    update public.dank_community_session_members
       set left_at = now(), ready = false, ready_at = null, last_seen_at = now()
     where session_id = p_session_id and user_id = p_user_id;

    if v_member.role = 'host' then
        select user_id into v_new_host
        from public.dank_community_session_members
        where session_id = p_session_id
          and left_at is null
          and role in ('cohost','member')
        order by case role when 'cohost' then 0 else 1 end, joined_at
        limit 1
        for update;

        if v_new_host is not null then
            update public.dank_community_session_members
               set role = 'host', ready = true, ready_at = now()
             where session_id = p_session_id and user_id = v_new_host;
            update public.dank_community_sessions set host_id = v_new_host where id = p_session_id;
            insert into public.dank_community_session_events(
                guild_id,session_id,event_type,actor_id,subject_user_id,metadata
            ) values (
                p_guild_id,p_session_id,'session.host_transferred',p_user_id,v_new_host,
                jsonb_build_object('automatic',true)
            );
        end if;
    end if;

    -- Promote one waitlisted member if a capacity slot opened.
    with candidate as (
        select user_id
        from public.dank_community_session_members
        where session_id = p_session_id and left_at is null and role = 'waitlist'
        order by joined_at
        limit 1
        for update
    )
    update public.dank_community_session_members m
       set role = 'member'
      from candidate c
     where m.session_id = p_session_id and m.user_id = c.user_id;

    select count(*) into v_active_count
    from public.dank_community_session_members
    where session_id = p_session_id and left_at is null and role <> 'waitlist';

    update public.dank_community_sessions
       set state = case
             when v_active_count = 0 and state in ('creating','forming','ready','active','paused','interrupted','recovering') then 'open'
             else state
           end,
           last_activity_at = now(),
           updated_at = now(),
           version = version + 1
     where id = p_session_id
     returning * into v_session;

    insert into public.dank_community_session_events(guild_id,session_id,event_type,actor_id,subject_user_id)
    values (p_guild_id,p_session_id,'session.left',p_user_id,p_user_id);

    return jsonb_build_object(
        'session',to_jsonb(v_session),
        'new_host_id',v_new_host,
        'needs_cleanup',(v_active_count = 0)
    );
end;
$$;

create or replace function public.community_hub_set_ready(
    p_session_id uuid,
    p_guild_id text,
    p_user_id text,
    p_ready boolean
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session public.dank_community_sessions%rowtype;
    v_member_count integer;
    v_ready_count integer;
begin
    select * into v_session
    from public.dank_community_sessions
    where id = p_session_id and guild_id = p_guild_id
    for update;
    if not found then raise exception 'session not found'; end if;
    if v_session.state not in ('open','forming','ready') then
        raise exception 'ready check is not available in this session state';
    end if;

    update public.dank_community_session_members
       set ready = p_ready,
           ready_at = case when p_ready then now() else null end,
           last_seen_at = now()
     where session_id = p_session_id and user_id = p_user_id
       and left_at is null and role <> 'waitlist';
    if not found then raise exception 'member is not in this session'; end if;

    select count(*), count(*) filter (where ready)
      into v_member_count, v_ready_count
    from public.dank_community_session_members
    where session_id = p_session_id and left_at is null and role <> 'waitlist';

    update public.dank_community_sessions
       set state = case
            when v_member_count >= 2 and v_ready_count = v_member_count then 'ready'
            when v_member_count >= 2 then 'forming'
            else 'open'
           end,
           last_activity_at = now(),
           updated_at = now(),
           version = version + 1
     where id = p_session_id
     returning * into v_session;

    insert into public.dank_community_session_events(guild_id,session_id,event_type,actor_id,subject_user_id,metadata)
    values (
        p_guild_id,p_session_id,'session.ready_changed',p_user_id,p_user_id,
        jsonb_build_object('ready',p_ready)
    );

    return to_jsonb(v_session);
end;
$$;

create or replace function public.community_hub_transition_session(
    p_session_id uuid,
    p_guild_id text,
    p_actor_id text,
    p_action text,
    p_staff_override boolean default false,
    p_reason text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session public.dank_community_sessions%rowtype;
    v_settings public.dank_community_hub_settings%rowtype;
    v_actor_role text;
    v_next_state text;
    v_member_count integer;
    v_ready_count integer;
begin
    select * into v_session
    from public.dank_community_sessions
    where id = p_session_id and guild_id = p_guild_id
    for update;
    if not found then raise exception 'session not found'; end if;

    select role into v_actor_role
    from public.dank_community_session_members
    where session_id = p_session_id and user_id = p_actor_id and left_at is null;

    if not p_staff_override and coalesce(v_actor_role,'') not in ('host','cohost') then
        raise exception 'host, co-host, or staff authority required';
    end if;

    if p_action = 'begin_end' and v_session.state = 'ending' then
        return to_jsonb(v_session);
    end if;

    if p_action = 'start' then
        select * into v_settings
        from public.dank_community_hub_settings
        where guild_id = p_guild_id;
        if not found or not v_settings.enabled or v_settings.maintenance_mode then
            raise exception 'community hub is not accepting session starts';
        end if;

        select count(*), count(*) filter (where ready)
          into v_member_count, v_ready_count
        from public.dank_community_session_members
        where session_id = p_session_id and left_at is null and role <> 'waitlist';

        if v_member_count < v_settings.minimum_players_to_start then
            raise exception 'minimum players to start has not been reached';
        end if;
        if v_settings.require_ready_to_start and v_ready_count <> v_member_count then
            raise exception 'all active players must be ready before start';
        end if;
    end if;

    v_next_state := case p_action
        when 'publish' then case when v_session.state = 'creating' then 'open' else null end
        when 'start' then case when v_session.state in ('open','forming','ready') then 'active' else null end
        when 'pause' then case when v_session.state = 'active' then 'paused' else null end
        when 'resume' then case when v_session.state in ('paused','interrupted','recovering') then 'active' else null end
        when 'lock' then case when v_session.state in ('open','forming','ready','active','paused') then v_session.state else null end
        when 'unlock' then case when v_session.state in ('open','forming','ready','active','paused') then v_session.state else null end
        when 'extend' then case when v_session.state in ('open','forming','ready','active','paused') then v_session.state else null end
        when 'begin_end' then case
             when v_session.state in ('creating','open','forming','ready','active','paused','interrupted','recovering','failed') then 'ending'
             else null
        end
        else null
    end;

    if v_next_state is null then raise exception 'invalid session transition'; end if;

    update public.dank_community_sessions
       set state = v_next_state,
           privacy = case
               when p_action = 'lock' then 'locked'
               when p_action = 'unlock' and privacy = 'locked' then 'public'
               else privacy
           end,
           started_at = case when p_action = 'start' and started_at is null then now() else started_at end,
           end_reason = case when p_action = 'begin_end' then left(coalesce(p_reason,'ended'),500) else end_reason end,
           ended_by = case when p_action = 'begin_end' then p_actor_id else ended_by end,
           last_activity_at = now(),
           updated_at = now(),
           version = version + 1
     where id = p_session_id
     returning * into v_session;

    insert into public.dank_community_session_events(guild_id,session_id,event_type,actor_id,metadata)
    values (
        p_guild_id,p_session_id,'session.' || p_action,p_actor_id,
        jsonb_build_object('staff_override',p_staff_override,'reason',p_reason)
    );

    return to_jsonb(v_session);
end;
$$;

create or replace function public.community_hub_assign_member_role(
    p_session_id uuid,
    p_guild_id text,
    p_actor_id text,
    p_target_user_id text,
    p_role text,
    p_staff_override boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session public.dank_community_sessions%rowtype;
    v_actor_role text;
    v_target public.dank_community_session_members%rowtype;
begin
    if p_role not in ('host','cohost','member') then
        raise exception 'invalid assignable session role';
    end if;

    select * into v_session
    from public.dank_community_sessions
    where id = p_session_id and guild_id = p_guild_id
    for update;
    if not found then raise exception 'session not found'; end if;
    if v_session.state in ('ending','ended','archived','cleaned','abandoned','failed') then
        raise exception 'session roles cannot be changed after ending';
    end if;

    select role into v_actor_role
    from public.dank_community_session_members
    where session_id = p_session_id and user_id = p_actor_id and left_at is null;

    if not p_staff_override and coalesce(v_actor_role,'') <> 'host' then
        raise exception 'host or staff authority required';
    end if;

    select * into v_target
    from public.dank_community_session_members
    where session_id = p_session_id
      and user_id = p_target_user_id
      and left_at is null
      and role <> 'waitlist'
    for update;
    if not found then raise exception 'target member is not an active participant'; end if;

    if p_role = 'host' then
        update public.dank_community_session_members
           set role = case when user_id = p_target_user_id then 'host'
                           when role = 'host' then 'member'
                           else role end,
               ready = case when user_id = p_target_user_id then true else ready end,
               ready_at = case when user_id = p_target_user_id then now() else ready_at end
         where session_id = p_session_id and left_at is null;
        update public.dank_community_sessions
           set host_id = p_target_user_id,
               updated_at = now(),
               last_activity_at = now(),
               version = version + 1
         where id = p_session_id
         returning * into v_session;
        insert into public.dank_community_session_events(
            guild_id,session_id,event_type,actor_id,subject_user_id,metadata
        ) values (
            p_guild_id,p_session_id,'session.host_transferred',p_actor_id,p_target_user_id,
            jsonb_build_object('automatic',false,'staff_override',p_staff_override)
        );
    else
        if v_target.role = 'host' then
            raise exception 'transfer host ownership before changing the host role';
        end if;
        update public.dank_community_session_members
           set role = p_role
         where session_id = p_session_id and user_id = p_target_user_id;
        update public.dank_community_sessions
           set updated_at = now(), last_activity_at = now(), version = version + 1
         where id = p_session_id
         returning * into v_session;
        insert into public.dank_community_session_events(
            guild_id,session_id,event_type,actor_id,subject_user_id,metadata
        ) values (
            p_guild_id,p_session_id,'session.role_changed',p_actor_id,p_target_user_id,
            jsonb_build_object('role',p_role,'staff_override',p_staff_override)
        );
    end if;

    return to_jsonb(v_session);
end;
$$;

create or replace function public.community_hub_finalize_end_session(
    p_session_id uuid,
    p_guild_id text,
    p_actor_id text,
    p_cleanup_state text default 'ended'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session public.dank_community_sessions%rowtype;
begin
    if p_cleanup_state not in ('ended','archived','cleaned','failed') then
        raise exception 'invalid cleanup state';
    end if;

    update public.dank_community_sessions
       set state = p_cleanup_state,
           ended_at = coalesce(ended_at,now()),
           ended_by = coalesce(ended_by,p_actor_id),
           last_activity_at = now(),
           updated_at = now(),
           version = version + 1
     where id = p_session_id and guild_id = p_guild_id and state in ('ending','ended','archived','failed')
     returning * into v_session;
    if not found then raise exception 'session cannot be finalized from its current state'; end if;

    insert into public.dank_community_session_events(guild_id,session_id,event_type,actor_id,metadata)
    values (
        p_guild_id,p_session_id,'session.finalized',p_actor_id,
        jsonb_build_object('state',p_cleanup_state)
    );
    return to_jsonb(v_session);
end;
$$;

create or replace function public.community_hub_restart_session(
    p_session_id uuid,
    p_guild_id text,
    p_actor_id text,
    p_idempotency_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_old public.dank_community_sessions%rowtype;
    v_new public.dank_community_sessions%rowtype;
    v_settings public.dank_community_hub_settings%rowtype;
    v_guild_active integer;
    v_member_active integer;
    v_recent_creations integer;
begin
    perform pg_advisory_xact_lock(hashtext(p_guild_id));

    select * into v_new
    from public.dank_community_sessions
    where guild_id = p_guild_id and idempotency_key = p_idempotency_key;

    if found then
        if v_new.host_id <> p_actor_id or v_new.parent_session_id <> p_session_id then
            raise exception 'idempotency key belongs to another replay request';
        end if;
        return to_jsonb(v_new);
    end if;

    select * into v_old
    from public.dank_community_sessions
    where id = p_session_id and guild_id = p_guild_id
    for update;
    if not found then raise exception 'session not found'; end if;
    if v_old.state not in ('ended','archived','cleaned') then
        raise exception 'only an ended session can be played again';
    end if;
    if v_old.privacy_pruned_at is not null or v_old.host_id is null then
        raise exception 'session replay history has expired';
    end if;
    if v_old.host_id <> p_actor_id then
        raise exception 'only the previous host can play again';
    end if;

    select * into v_settings
    from public.dank_community_hub_settings
    where guild_id = p_guild_id
    for update;
    if not found or not v_settings.enabled or v_settings.maintenance_mode then
        raise exception 'community hub is not accepting new sessions';
    end if;
    if not v_settings.allow_member_creation then
        raise exception 'member session creation is disabled';
    end if;
    if v_old.capacity > v_settings.max_session_capacity then
        raise exception 'session capacity is outside this server''s allowed range';
    end if;

    select count(*) into v_guild_active
    from public.dank_community_sessions
    where guild_id = p_guild_id
      and state in ('creating','open','forming','ready','active','paused','ending','interrupted','recovering');
    if v_guild_active >= v_settings.max_active_sessions then
        raise exception 'server active-session limit reached';
    end if;

    select count(*) into v_member_active
    from public.dank_community_session_members m
    join public.dank_community_sessions s on s.id = m.session_id
    where m.guild_id = p_guild_id
      and m.user_id = p_actor_id
      and m.left_at is null
      and m.role <> 'waitlist'
      and s.state in ('creating','open','forming','ready','active','paused','ending','interrupted','recovering');
    if v_member_active >= v_settings.max_active_sessions_per_member then
        raise exception 'member active-session limit reached';
    end if;

    select count(*)
      into v_recent_creations
    from public.dank_community_sessions
    where guild_id = p_guild_id
      and host_id = p_actor_id
      and created_at >= now() - interval '1 hour';

    if v_recent_creations >= v_settings.max_session_creations_per_hour_per_member then
        raise exception 'member hourly session creation limit reached';
    end if;

    insert into public.dank_community_sessions(
        guild_id,parent_session_id,idempotency_key,host_id,game_name,notes,capacity,
        mic_preference,play_style,privacy,state
    ) values (
        p_guild_id,v_old.id,p_idempotency_key,p_actor_id,v_old.game_name,v_old.notes,
        v_old.capacity,v_old.mic_preference,v_old.play_style,
        case when v_old.privacy = 'locked' then 'public' else v_old.privacy end,
        'creating'
    ) returning * into v_new;

    insert into public.dank_community_session_members(
        session_id,guild_id,user_id,role,ready,ready_at
    )
    values (v_new.id,p_guild_id,p_actor_id,'host',true,now());

    insert into public.dank_community_session_events(guild_id,session_id,event_type,actor_id,metadata)
    values (
        p_guild_id,v_new.id,'session.restarted',p_actor_id,
        jsonb_build_object('parent_session_id',v_old.id)
    );
    return to_jsonb(v_new);
end;
$$;

create or replace function public.community_hub_rsvp_event(
    p_event_id uuid,
    p_guild_id text,
    p_user_id text,
    p_response text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_event public.dank_community_events%rowtype;
    v_attendee public.dank_community_event_attendees%rowtype;
    v_previous text;
    v_effective text;
    v_going integer;
    v_promote_user text;
begin
    if p_response not in ('interested','going','declined') then
        raise exception 'invalid member event response';
    end if;

    select * into v_event
    from public.dank_community_events
    where id=p_event_id and guild_id=p_guild_id
    for update;
    if not found then raise exception 'community event not found'; end if;
    if v_event.state in ('ended','cancelled') then
        raise exception 'community event is no longer accepting responses';
    end if;

    select response into v_previous
    from public.dank_community_event_attendees
    where event_id=p_event_id and user_id=p_user_id
    for update;

    v_effective := p_response;
    if p_response='going' and v_event.capacity is not null then
        select count(*) into v_going
        from public.dank_community_event_attendees
        where event_id=p_event_id
          and user_id<>p_user_id
          and response in ('going','attended');
        if v_going >= v_event.capacity then
            v_effective := 'waitlist';
        end if;
    end if;

    insert into public.dank_community_event_attendees(
        event_id,guild_id,user_id,response,updated_at
    ) values (
        p_event_id,p_guild_id,p_user_id,v_effective,now()
    )
    on conflict (event_id,user_id) do update
       set response=excluded.response,
           updated_at=now()
    returning * into v_attendee;

    if v_previous='going' and v_effective<>'going' and v_event.capacity is not null then
        select user_id into v_promote_user
        from public.dank_community_event_attendees
        where event_id=p_event_id
          and response='waitlist'
        order by updated_at
        limit 1
        for update skip locked;

        if v_promote_user is not null then
            update public.dank_community_event_attendees
               set response='going', updated_at=now()
             where event_id=p_event_id and user_id=v_promote_user;
        end if;
    end if;

    return to_jsonb(v_attendee);
end;
$$;

create or replace function public.community_hub_request_partner(
    p_source_guild_id text,
    p_target_guild_id text,
    p_actor_id text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_a text;
    v_b text;
    v_link public.dank_community_partner_links%rowtype;
begin
    if btrim(coalesce(p_source_guild_id,'')) = ''
       or btrim(coalesce(p_target_guild_id,'')) = ''
       or p_source_guild_id = p_target_guild_id then
        raise exception 'choose a different partner server';
    end if;

    v_a := least(p_source_guild_id,p_target_guild_id);
    v_b := greatest(p_source_guild_id,p_target_guild_id);
    perform pg_advisory_xact_lock(hashtext(v_a || ':partner:' || v_b));

    if not exists (
        select 1 from public.dank_community_hub_settings
        where guild_id=p_source_guild_id and partner_discovery_enabled=true
    ) then
        raise exception 'source server has not enabled partner discovery';
    end if;
    if not exists (
        select 1 from public.dank_community_hub_settings
        where guild_id=p_target_guild_id and partner_discovery_enabled=true
    ) then
        raise exception 'target server has not enabled partner discovery';
    end if;

    select * into v_link
    from public.dank_community_partner_links
    where guild_a_id=v_a and guild_b_id=v_b
    for update;

    if found and v_link.state in ('pending','active') then
        return to_jsonb(v_link);
    end if;

    if found then
        update public.dank_community_partner_links
           set state='pending',
               requested_by_guild_id=p_source_guild_id,
               requested_by_user_id=p_actor_id,
               aggregate_activity_shared=false,
               session_discovery_shared=false,
               updated_at=now()
         where id=v_link.id
         returning * into v_link;
    else
        insert into public.dank_community_partner_links(
            guild_a_id,guild_b_id,state,requested_by_guild_id,requested_by_user_id
        ) values (
            v_a,v_b,'pending',p_source_guild_id,p_actor_id
        )
        returning * into v_link;
    end if;

    return to_jsonb(v_link);
end;
$$;

create or replace function public.community_hub_submit_report(
    p_guild_id text,
    p_session_id uuid,
    p_reporter_user_id text,
    p_reason text,
    p_details text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_settings public.dank_community_hub_settings%rowtype;
    v_report public.dank_community_reports%rowtype;
    v_recent integer;
begin
    if char_length(btrim(coalesce(p_reason,''))) < 1 or char_length(p_reason) > 100 then
        raise exception 'report reason must be between 1 and 100 characters';
    end if;
    if char_length(coalesce(p_details,'')) > 1000 then
        raise exception 'report details cannot exceed 1000 characters';
    end if;

    perform pg_advisory_xact_lock(hashtext(p_guild_id || ':report:' || p_reporter_user_id));

    select * into v_settings
    from public.dank_community_hub_settings
    where guild_id=p_guild_id;
    if not found then
        insert into public.dank_community_hub_settings(guild_id)
        values (p_guild_id)
        on conflict (guild_id) do nothing;
        select * into v_settings
        from public.dank_community_hub_settings
        where guild_id=p_guild_id;
    end if;

    if p_session_id is not null and not exists (
        select 1
        from public.dank_community_session_members
        where session_id=p_session_id
          and guild_id=p_guild_id
          and user_id=p_reporter_user_id
    ) then
        raise exception 'only a participant can report a Community Hub session';
    end if;

    select count(*) into v_recent
    from public.dank_community_reports
    where guild_id=p_guild_id
      and reporter_user_id=p_reporter_user_id
      and created_at >= now() - interval '1 hour';

    if v_recent >= v_settings.max_reports_per_hour_per_member then
        raise exception 'member hourly Community Hub report limit reached';
    end if;

    insert into public.dank_community_reports(
        guild_id,session_id,reporter_user_id,reason,details,state
    ) values (
        p_guild_id,p_session_id,p_reporter_user_id,btrim(p_reason),coalesce(p_details,''),'open'
    )
    returning * into v_report;

    return to_jsonb(v_report);
end;
$$;

create or replace function public.community_hub_set_match_block(
    p_guild_id text,
    p_blocker_user_id text,
    p_blocked_user_id text,
    p_blocked boolean
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
    if btrim(coalesce(p_guild_id,'')) = ''
       or btrim(coalesce(p_blocker_user_id,'')) = ''
       or btrim(coalesce(p_blocked_user_id,'')) = '' then
        raise exception 'guild and users are required';
    end if;
    if p_blocker_user_id = p_blocked_user_id then
        raise exception 'a user cannot block themselves from matching';
    end if;

    if p_blocked then
        insert into public.dank_community_user_blocks(
            guild_id,blocker_user_id,blocked_user_id
        ) values (
            p_guild_id,p_blocker_user_id,p_blocked_user_id
        )
        on conflict (guild_id,blocker_user_id,blocked_user_id) do nothing;
        return true;
    end if;

    delete from public.dank_community_user_blocks
    where guild_id=p_guild_id
      and blocker_user_id=p_blocker_user_id
      and blocked_user_id=p_blocked_user_id;
    return false;
end;
$$;

create or replace function public.community_hub_reserve_notification(
    p_guild_id text,
    p_user_id text,
    p_now timestamptz,
    p_max_per_hour integer,
    p_cooldown_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    v_state public.dank_community_notification_state%rowtype;
    v_max integer := greatest(1,least(50,coalesce(p_max_per_hour,5)));
    v_cooldown integer := greatest(60,least(3600,coalesce(p_cooldown_seconds,300)));
begin
    insert into public.dank_community_notification_state(guild_id,user_id,window_started_at)
    values (p_guild_id,p_user_id,p_now)
    on conflict (guild_id,user_id) do nothing;

    select * into v_state
    from public.dank_community_notification_state
    where guild_id=p_guild_id and user_id=p_user_id
    for update;

    if v_state.blocked_until is not null and v_state.blocked_until > p_now then
        return false;
    end if;

    if v_state.window_started_at <= p_now - interval '1 hour' then
        update public.dank_community_notification_state
           set window_started_at=p_now,
               sent_in_window=0,
               failure_count=0,
               updated_at=p_now
         where guild_id=p_guild_id and user_id=p_user_id;
        v_state.sent_in_window := 0;
        v_state.window_started_at := p_now;
    end if;

    if v_state.sent_in_window >= v_max then
        return false;
    end if;
    if v_state.last_sent_at is not null
       and v_state.last_sent_at > p_now - make_interval(secs => v_cooldown) then
        return false;
    end if;

    update public.dank_community_notification_state
       set sent_in_window=sent_in_window+1,
           last_sent_at=p_now,
           updated_at=p_now
     where guild_id=p_guild_id and user_id=p_user_id;
    return true;
end;
$$;

create or replace function public.community_hub_mark_notification_failure(
    p_guild_id text,
    p_user_id text,
    p_blocked_until timestamptz
) returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.dank_community_notification_state(
        guild_id,user_id,window_started_at,blocked_until,failure_count,updated_at
    )
    values (p_guild_id,p_user_id,now(),p_blocked_until,1,now())
    on conflict (guild_id,user_id) do update
       set blocked_until=greatest(
               coalesce(public.dank_community_notification_state.blocked_until,'epoch'::timestamptz),
               excluded.blocked_until
           ),
           failure_count=public.dank_community_notification_state.failure_count+1,
           updated_at=now();
end;
$$;

create or replace function public.community_hub_clear_stale_ready(
    p_guild_id text,
    p_cutoff timestamptz
) returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_count integer := 0;
begin
    with stale as (
        update public.dank_community_session_members
           set ready = false,
               ready_at = null,
               last_seen_at = now()
         where guild_id = p_guild_id
           and ready = true
           and ready_at is not null
           and ready_at < p_cutoff
           and left_at is null
        returning session_id, user_id
    ),
    touched as (
        select distinct session_id from stale
    ),
    session_updates as (
        update public.dank_community_sessions s
           set state = case when s.state = 'ready' then 'forming' else s.state end,
               updated_at = now(),
               version = version + 1
          from touched t
         where s.id = t.session_id
           and s.state in ('open','forming','ready')
        returning s.id
    )
    select count(*) into v_count from stale;

    return v_count;
end;
$$;

create or replace function public.community_hub_prune_session_personal_data(
    p_guild_id text,
    p_cutoff timestamptz
) returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_count integer := 0;
begin
    with targets as materialized (
        select id
        from public.dank_community_sessions
        where guild_id=p_guild_id
          and ended_at is not null
          and ended_at < p_cutoff
          and privacy_pruned_at is null
          and state in ('ended','archived','cleaned','abandoned','failed')
        limit 500
        for update skip locked
    ),
    deleted_members as (
        delete from public.dank_community_session_members m
        using targets t
        where m.session_id=t.id
        returning m.session_id
    ),
    scrubbed as (
        update public.dank_community_sessions s
           set host_id=null,
               notes='',
               ended_by=null,
               idempotency_key='pruned:' || s.id::text,
               privacy_pruned_at=now(),
               updated_at=now()
          from targets t
         where s.id=t.id
        returning s.id
    )
    select count(*) into v_count from scrubbed;

    return v_count;
end;
$$;

create or replace function public.community_hub_bump_metric(
    p_guild_id text,
    p_bucket_start timestamptz,
    p_metric text,
    p_amount integer,
    p_peak boolean default false
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_amount integer := greatest(0, coalesce(p_amount,0));
begin
    if exists (
        select 1
        from public.dank_community_hub_settings
        where guild_id=p_guild_id and analytics_enabled=false
    ) then
        return;
    end if;

    insert into public.dank_community_metrics_hourly(guild_id,bucket_start)
    values (p_guild_id,date_trunc('hour',p_bucket_start))
    on conflict (guild_id,bucket_start) do nothing;

    if p_metric = 'sessions_created' then
        update public.dank_community_metrics_hourly set sessions_created = sessions_created + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'sessions_started' then
        update public.dank_community_metrics_hourly set sessions_started = sessions_started + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'sessions_ended' then
        update public.dank_community_metrics_hourly set sessions_ended = sessions_ended + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'joins' then
        update public.dank_community_metrics_hourly set joins = joins + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'leaves' then
        update public.dank_community_metrics_hourly set leaves = leaves + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'completed_sessions' then
        update public.dank_community_metrics_hourly set completed_sessions = completed_sessions + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'abandoned_sessions' then
        update public.dank_community_metrics_hourly set abandoned_sessions = abandoned_sessions + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'cleanup_failures' then
        update public.dank_community_metrics_hourly set cleanup_failures = cleanup_failures + v_amount
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'voice_participants_peak' then
        update public.dank_community_metrics_hourly set voice_participants_peak = greatest(voice_participants_peak,v_amount)
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'gaming_presence_peak' then
        update public.dank_community_metrics_hourly set gaming_presence_peak = greatest(gaming_presence_peak,v_amount)
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    elsif p_metric = 'online_presence_peak' then
        update public.dank_community_metrics_hourly set online_presence_peak = greatest(online_presence_peak,v_amount)
        where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start);
    else
        raise exception 'unsupported community metric';
    end if;
end;
$$;

create or replace function public.community_hub_bump_game_metric(
    p_guild_id text,
    p_bucket_start timestamptz,
    p_game_key text,
    p_game_name text,
    p_metric text,
    p_amount integer,
    p_peak boolean default false
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_amount integer := greatest(0, coalesce(p_amount,0));
begin
    if exists (
        select 1
        from public.dank_community_hub_settings
        where guild_id=p_guild_id and analytics_enabled=false
    ) then
        return;
    end if;

    insert into public.dank_community_game_metrics_hourly(
        guild_id,bucket_start,game_key,game_name
    )
    values (
        p_guild_id,date_trunc('hour',p_bucket_start),p_game_key,left(p_game_name,80)
    )
    on conflict (guild_id,bucket_start,game_key) do update
       set game_name = excluded.game_name;

    if p_metric = 'active_players_peak' then
        update public.dank_community_game_metrics_hourly
           set active_players_peak = greatest(active_players_peak,v_amount)
         where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start) and game_key=p_game_key;
    elsif p_metric = 'sessions_created' then
        update public.dank_community_game_metrics_hourly
           set sessions_created = sessions_created + v_amount
         where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start) and game_key=p_game_key;
    elsif p_metric = 'joins' then
        update public.dank_community_game_metrics_hourly
           set joins = joins + v_amount
         where guild_id=p_guild_id and bucket_start=date_trunc('hour',p_bucket_start) and game_key=p_game_key;
    else
        raise exception 'unsupported community game metric';
    end if;
end;
$$;

-- Default-deny function execution, then service-role only.
revoke all on function public.community_hub_create_session(text,text,text,text,text,integer,text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_join_session(uuid,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_quick_match(text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_leave_session(uuid,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_set_ready(uuid,text,text,boolean)
    from public, anon, authenticated;
revoke all on function public.community_hub_transition_session(uuid,text,text,text,boolean,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_assign_member_role(uuid,text,text,text,text,boolean)
    from public, anon, authenticated;
revoke all on function public.community_hub_finalize_end_session(uuid,text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_restart_session(uuid,text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_rsvp_event(uuid,text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_request_partner(text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_submit_report(text,uuid,text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_set_match_block(text,text,text,boolean)
    from public, anon, authenticated;
revoke all on function public.community_hub_reserve_notification(text,text,timestamptz,integer,integer)
    from public, anon, authenticated;
revoke all on function public.community_hub_mark_notification_failure(text,text,timestamptz)
    from public, anon, authenticated;
revoke all on function public.community_hub_clear_stale_ready(text,timestamptz)
    from public, anon, authenticated;
revoke all on function public.community_hub_prune_session_personal_data(text,timestamptz)
    from public, anon, authenticated;
revoke all on function public.community_hub_bump_metric(text,timestamptz,text,integer,boolean)
    from public, anon, authenticated;
revoke all on function public.community_hub_bump_game_metric(text,timestamptz,text,text,text,integer,boolean)
    from public, anon, authenticated;

grant execute on function public.community_hub_create_session(text,text,text,text,text,integer,text,text,text) to service_role;
grant execute on function public.community_hub_join_session(uuid,text,text) to service_role;
grant execute on function public.community_hub_quick_match(text,text,text) to service_role;
grant execute on function public.community_hub_leave_session(uuid,text,text) to service_role;
grant execute on function public.community_hub_set_ready(uuid,text,text,boolean) to service_role;
grant execute on function public.community_hub_transition_session(uuid,text,text,text,boolean,text) to service_role;
grant execute on function public.community_hub_assign_member_role(uuid,text,text,text,text,boolean) to service_role;
grant execute on function public.community_hub_finalize_end_session(uuid,text,text,text) to service_role;
grant execute on function public.community_hub_restart_session(uuid,text,text,text) to service_role;
grant execute on function public.community_hub_rsvp_event(uuid,text,text,text) to service_role;
grant execute on function public.community_hub_request_partner(text,text,text) to service_role;
grant execute on function public.community_hub_submit_report(text,uuid,text,text,text) to service_role;
grant execute on function public.community_hub_set_match_block(text,text,text,boolean) to service_role;
grant execute on function public.community_hub_reserve_notification(text,text,timestamptz,integer,integer) to service_role;
grant execute on function public.community_hub_mark_notification_failure(text,text,timestamptz) to service_role;
grant execute on function public.community_hub_clear_stale_ready(text,timestamptz) to service_role;
grant execute on function public.community_hub_prune_session_personal_data(text,timestamptz) to service_role;
grant execute on function public.community_hub_bump_metric(text,timestamptz,text,integer,boolean) to service_role;
grant execute on function public.community_hub_bump_game_metric(text,timestamptz,text,text,text,integer,boolean) to service_role;

comment on table public.dank_community_managed_resources is
    'Only Discord resources with durable Community Hub ownership provenance may be automatically cleaned up.';
comment on table public.dank_community_metrics_hourly is
    'Privacy-preserving aggregate Community Hub metrics. Raw member presence history is intentionally not stored.';

commit;
