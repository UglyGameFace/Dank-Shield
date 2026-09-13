-- Promote the legacy runtime/bootstrap-owned schema into the canonical Supabase
-- migration chain.
--
-- This migration is intentionally current-dated. Older deployments have already
-- applied the historical migration sequence, so inserting a new backdated base
-- migration would make remote/local migration history disagree. Everything here
-- is idempotent and safe to apply to both established databases and fresh ones.
--
-- After this migration, application startup must only inspect schema readiness.
-- It must never create/alter database objects or execute migration SQL.

begin;

-- ---------------------------------------------------------------------------
-- Tickets: the original base table predates the committed migration history.
-- Create the current repository contract, then enrich any legacy table in place.
-- ---------------------------------------------------------------------------
create table if not exists public.tickets (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text,
    username text,
    title text,
    category text not null default 'support',
    status text not null default 'open',
    priority text not null default 'medium',
    claimed_by text,
    closed_by text,
    closed_reason text,
    initial_message text,
    ai_category_confidence double precision not null default 0,
    mod_suggestion text,
    mod_suggestion_confidence double precision not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    closed_at timestamptz,
    discord_thread_id text,
    assigned_to text,
    reopened_at timestamptz,
    sla_deadline timestamptz,
    channel_id text,
    channel_name text,
    ticket_number integer,
    is_ghost boolean not null default false,
    deleted_at timestamptz,
    deleted_by text,
    transcript_url text,
    transcript_message_id text,
    transcript_channel_id text,
    source text,
    category_id text,
    category_override boolean not null default false,
    category_set_by text,
    category_set_at timestamptz,
    matched_category_id text,
    matched_category_name text,
    matched_category_slug text,
    matched_intake_type text,
    matched_category_reason text,
    matched_category_score integer,
    decision text,
    last_activity_at timestamptz,
    last_message_id text,
    panel_message_id text,
    webhook_url text,
    webhook_id text,
    reopened_by text,
    reopened_by_name text,
    reopen_reason text,
    close_reason text,
    delete_reason text,
    owner_id text,
    owner_name text,
    requester_id text,
    requester_name text,
    claimed_by_name text,
    assigned_to_name text,
    closed_by_name text,
    deleted_by_name text,
    metadata jsonb not null default '{}'::jsonb,
    meta jsonb not null default '{}'::jsonb
);

alter table public.tickets
    add column if not exists user_id text,
    add column if not exists username text,
    add column if not exists title text,
    add column if not exists category text not null default 'support',
    add column if not exists status text not null default 'open',
    add column if not exists priority text not null default 'medium',
    add column if not exists claimed_by text,
    add column if not exists closed_by text,
    add column if not exists closed_reason text,
    add column if not exists initial_message text,
    add column if not exists ai_category_confidence double precision not null default 0,
    add column if not exists mod_suggestion text,
    add column if not exists mod_suggestion_confidence double precision not null default 0,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now(),
    add column if not exists closed_at timestamptz,
    add column if not exists discord_thread_id text,
    add column if not exists assigned_to text,
    add column if not exists reopened_at timestamptz,
    add column if not exists sla_deadline timestamptz,
    add column if not exists channel_id text,
    add column if not exists channel_name text,
    add column if not exists ticket_number integer,
    add column if not exists is_ghost boolean not null default false,
    add column if not exists deleted_at timestamptz,
    add column if not exists deleted_by text,
    add column if not exists transcript_url text,
    add column if not exists transcript_message_id text,
    add column if not exists transcript_channel_id text,
    add column if not exists source text,
    add column if not exists category_id text,
    add column if not exists category_override boolean not null default false,
    add column if not exists category_set_by text,
    add column if not exists category_set_at timestamptz,
    add column if not exists matched_category_id text,
    add column if not exists matched_category_name text,
    add column if not exists matched_category_slug text,
    add column if not exists matched_intake_type text,
    add column if not exists matched_category_reason text,
    add column if not exists matched_category_score integer,
    add column if not exists decision text,
    add column if not exists last_activity_at timestamptz,
    add column if not exists last_message_id text,
    add column if not exists panel_message_id text,
    add column if not exists webhook_url text,
    add column if not exists webhook_id text,
    add column if not exists reopened_by text,
    add column if not exists reopened_by_name text,
    add column if not exists reopen_reason text,
    add column if not exists close_reason text,
    add column if not exists delete_reason text,
    add column if not exists owner_id text,
    add column if not exists owner_name text,
    add column if not exists requester_id text,
    add column if not exists requester_name text,
    add column if not exists claimed_by_name text,
    add column if not exists assigned_to_name text,
    add column if not exists closed_by_name text,
    add column if not exists deleted_by_name text,
    add column if not exists metadata jsonb not null default '{}'::jsonb,
    add column if not exists meta jsonb not null default '{}'::jsonb;

