-- Stoney Verify per-guild configuration
-- ------------------------------------------------------------
-- This table is the long-term replacement for single-server global env IDs.
-- It allows the owner server and every customer server to use the same runtime
-- config path without cross-guild channel/role leaks.

create table if not exists guild_configs (
  guild_id text primary key,

  -- Channel/category config
  modlog_channel_id text null,
  transcripts_channel_id text null,
  ticket_category_id text null,
  ticket_archive_category_id text null,
  verify_channel_id text null,
  vc_verify_channel_id text null,
  vc_verify_queue_channel_id text null,

  -- Role config
  unverified_role_id text null,
  verified_role_id text null,
  resident_role_id text null,
  staff_role_id text null,

  -- Setup/status metadata
  setup_completed boolean not null default false,
  setup_source text not null default 'manual',
  setup_notes text null,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_guild_configs_guild_id
  on guild_configs (guild_id);

create index if not exists idx_guild_configs_setup_completed
  on guild_configs (setup_completed);

-- Keep updated_at fresh on upserts/updates.
create or replace function set_guild_configs_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_guild_configs_updated_at on guild_configs;
create trigger trg_guild_configs_updated_at
before update on guild_configs
for each row
execute function set_guild_configs_updated_at();
