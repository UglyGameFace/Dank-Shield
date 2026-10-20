-- NEW dedicated DankShield Supabase project viamriafkbqhquvrsnua ONLY.
-- Current member, role, verification, identity evidence, activity contracts.
-- Source columns/checks copied without the old project public access holes.
-- NO seed rows, no previously expired token/session restoration,
-- no ephemeral job locks; separate data snapshots follow schema creation.

create table public.guild_members (
    guild_id text not null,
    user_id text not null,
    username text,
    display_name text,
    avatar_url text,
    role_ids text[] not null default '{}'::text[],
    role_names text[] not null default '{}'::text[],
    highest_role_id text,
    highest_role_name text,
    in_guild boolean not null default true,
    has_any_role boolean not null default false,
    data_health text not null default 'ok'::text,
    synced_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    has_unverified boolean not null default false,
    has_verified_role boolean not null default false,
    has_staff_role boolean not null default false,
    has_secondary_verified_role boolean not null default false,
    has_cosmetic_only boolean not null default false,
    role_state text default 'unknown'::text,
    role_state_reason text,
    avatar_hash text,
    nickname text,
    roles jsonb default '[]'::jsonb,
    top_role text,
    joined_at timestamptz default now(),
    previous_usernames jsonb not null default '[]'::jsonb,
    previous_display_names jsonb not null default '[]'::jsonb,
    previous_nicknames jsonb not null default '[]'::jsonb,
    last_seen_username text,
    last_seen_display_name text,
    last_seen_nickname text,
    first_seen_at timestamptz default now(),
    last_seen_at timestamptz default now(),
    left_at timestamptz,
    rejoined_at timestamptz,
    times_joined integer not null default 1,
    times_left integer not null default 0,
    is_bot boolean not null default false,
    invited_by text,
    invited_by_name text,
    invite_code text,
    vouched_by text,
    vouched_by_name text,
    approved_by text,
    approved_by_name text,
    verification_ticket_id text,
    source_ticket_id text,
    entry_method text,
    verification_source text,
    entry_reason text,
    approval_reason text,
    risk_score integer not null default 0,
    risk_level text not null default 'low'::text,
    risk_reasons text[] not null default '{}'::text[],
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
    digit_ratio numeric(6,3),
    underscore_ratio numeric(6,3),
    cluster_members jsonb not null default '[]'::jsonb,
    suspicion_flags text[] not null default '{}'::text[],
    risk_last_evaluated_at timestamptz,
    last_join_risk_score integer,
    last_join_risk_level text,
    last_join_fingerprint text,
    alt_notes text,
    departed_at timestamptz,
    join_source text,
    is_active boolean not null default true,
    metadata jsonb not null default '{}'::jsonb,
    vanity_used boolean not null default false,
    entry_truth_quality text,
    entry_confidence integer,
    entry_quality_reason text,
    entry_conflict boolean not null default false,
    constraint guild_members_data_health_check CHECK ((data_health = ANY (ARRAY['ok'::text, 'missing_role'::text, 'stale'::text, 'left_guild'::text, 'unknown'::text]))),
    constraint guild_members_role_state_check CHECK (((role_state IS NULL) OR (role_state ~ '^[a-z][a-z0-9_]{0,63}$'::text))),
    constraint guild_members_pkey PRIMARY KEY (guild_id, user_id)
);
alter table public.guild_members enable row level security;
revoke all on public.guild_members from public,anon,authenticated;
grant select,insert,update,delete on public.guild_members to service_role;

create table public.guild_roles (
    id uuid not null default gen_random_uuid(),
    guild_id text not null,
    role_id text not null,
    name text not null,
    position integer not null default 0,
    member_count integer default 0,
    constraint guild_roles_pkey PRIMARY KEY (id),
    constraint guild_roles_guild_id_role_id_key UNIQUE (guild_id, role_id)
);
alter table public.guild_roles enable row level security;
revoke all on public.guild_roles from public,anon,authenticated;
grant select,insert,update,delete on public.guild_roles to service_role;

create table public.guild_role_rules (
    guild_id text not null,
    role_id text not null,
    role_name text not null,
    role_group text not null,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint guild_role_rules_role_group_check CHECK ((role_group = ANY (ARRAY['unverified'::text, 'verified'::text, 'secondary_verified'::text, 'staff'::text, 'admin'::text, 'cosmetic'::text, 'excluded'::text]))),
    constraint guild_role_rules_pkey PRIMARY KEY (guild_id, role_id)
);
alter table public.guild_role_rules enable row level security;
revoke all on public.guild_role_rules from public,anon,authenticated;
grant select,insert,update,delete on public.guild_role_rules to service_role;