create index if not exists idx_tickets_guild_status on public.tickets (guild_id, status);
create index if not exists idx_tickets_channel_id on public.tickets (channel_id);
create index if not exists idx_tickets_discord_thread_id on public.tickets (discord_thread_id);
create index if not exists idx_tickets_owner on public.tickets (guild_id, owner_id);
create index if not exists idx_tickets_owner_id on public.tickets (owner_id);
create index if not exists idx_tickets_requester_id on public.tickets (requester_id);
create index if not exists idx_tickets_reopened_by on public.tickets (reopened_by);

-- Historical databases can contain duplicate ticket identifiers. Preserve those
-- rows and only enable atomic uniqueness when existing data allows it.
do $$
begin
    if not exists (
        select 1
        from public.tickets
        where ticket_number is not null
        group by guild_id, ticket_number
        having count(*) > 1
        limit 1
    ) then
        execute 'create unique index if not exists uq_tickets_guild_ticket_number on public.tickets (guild_id, ticket_number) where ticket_number is not null';
    else
        raise notice 'Skipping uq_tickets_guild_ticket_number because duplicate historical ticket numbers exist.';
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from public.tickets
        where nullif(channel_id, '') is not null
        group by channel_id
        having count(*) > 1
        limit 1
    ) then
        execute 'create unique index if not exists uq_tickets_channel_id on public.tickets (channel_id) where nullif(channel_id, '''') is not null';
    else
        raise notice 'Skipping uq_tickets_channel_id because duplicate historical channel IDs exist.';
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from public.tickets
        where nullif(discord_thread_id, '') is not null
        group by discord_thread_id
        having count(*) > 1
        limit 1
    ) then
        execute 'create unique index if not exists uq_tickets_discord_thread_id on public.tickets (discord_thread_id) where nullif(discord_thread_id, '''') is not null';
    else
        raise notice 'Skipping uq_tickets_discord_thread_id because duplicate historical thread IDs exist.';
    end if;
end $$;

-- ---------------------------------------------------------------------------
-- Ticket notes/messages: support the current repository contract while keeping
-- legacy columns readable during the compatibility window.
-- ---------------------------------------------------------------------------
create table if not exists public.ticket_notes (
    id uuid primary key default gen_random_uuid(),
    ticket_id text,
    staff_id text,
    staff_name text,
    content text,
    is_pinned boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    guild_id text,
    channel_id text,
    author_id text,
    author_name text,
    note text
);

alter table public.ticket_notes
    add column if not exists ticket_id text,
    add column if not exists staff_id text,
    add column if not exists staff_name text,
    add column if not exists content text,
    add column if not exists is_pinned boolean not null default false,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now(),
    add column if not exists guild_id text,
    add column if not exists channel_id text,
    add column if not exists author_id text,
    add column if not exists author_name text,
    add column if not exists note text;

create index if not exists idx_ticket_notes_ticket_created on public.ticket_notes (ticket_id, created_at desc);
create index if not exists idx_ticket_notes_ticket_pinned on public.ticket_notes (ticket_id, is_pinned, created_at desc);

create table if not exists public.ticket_messages (
    id uuid primary key default gen_random_uuid(),
    ticket_id text,
    author_id text,
    author_name text,
    content text,
    message_type text not null default 'staff',
    attachments jsonb not null default '[]'::jsonb,
    source text,
    created_at timestamptz not null default now(),
    guild_id text,
    channel_id text,
    message_id text,
    metadata jsonb not null default '{}'::jsonb
);

alter table public.ticket_messages
    add column if not exists ticket_id text,
    add column if not exists author_id text,
    add column if not exists author_name text,
    add column if not exists content text,
    add column if not exists message_type text not null default 'staff',
    add column if not exists attachments jsonb not null default '[]'::jsonb,
    add column if not exists source text,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists guild_id text,
    add column if not exists channel_id text,
    add column if not exists message_id text,
    add column if not exists metadata jsonb not null default '{}'::jsonb;

create index if not exists idx_ticket_messages_ticket_created on public.ticket_messages (ticket_id, created_at);

