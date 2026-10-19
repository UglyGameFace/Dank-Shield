-- Deliberately isolated, original-text staging for 15 live operational tables.
-- No current Discord reads/writes are routed here; no original data is deleted.
-- Store PG17 JSONB textual source exactly, preserving decimal precision/PKs.
create schema if not exists dank_migration;
revoke all on schema dank_migration from public;

create table if not exists dank_migration.core_staged_originals (
    source_table text not null check (
        source_table in (
            'guild_security_settings',
            'guild_role_rules',
            'guild_roles',
            'member_activity_ledger',
            'member_activity_tracker_state',
            'member_cleanup_settings',
            'member_verify_timers',
            'ticket_categories',
            'ticket_panels',
            'ticket_panel_presets',
            'ticket_panel_rules',
            'tickets',
            'ticket_notes',
            'ticket_form_responses',
            'dank_community_hub_settings'
        )
    ),
    record_key text not null check (record_key ~ '^[a-f0-9]{64}$'),
    source_pk_json text not null check (octet_length(source_pk_json) between 2 and 8192),
    guild_id text,
    source_json_text text not null check (
        octet_length(source_json_text) between 2 and 2097152
        and jsonb_typeof(source_json_text::jsonb) = 'object'
    ),
    source_sha256 text not null check (source_sha256 ~ '^[a-f0-9]{64}$'),
    raw_bytes integer not null check (
        raw_bytes between 2 and 2097152
        and raw_bytes = octet_length(source_json_text)
    ),
    staged_at timestamptz not null default now(),
    primary key (source_table, record_key)
);

create index if not exists core_staged_originals_guild_table
    on dank_migration.core_staged_originals (guild_id, source_table)
    where guild_id is not null;

revoke all on table dank_migration.core_staged_originals from public;

comment on table dank_migration.core_staged_originals is
'Offline, exact original PG JSONB text from operational Supabase source tables. No automatic expiry, runtime reads or cutover authorization.';