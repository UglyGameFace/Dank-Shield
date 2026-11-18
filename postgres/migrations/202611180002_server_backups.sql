-- Dank Shield Pro: whole-server structure backups (Phase 3 flagship, 2026-11-18).
-- A Pro-covered guild keeps up to 10 structure snapshots (categories, channels,
-- roles, permission overwrites, guild settings) for plan-first restore.
-- Dank Shield's own guild config is NOT duplicated here; it stays versioned by
-- Config History (Supabase guild_config_versions) and is linked by version id.
-- Apply explicitly after backup; the bot treats a missing table as "no backups".
create table if not exists public.dank_server_backups (
  backup_id bigint generated always as identity primary key,
  guild_id bigint not null,
  created_by bigint not null default 0,
  label text not null default '',
  created_at timestamptz not null default now(),
  schema_version integer not null default 1,
  snapshot jsonb not null,
  snapshot_bytes integer not null default 0,
  restore_state jsonb
);

create index if not exists idx_dank_server_backups_guild_created
  on public.dank_server_backups (guild_id, created_at desc);
