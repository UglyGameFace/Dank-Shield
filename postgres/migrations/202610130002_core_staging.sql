-- Staging only. NOT the live Dank Shield database schema.
-- Apply explicitly within the existing private-VLAN DankShield-DB instance.
-- Do not modify the voice bot's tables or the reminder delivery ledger.
create schema if not exists dank_migration;
revoke all on schema dank_migration from public;

create table if not exists dank_migration.staged_records (
    source_table text not null,
    record_key text not null,
    source_pk jsonb not null,
    guild_id text,
    row_payload jsonb not null,
    payload_sha256 text not null,
    payload_bytes integer not null,
    copied_at timestamptz not null default now(),
    primary key (source_table, record_key),
    constraint staged_records_sha_format
        check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    constraint staged_records_key_format
        check (record_key ~ '^[a-f0-9]{64}$'),
    constraint staged_records_bounds
        check (payload_bytes between 2 and 2097152),
    constraint staged_records_table_name
        check (source_table ~ '^[a-z][a-z0-9_]{0,95}$')
);

-- PostgreSQL 17 public schema has no special role requirements here.
-- Only the existing private DB owner/explicitly granted operators may read.
revoke all on table dank_migration.staged_records
from public;

comment on table dank_migration.staged_records is
'Temporary lossless source-copy records for an explicitly authorized data migration. Not a live data source; no automatic deletion or startup writes.';