create table public.member_joins (
    id uuid not null default gen_random_uuid(),
    guild_id text not null,
    user_id text not null,
    username text,
    joined_at timestamptz default now(),
    display_name text,
    avatar_url text,
    invited_by text,
    invited_by_name text,
    invite_code text,
    entry_method text,
    verification_source text,
    vouched_by text,
    vouched_by_name text,
    approved_by text,
    approved_by_name text,
    source_ticket_id text,
    join_note text,
    risk_score integer not null default 0,
    risk_level text not null default 'low'::text,
    risk_reasons text[] not null default '{}'::text[],
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
    digit_ratio numeric(6,3),
    underscore_ratio numeric(6,3),
    cluster_members jsonb not null default '[]'::jsonb,
    suspicion_flags text[] not null default '{}'::text[],
    risk_evaluated_at timestamptz,
    join_fingerprint text,
    departed_at timestamptz,
    join_source text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    entry_reason text,
    approval_reason text,
    channel_id text,
    channel_name text,
    entry_truth_quality text,
    entry_confidence integer,
    entry_quality_reason text,
    entry_conflict boolean not null default false,
    vanity_used boolean not null default false,
    constraint member_joins_pkey PRIMARY KEY (id)
);
alter table public.member_joins enable row level security;
revoke all on public.member_joins from public,anon,authenticated;
grant select,insert,update,delete on public.member_joins to service_role;

create table public.verification_tokens (
    token text not null,
    webhook_url text not null,
    expires_at timestamptz not null,
    used boolean not null default false,
    created_at timestamptz not null default now(),
    guild_id text,
    channel_id text,
    requester_id text,
    decision text default 'PENDING'::text,
    decided_by text,
    decided_at timestamptz,
    user_id text,
    submitted boolean default false,
    submitted_at timestamptz,
    ai_status text,
    owner_display_name text,
    owner_username text,
    owner_tag text,
    approved_user_id text,
    updated_at timestamptz,
    status text default 'pending'::text,
    requester_display_name text,
    requester_username text,
    requester_avatar_url text,
    requester_role_ids text[] default '{}'::text[],
    requester_role_names text[] default '{}'::text[],
    expected_role_state text default 'unverified_only'::text,
    actual_role_state text default 'unknown'::text,
    role_sync_ok boolean default false,
    role_sync_reason text,
    decided_by_display_name text,
    decided_by_username text,
    decided_by_avatar_url text,
    identity_fingerprint text,
    fingerprint_version text default 'v1'::text,
    identity_source text,
    verification_source text,
    proof_captured_at timestamptz,
    submission_meta jsonb not null default '{}'::jsonb,
    constraint verification_tokens_actual_role_state_check CHECK ((actual_role_state = ANY (ARRAY['unverified_only'::text, 'verified_ok'::text, 'verified_conflict'::text, 'staff_ok'::text, 'staff_conflict'::text, 'missing_unverified'::text, 'missing_verified_role'::text, 'booster_only'::text, 'left_guild'::text, 'unknown'::text, 'bot_ok'::text]))),
    constraint verification_tokens_expected_role_state_check CHECK ((expected_role_state = ANY (ARRAY['unverified_only'::text, 'verified_ok'::text, 'staff_ok'::text, 'unknown'::text]))),
    constraint verification_tokens_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'submitted'::text, 'approved'::text, 'denied'::text, 'resubmit'::text, 'used'::text, 'expired'::text]))),
    constraint verification_tokens_pkey PRIMARY KEY (token)
);
alter table public.verification_tokens enable row level security;
revoke all on public.verification_tokens from public,anon,authenticated;
grant select,insert,update,delete on public.verification_tokens to service_role;

create table public.verification_flags (
    id uuid not null default gen_random_uuid(),
    guild_id text not null,
    user_id text not null,
    username text,
    score integer not null default 0,
    reasons jsonb default '[]'::jsonb,
    flagged boolean not null default false,
    created_at timestamptz default now(),
    constraint verification_flags_pkey PRIMARY KEY (id)
);
alter table public.verification_flags enable row level security;
revoke all on public.verification_flags from public,anon,authenticated;
grant select,insert,update,delete on public.verification_flags to service_role;

