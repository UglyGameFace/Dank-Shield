-- Shadow-only canonical guild settings, deliberately NOT wired to Discord.
-- Explicit private Discloud PG17 schema migration, never at bot startup.
-- Preserve public reminder ledger, voice-bot tables and source Supabase data.
create schema if not exists dank_core;
revoke all on schema dank_core from public;

create table if not exists dank_core.guild_config_shadow (
    guild_id text primary key,
    source_pk jsonb not null,
    raw_row jsonb not null,
    source_sha256 text not null
        check (source_sha256 ~ '^[a-f0-9]{64}$'),
    imported_at timestamptz not null default now(),
    constraint shadow_guild_id_format
        check (guild_id ~ '^[1-9][0-9]{0,19}$'),
    constraint shadow_payload_guild_matches
        check (raw_row->>'guild_id' = guild_id),
    constraint shadow_source_pk_contains_id
        check (source_pk ? 'id'),
    constraint shadow_payload_bounded
        check (pg_column_size(raw_row) <= 2097152)
);

revoke all on table dank_core.guild_config_shadow from public;

comment on table dank_core.guild_config_shadow is
'Verified imported original guild_config rows. Shadow-read only, not live.';