-- ---------------------------------------------------------------------------
-- Activity feed: current ticket/community services write the rich fields below.
-- Legacy actor_id/target_id/message remain for older callers.
-- ---------------------------------------------------------------------------
create table if not exists public.activity_feed_events (
    id uuid primary key default gen_random_uuid(),
    guild_id text,
    event_family text,
    event_type text,
    source text,
    actor_user_id text,
    actor_name text,
    target_user_id text,
    target_name text,
    channel_id text,
    channel_name text,
    ticket_id text,
    ticket_message_id text,
    related_table text,
    related_id text,
    title text,
    description text,
    reason text,
    search_text text,
    metadata jsonb not null default '{}'::jsonb,
    meta jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    actor_id text,
    target_id text,
    message text
);

alter table public.activity_feed_events
    add column if not exists guild_id text,
    add column if not exists event_family text,
    add column if not exists event_type text,
    add column if not exists source text,
    add column if not exists actor_user_id text,
    add column if not exists actor_name text,
    add column if not exists target_user_id text,
    add column if not exists target_name text,
    add column if not exists channel_id text,
    add column if not exists channel_name text,
    add column if not exists ticket_id text,
    add column if not exists ticket_message_id text,
    add column if not exists related_table text,
    add column if not exists related_id text,
    add column if not exists title text,
    add column if not exists description text,
    add column if not exists reason text,
    add column if not exists search_text text,
    add column if not exists metadata jsonb not null default '{}'::jsonb,
    add column if not exists meta jsonb not null default '{}'::jsonb,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists actor_id text,
    add column if not exists target_id text,
    add column if not exists message text;

create index if not exists idx_activity_feed_events_guild_created on public.activity_feed_events (guild_id, created_at desc);
create index if not exists idx_activity_feed_events_guild_type on public.activity_feed_events (guild_id, event_type, created_at desc);

-- ---------------------------------------------------------------------------
-- Member truth and join history. The standalone runtime-stability SQL is folded
-- into the migration chain here, including the join-source/risk fields used by
-- current services. Broader optional profile-card fields stay owned by their
-- feature migrations/fallback logic.
-- ---------------------------------------------------------------------------
create table if not exists public.guild_members (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text not null,
    username text,
    display_name text,
    avatar_url text,
    nickname text,
    joined_at timestamptz,
    departed_at timestamptz,
    left_at timestamptz,
    rejoined_at timestamptz,
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    synced_at timestamptz,
    join_source text,
    verification_source text,
    entry_method text,
    invite_code text,
    invited_by text,
    invited_by_name text,
    vouched_by text,
    vouched_by_name text,
    approved_by text,
    approved_by_name text,
    verification_ticket_id text,
    source_ticket_id text,
    vanity_used boolean not null default false,
    entry_reason text,
    approval_reason text,
    entry_truth_quality text,
    entry_confidence integer,
    entry_quality_reason text,
    entry_conflict boolean not null default false,
    role_state text,
    role_state_reason text,
    risk_score integer not null default 0,
    risk_level text not null default 'low',
    risk_reasons jsonb not null default '[]'::jsonb,
    fingerprint text,
    alt_cluster_key text,
    alt_cluster_size integer not null default 0,
    burst_join_count integer not null default 0,
    same_fingerprint_count integer not null default 0,
    similar_name_count integer not null default 0,
    same_age_bucket_count integer not null default 0,
    suspicious_name_pattern boolean not null default false,
    repeated_char_pattern boolean not null default false,
    default_avatar boolean not null default false,
    account_age_days integer,
    age_bucket text,
    digit_ratio double precision not null default 0,
    underscore_ratio double precision not null default 0,
    cluster_members jsonb not null default '[]'::jsonb,
    suspicion_flags jsonb not null default '[]'::jsonb,
    risk_last_evaluated_at timestamptz,
    last_join_risk_score integer,
    last_join_risk_level text,
    last_join_fingerprint text,
    alt_notes text,
    data_health text,
    is_bot boolean not null default false,
    is_active boolean not null default true,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, user_id)
);

