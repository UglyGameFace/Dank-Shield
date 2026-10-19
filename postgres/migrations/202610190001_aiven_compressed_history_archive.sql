-- Private, lossless history archive for the isolated Aiven PostgreSQL 18 destination.
-- This is NOT a live Discord repository, retention job, backup replacement, or cutover.
-- The import preserves original rows in individually verified zlib-compressed JSON.
create schema if not exists dank_archive;
revoke all on schema dank_archive from public;

create table if not exists dank_archive.original_rows (
    source_table text not null check (
        source_table in (
            'guild_config_versions',
            'staff_action_logs',
            'activity_feed_events',
            'dank_invite_block_events'
        )
    ),
    record_key text not null check (length(record_key) between 1 and 80),
    guild_id text not null check (guild_id ~ '^[1-9][0-9]{0,19}$'),
    event_at timestamptz not null,
    source_sha256 text not null check (source_sha256 ~ '^[a-f0-9]{64}$'),
    payload_codec text not null default 'zlib-json-v1'
        check (payload_codec = 'zlib-json-v1'),
    raw_bytes integer not null check (raw_bytes between 2 and 1048576),
    compressed_payload bytea not null check (
        octet_length(compressed_payload) between 2 and 1048576
    ),
    archived_at timestamptz not null default now(),
    primary key (source_table, record_key)
);

create index if not exists original_rows_guild_time
    on dank_archive.original_rows (guild_id, event_at desc);

revoke all on table dank_archive.original_rows from public;

comment on table dank_archive.original_rows is
'Immutable original Supabase historical JSON rows; decompress and verify SHA-256 before read. Not the live feed or an independent encrypted backup. Never auto-delete.';