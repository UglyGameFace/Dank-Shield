-- Manual-only, private and lossless PostgreSQL 18 staging of 18 adjunct tables.
-- Raw source includes sensitive verification tokens. Strictly no public grants.
-- These are NOT active authentication, verification, or ticket runtime tables.
create schema if not exists dank_migration;
revoke all on schema dank_migration from public;

create table if not exists dank_migration.aux_staged_originals (
    source_table text not null check (source_table in (
        'guild_members',
        'identity_proofs',
        'manual_alt_links',
        'member_activity_batch_receipts',
        'member_activity_notices',
        'member_activity_scan_locks',
        'member_events',
        'member_joins',
        'ticket_automation_settings',
        'ticket_automation_state',
        'ticket_counters',
        'ticket_macros',
        'ticket_messages',
        'ticket_panel_categories',
        'vc_verify_sessions',
        'verification_flags',
        'verification_kick_timers',
        'verification_tokens'
    )),
    record_key text not null check (record_key ~ '^[a-f0-9]{64}$'),
    source_pk_json text not null check (octet_length(source_pk_json) between 2 and 8192),
    guild_id text,
    source_json_text text not null check (
        octet_length(source_json_text) between 2 and 2097152
        and jsonb_typeof(source_json_text::jsonb) = 'object'
    ),
    source_sha256 text not null check (source_sha256 ~ '^[a-f0-9]{64}$'),
    raw_bytes integer not null check (
        raw_bytes between 2 and 2097152 and raw_bytes = octet_length(source_json_text)
    ),
    staged_at timestamptz not null default now(),
    primary key (source_table, record_key)
);

create index if not exists aux_staged_originals_guild_table
    on dank_migration.aux_staged_originals (guild_id, source_table)
    where guild_id is not null;

revoke all on table dank_migration.aux_staged_originals from public;

comment on table dank_migration.aux_staged_originals is
'Offline original PG JSONB text for ticket, member, and sensitive verification records. Never read by production bot, authenticate from these copied tokens or automatically expire evidence.';