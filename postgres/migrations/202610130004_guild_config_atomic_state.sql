-- Explicit private-network PG17 migration. NOT used by running Dank Shield.
-- A small canonical row with revision is more efficient than keeping a full
-- JSON snapshot on every update. The audited shadow is a separate source copy.
create schema if not exists dank_core;
revoke all on schema dank_core from public;

create table if not exists dank_core.guild_config_state (
    guild_id text primary key,
    row_payload jsonb not null,
    revision bigint not null default 0 check (revision >= 0),
    changed_at timestamptz not null default now(),
    constraint live_config_guild_snowflake
        check (guild_id ~ '^[1-9][0-9]{0,19}$'),
    constraint live_config_matches_guild
        check (jsonb_typeof(row_payload) = 'object'
            and row_payload ->> 'guild_id' = guild_id),
    constraint live_config_size
        check (pg_column_size(row_payload) <= 2097152)
);
revoke all on table dank_core.guild_config_state from public;

comment on table dank_core.guild_config_state is
'Disabled-by-default future canonical guild config. Only explicit imported rows; no runtime routing, no startup schema changes.';