alter table public.guild_members
    add column if not exists username text,
    add column if not exists display_name text,
    add column if not exists avatar_url text,
    add column if not exists nickname text,
    add column if not exists joined_at timestamptz,
    add column if not exists departed_at timestamptz,
    add column if not exists left_at timestamptz,
    add column if not exists rejoined_at timestamptz,
    add column if not exists first_seen_at timestamptz,
    add column if not exists last_seen_at timestamptz,
    add column if not exists synced_at timestamptz,
    add column if not exists join_source text,
    add column if not exists verification_source text,
    add column if not exists entry_method text,
    add column if not exists invite_code text,
    add column if not exists invited_by text,
    add column if not exists invited_by_name text,
    add column if not exists vouched_by text,
    add column if not exists vouched_by_name text,
    add column if not exists approved_by text,
    add column if not exists approved_by_name text,
    add column if not exists verification_ticket_id text,
    add column if not exists source_ticket_id text,
    add column if not exists vanity_used boolean not null default false,
    add column if not exists entry_reason text,
    add column if not exists approval_reason text,
    add column if not exists entry_truth_quality text,
    add column if not exists entry_confidence integer,
    add column if not exists entry_quality_reason text,
    add column if not exists entry_conflict boolean not null default false,
    add column if not exists role_state text,
    add column if not exists role_state_reason text,
    add column if not exists risk_score integer not null default 0,
    add column if not exists risk_level text not null default 'low',
    add column if not exists risk_reasons jsonb not null default '[]'::jsonb,
    add column if not exists fingerprint text,
    add column if not exists alt_cluster_key text,
    add column if not exists alt_cluster_size integer not null default 0,
    add column if not exists burst_join_count integer not null default 0,
    add column if not exists same_fingerprint_count integer not null default 0,
    add column if not exists similar_name_count integer not null default 0,
    add column if not exists same_age_bucket_count integer not null default 0,
    add column if not exists suspicious_name_pattern boolean not null default false,
    add column if not exists repeated_char_pattern boolean not null default false,
    add column if not exists default_avatar boolean not null default false,
    add column if not exists account_age_days integer,
    add column if not exists age_bucket text,
    add column if not exists digit_ratio double precision not null default 0,
    add column if not exists underscore_ratio double precision not null default 0,
    add column if not exists cluster_members jsonb not null default '[]'::jsonb,
    add column if not exists suspicion_flags jsonb not null default '[]'::jsonb,
    add column if not exists risk_last_evaluated_at timestamptz,
    add column if not exists last_join_risk_score integer,
    add column if not exists last_join_risk_level text,
    add column if not exists last_join_fingerprint text,
    add column if not exists alt_notes text,
    add column if not exists data_health text,
    add column if not exists is_bot boolean not null default false,
    add column if not exists is_active boolean not null default true,
    add column if not exists metadata jsonb not null default '{}'::jsonb,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now();

create unique index if not exists uq_guild_members_guild_user on public.guild_members (guild_id, user_id);
create index if not exists idx_guild_members_guild_active on public.guild_members (guild_id, is_active);
create index if not exists idx_guild_members_join_source on public.guild_members (guild_id, join_source);