create table public.verification_kick_timers (
    channel_id text not null,
    guild_id text not null,
    owner_id text not null,
    started_at timestamptz not null,
    hours integer not null,
    started_by text,
    updated_at timestamptz,
    constraint verification_kick_timers_pkey PRIMARY KEY (channel_id)
);
alter table public.verification_kick_timers enable row level security;
revoke all on public.verification_kick_timers from public,anon,authenticated;
grant select,insert,update,delete on public.verification_kick_timers to service_role;

create table public.vc_verify_sessions (
    token text not null,
    guild_id bigint not null,
    ticket_channel_id bigint not null,
    requester_id bigint not null,
    owner_id bigint not null,
    vc_channel_id bigint,
    queue_channel_id bigint,
    queue_message_id bigint,
    status text not null default 'PENDING'::text,
    created_at timestamptz not null default now(),
    accepted_at timestamptz,
    accepted_by bigint,
    started_at timestamptz,
    completed_at timestamptz,
    canceled_at timestamptz,
    canceled_by bigint,
    access_minutes integer not null default 30,
    revoke_at timestamptz,
    last_watchdog_at timestamptz,
    meta jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    ready_at timestamptz,
    started_by bigint,
    completed_by bigint,
    expired_at timestamptz,
    restarted_at timestamptz,
    restarted_by bigint,
    constraint vc_verify_sessions_status_check_v1 CHECK ((status = ANY (ARRAY['PENDING'::text, 'STAFF_ACCEPTED'::text, 'OWNER_CONFIRMED'::text, 'READY'::text, 'IN_VC'::text, 'STARTED'::text, 'TAKEN_OVER'::text, 'RESTARTED'::text, 'COMPLETED'::text, 'DONE'::text, 'CANCELED'::text, 'CANCELLED'::text, 'EXPIRED'::text]))),
    constraint vc_verify_sessions_pkey PRIMARY KEY (token)
);
alter table public.vc_verify_sessions enable row level security;
revoke all on public.vc_verify_sessions from public,anon,authenticated;
grant select,insert,update,delete on public.vc_verify_sessions to service_role;

create table public.member_verify_timers (
    guild_id text not null,
    user_id text not null,
    timer_type text not null,
    started_at timestamptz not null,
    grace_minutes integer,
    hours integer,
    source_channel_id text,
    started_by text,
    updated_at timestamptz not null default now(),
    constraint member_verify_timers_timer_type_check CHECK ((timer_type = ANY (ARRAY['join_grace'::text, 'member_no_ticket'::text]))),
    constraint member_verify_timers_pkey PRIMARY KEY (guild_id, user_id, timer_type)
);
alter table public.member_verify_timers enable row level security;
revoke all on public.member_verify_timers from public,anon,authenticated;
grant select,insert,update,delete on public.member_verify_timers to service_role;

create table public.identity_proofs (
    id bigint not null,
    guild_id text not null,
    user_id text not null,
    identity_fingerprint text not null,
    fingerprint_version text not null default 'v1'::text,
    source text not null,
    confidence integer not null default 100,
    status text not null default 'active'::text,
    created_by text,
    notes text,
    evidence jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    revoked_at timestamptz,
    revoked_by text,
    constraint identity_proofs_confidence_check CHECK (((confidence >= 0) AND (confidence <= 100))),
    constraint identity_proofs_source_check CHECK ((source = ANY (ARRAY['manual_review'::text, 'id_verification'::text, 'voice_verification'::text, 'document_verification'::text, 'selfie_match'::text, 'external_account_link'::text, 'trusted_admin_override'::text]))),
    constraint identity_proofs_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text]))),
    constraint identity_proofs_pkey PRIMARY KEY (id)
);
alter table public.identity_proofs enable row level security;
revoke all on public.identity_proofs from public,anon,authenticated;
grant select,insert,update,delete on public.identity_proofs to service_role;

