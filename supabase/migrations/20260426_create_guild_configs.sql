-- ============================================================
-- 20260426_create_guild_configs.sql
-- ------------------------------------------------------------
-- Per-guild public/beta configuration for Stoney Verify.
--
-- Why this table exists:
-- - Public bots cannot rely on one env GUILD_ID / one env channel setup.
-- - Every server stores its own category/channel/role IDs by guild_id.
-- - Bot code reads this table with the Supabase service role.
--
-- Security model:
-- - RLS is enabled.
-- - No anon/authenticated read/write policies are created here.
-- - The bot backend should use SUPABASE_SERVICE_ROLE_KEY only server-side.
-- ============================================================

create extension if not exists pgcrypto;

create table if not exists public.guild_configs (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null unique,

    -- Flexible JSON payloads. The bot prefers settings, but supports config/meta too.
    settings jsonb not null default '{}'::jsonb,
    config jsonb not null default '{}'::jsonb,
    metadata jsonb not null default '{}'::jsonb,
    meta jsonb not null default '{}'::jsonb,

    -- Ticket config
    ticket_category_id text,
    ticket_archive_category_id text,
    ticket_prefix text not null default 'ticket',
    auto_delete_ticket_seconds integer not null default 0,
    transcripts_channel_id text,
    transcript_panel_name text not null default 'Support',
    single_panel_mode boolean not null default true,

    -- Verification config
    verify_channel_id text,
    vc_verify_channel_id text,
    vc_verify_queue_channel_id text,
    token_ttl_minutes integer not null default 240,
    vc_request_ttl_minutes integer not null default 240,
    verify_kick_hours integer not null default 24,
    vc_request_cooldown_seconds integer not null default 60,

    -- Role config
    unverified_role_id text,
    verified_role_id text,
    resident_role_id text,
    stoner_role_id text,
    drunken_role_id text,
    staff_role_id text,
    vc_staff_role_id text,

    -- Log config
    modlog_channel_id text,
    raidlog_channel_id text,
    join_log_channel_id text,
    force_verify_log_channel_id text,

    -- Optional role prompt config
    enable_optional_role_prompt boolean not null default true,
    optional_role_auto_close_seconds integer not null default 0,

    -- Public-safe audit metadata. No secrets/tokens belong in this table.
    configured_by_id text,
    configured_by_name text,
    configured_at timestamptz,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_guild_configs_guild_id on public.guild_configs (guild_id);
create index if not exists idx_guild_configs_updated_at on public.guild_configs (updated_at desc);

create or replace function public.set_guild_configs_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_guild_configs_updated_at on public.guild_configs;
create trigger trg_guild_configs_updated_at
before update on public.guild_configs
for each row
execute function public.set_guild_configs_updated_at();

alter table public.guild_configs enable row level security;

-- Keep direct client access closed by default. The service role bypasses RLS.
-- Do not add anon/authenticated policies unless you later build a scoped web dashboard.

comment on table public.guild_configs is 'Per-guild Stoney Verify configuration. Server-side service role only; no public secrets.';
comment on column public.guild_configs.guild_id is 'Discord guild/server ID as text.';
comment on column public.guild_configs.settings is 'Flexible per-guild config JSON used by public setup commands.';
comment on column public.guild_configs.config is 'Compatibility JSON config payload.';
comment on column public.guild_configs.metadata is 'Compatibility metadata payload.';
comment on column public.guild_configs.meta is 'Compatibility metadata payload.';