create table if not exists public.member_joins (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text not null,
    username text,
    display_name text,
    avatar_url text,
    joined_at timestamptz not null default now(),
    departed_at timestamptz,
    entry_method text,
    join_source text,
    verification_source text,
    invite_code text,
    invited_by text,
    invited_by_name text,
    vouched_by text,
    vouched_by_name text,
    approved_by text,
    approved_by_name text,
    join_note text,
    entry_reason text,
    approval_reason text,
    channel_id text,
    channel_name text,
    source_ticket_id text,
    entry_truth_quality text,
    entry_confidence integer,
    entry_quality_reason text,
    entry_conflict boolean not null default false,
    vanity_used boolean not null default false,
    risk_score integer not null default 0,
    risk_level text not null default 'low',
    risk_reasons jsonb not null default '[]'::jsonb,
    fingerprint text,
    alt_cluster_key text,
    alt_cluster_size integer not null default 0,
    burst_join_count integer not null default 0,
    same_fingerprint_count integer not null default 0,
    similar_name_count integer not null default 0,
    same_age_bucket_count integer not null default 0,
    suspicious_name_pattern boolean not null default false,
    repeated_char_pattern boolean not null default false,
    default_avatar boolean not null default false,
    account_age_days integer,
    age_bucket text,
    digit_ratio double precision not null default 0,
    underscore_ratio double precision not null default 0,
    cluster_members jsonb not null default '[]'::jsonb,
    suspicion_flags jsonb not null default '[]'::jsonb,
    risk_evaluated_at timestamptz,
    join_fingerprint text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.member_joins
    add column if not exists username text,
    add column if not exists display_name text,
    add column if not exists avatar_url text,
    add column if not exists joined_at timestamptz not null default now(),
    add column if not exists departed_at timestamptz,
    add column if not exists entry_method text,
    add column if not exists join_source text,
    add column if not exists verification_source text,
    add column if not exists invite_code text,
    add column if not exists invited_by text,
    add column if not exists invited_by_name text,
    add column if not exists vouched_by text,
    add column if not exists vouched_by_name text,
    add column if not exists approved_by text,
    add column if not exists approved_by_name text,
    add column if not exists join_note text,
    add column if not exists entry_reason text,
    add column if not exists approval_reason text,
    add column if not exists channel_id text,
    add column if not exists channel_name text,
    add column if not exists source_ticket_id text,
    add column if not exists entry_truth_quality text,
    add column if not exists entry_confidence integer,
    add column if not exists entry_quality_reason text,
    add column if not exists entry_conflict boolean not null default false,
    add column if not exists vanity_used boolean not null default false,
    add column if not exists risk_score integer not null default 0,
    add column if not exists risk_level text not null default 'low',
    add column if not exists risk_reasons jsonb not null default '[]'::jsonb,
    add column if not exists fingerprint text,
    add column if not exists alt_cluster_key text,
    add column if not exists alt_cluster_size integer not null default 0,
    add column if not exists burst_join_count integer not null default 0,
    add column if not exists same_fingerprint_count integer not null default 0,
    add column if not exists similar_name_count integer not null default 0,
    add column if not exists same_age_bucket_count integer not null default 0,
    add column if not exists suspicious_name_pattern boolean not null default false,
    add column if not exists repeated_char_pattern boolean not null default false,
    add column if not exists default_avatar boolean not null default false,
    add column if not exists account_age_days integer,
    add column if not exists age_bucket text,
    add column if not exists digit_ratio double precision not null default 0,
    add column if not exists underscore_ratio double precision not null default 0,
    add column if not exists cluster_members jsonb not null default '[]'::jsonb,
    add column if not exists suspicion_flags jsonb not null default '[]'::jsonb,
    add column if not exists risk_evaluated_at timestamptz,
    add column if not exists join_fingerprint text,
    add column if not exists metadata jsonb not null default '{}'::jsonb,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_member_joins_guild_user_joined on public.member_joins (guild_id, user_id, joined_at desc);
create index if not exists idx_member_joins_join_source on public.member_joins (guild_id, join_source);
create index if not exists idx_member_joins_entry_quality on public.member_joins (guild_id, entry_truth_quality, joined_at desc);

create table if not exists public.member_events (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text,
    actor_id text,
    actor_name text,
    event_type text not null,
    title text,
    description text,
    reason text,
    source text,
    metadata jsonb not null default '{}'::jsonb,
    meta jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

alter table public.member_events
    add column if not exists guild_id text,
    add column if not exists user_id text,
    add column if not exists actor_id text,
    add column if not exists actor_name text,
    add column if not exists event_type text,
    add column if not exists title text,
    add column if not exists description text,
    add column if not exists reason text,
    add column if not exists source text,
    add column if not exists metadata jsonb not null default '{}'::jsonb,
    add column if not exists meta jsonb not null default '{}'::jsonb,
    add column if not exists created_at timestamptz not null default now();

create index if not exists idx_member_events_guild_user_created on public.member_events (guild_id, user_id, created_at desc);
create index if not exists idx_member_events_guild_type_created on public.member_events (guild_id, event_type, created_at desc);

-- ---------------------------------------------------------------------------
-- Member cleanup control-plane tables formerly created only at bot startup.
-- ---------------------------------------------------------------------------
create table if not exists public.member_activity_scan_locks (
    guild_id text not null,
    user_id text not null,
    active boolean not null default true,
    reason text,
    locked_by text,
    locked_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id)
);

alter table public.member_activity_scan_locks
    add column if not exists active boolean not null default true,
    add column if not exists reason text,
    add column if not exists locked_by text,
    add column if not exists locked_at timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_member_activity_scan_locks_guild_active on public.member_activity_scan_locks (guild_id, active);

create table if not exists public.member_cleanup_settings (
    guild_id text primary key,
    require_queue_confirmation boolean not null default true,
    allow_low_confidence_queue boolean not null default false,
    default_queue_limit integer not null default 10,
    updated_by text,
    updated_at timestamptz not null default now()
);

alter table public.member_cleanup_settings
    add column if not exists require_queue_confirmation boolean not null default true,
    add column if not exists allow_low_confidence_queue boolean not null default false,
    add column if not exists default_queue_limit integer not null default 10,
    add column if not exists updated_by text,
    add column if not exists updated_at timestamptz not null default now();

commit;