create table public.manual_alt_links (
    id bigint not null,
    guild_id text not null,
    user_a_id text not null,
    user_b_id text not null,
    link_type text not null,
    status text not null default 'active'::text,
    created_by text not null,
    reason text,
    evidence jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    revoked_at timestamptz,
    revoked_by text,
    constraint manual_alt_links_distinct_users_check CHECK ((user_a_id <> user_b_id)),
    constraint manual_alt_links_link_type_check CHECK ((link_type = ANY (ARRAY['confirmed_duplicate'::text, 'same_person_likely'::text, 'not_linked'::text]))),
    constraint manual_alt_links_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text]))),
    constraint manual_alt_links_pkey PRIMARY KEY (id)
);
alter table public.manual_alt_links enable row level security;
revoke all on public.manual_alt_links from public,anon,authenticated;
grant select,insert,update,delete on public.manual_alt_links to service_role;

create table public.member_activity_ledger (
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
    constraint member_activity_ledger_pkey PRIMARY KEY (guild_id, user_id)
);
alter table public.member_activity_ledger enable row level security;
revoke all on public.member_activity_ledger from public,anon,authenticated;
grant select,insert,update,delete on public.member_activity_ledger to service_role;

create table public.member_activity_tracker_state (
    guild_id text not null,
    process_id text not null,
    continuous_since timestamptz not null,
    last_heartbeat_at timestamptz not null,
    last_error_at timestamptz,
    last_error text,
    event_writes_failed bigint not null default 0,
    updated_at timestamptz not null default now(),
    constraint member_activity_tracker_state_pkey PRIMARY KEY (guild_id)
);
alter table public.member_activity_tracker_state enable row level security;
revoke all on public.member_activity_tracker_state from public,anon,authenticated;
grant select,insert,update,delete on public.member_activity_tracker_state to service_role;

create table public.member_activity_scan_locks (
    guild_id text not null,
    user_id text not null,
    active boolean not null default true,
    reason text,
    locked_by text,
    locked_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint member_activity_scan_locks_pkey PRIMARY KEY (guild_id, user_id)
);
alter table public.member_activity_scan_locks enable row level security;
revoke all on public.member_activity_scan_locks from public,anon,authenticated;
grant select,insert,update,delete on public.member_activity_scan_locks to service_role;

create table public.member_activity_notices (
    notice_id text not null,
    guild_id text not null,
    guild_name text,
    user_id text not null,
    user_display_name text,
    scope text,
    status text not null default 'scheduled'::text,
    send_at timestamptz not null,
    deadline_at timestamptz not null,
    created_by text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    sent_at timestamptz,
    attempted_at timestamptz,
    responded_at timestamptz,
    response text,
    note text,
    confidence text,
    inactivity_days integer,
    removable boolean default false,
    error text,
    dm_message_id text,
    constraint member_activity_notices_pkey PRIMARY KEY (notice_id)
);
alter table public.member_activity_notices enable row level security;
revoke all on public.member_activity_notices from public,anon,authenticated;
grant select,insert,update,delete on public.member_activity_notices to service_role;

create table public.member_activity_batch_receipts (
    batch_id uuid not null,
    applied_at timestamptz not null default now(),
    constraint member_activity_batch_receipts_pkey PRIMARY KEY (batch_id)
);
alter table public.member_activity_batch_receipts enable row level security;
revoke all on public.member_activity_batch_receipts from public,anon,authenticated;
grant select,insert,update,delete on public.member_activity_batch_receipts to service_role;
alter table public.verification_tokens add constraint verification_tokens_token_len_chk CHECK ((length(token) >= 6)) NOT VALID;
alter table public.verification_tokens add constraint verification_tokens_webhook_url_not_empty_chk CHECK ((length(webhook_url) > 0)) NOT VALID;

-- Surgical covering indexes; keep original primary/unique constraints.
create index idx_dank_member_guild_active on public.guild_members(guild_id,is_active);
create index idx_dank_member_guild_updated on public.guild_members(guild_id,updated_at desc);
create index idx_dank_member_join_guild_recent on public.member_joins(guild_id,created_at desc);
create index idx_dank_member_ledger_recent on public.member_activity_ledger(guild_id,last_message_at desc);
create index idx_dank_member_roles_guild on public.guild_roles(guild_id);
create index idx_dank_vc_verify_status on public.vc_verify_sessions(guild_id,status,created_at desc);
create index idx_dank_verify_expiry on public.verification_tokens(expires_at) where used=false;
create unique index uq_dank_identity_active_exact on public.identity_proofs(guild_id,user_id,identity_fingerprint,source,fingerprint_version) where status='active';
create unique index uq_dank_manual_alt_active_pair on public.manual_alt_links(guild_id,user_a_id,user_b_id) where status='active';
