-- ============================================================
-- 20260426_guild_configs.sql
-- ------------------------------------------------------------
-- Per-guild runtime configuration for public / multi-server use.
--
-- This table lets the bot stop depending on one process-wide GUILD_ID
-- and one process-wide set of channel/role ids.
--
-- Safe to run more than once.
-- ============================================================

create table if not exists public.guild_configs (
    guild_id text primary key,

    -- Discord server metadata
    guild_name text,
    owner_id text,

    -- Verification channels
    verify_channel_id text,
    vc_verify_channel_id text,
    vc_verify_queue_channel_id text,

    -- Tickets
    ticket_category_id text,
    ticket_prefix text not null default 'ticket',
    auto_delete_ticket_seconds integer not null default 0,
    transcripts_channel_id text,
    transcript_panel_name text not null default 'Support',
    single_panel_mode boolean not null default true,
    join_log_channel_id text,

    -- Verification timers
    token_ttl_minutes integer not null default 240,
    vc_request_ttl_minutes integer not null default 240,
    verify_kick_hours integer not null default 24,
    vc_request_cooldown_seconds integer not null default 60,

    -- Roles
    unverified_role_id text,
    verified_role_id text,
    resident_role_id text,
    stoner_role_id text,
    drunken_role_id text,
    staff_role_id text,
    vc_staff_role_id text,

    -- Logs
    modlog_channel_id text,
    raidlog_channel_id text,
    force_verify_log_channel_id text,

    -- Optional role prompt
    enable_optional_role_prompt boolean not null default true,
    optional_role_auto_close_seconds integer not null default 0,

    -- Flexible future settings without another migration
    settings jsonb not null default '{}'::jsonb,
    metadata jsonb not null default '{}'::jsonb,

    -- Lifecycle
    enabled boolean not null default true,
    public_beta_enabled boolean not null default false,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_guild_configs_enabled
    on public.guild_configs (enabled);

create index if not exists idx_guild_configs_public_beta_enabled
    on public.guild_configs (public_beta_enabled);

create index if not exists idx_guild_configs_updated_at
    on public.guild_configs (updated_at desc);

create or replace function public.set_updated_at()
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
execute function public.set_updated_at();

-- Keep RLS enabled for dashboard/frontend safety.
-- Your bot uses the Supabase service role key, which bypasses RLS.
alter table public.guild_configs enable row level security;

-- No public read/write policies are created here on purpose.
-- Add dashboard-specific policies later when auth/tenancy is wired.

-- ============================================================
-- Current beta server seed row
-- ------------------------------------------------------------
-- This is not a secret; these are Discord snowflake IDs.
-- You can run this as-is for your current server, then each new
-- public server should insert/update its own row during onboarding.
-- ============================================================

insert into public.guild_configs (
    guild_id,
    guild_name,
    verify_channel_id,
    vc_verify_channel_id,
    vc_verify_queue_channel_id,
    ticket_category_id,
    ticket_prefix,
    transcripts_channel_id,
    unverified_role_id,
    verified_role_id,
    resident_role_id,
    stoner_role_id,
    drunken_role_id,
    staff_role_id,
    vc_staff_role_id,
    verify_kick_hours,
    token_ttl_minutes,
    vc_request_ttl_minutes,
    public_beta_enabled,
    settings,
    metadata
) values (
    '1357215261001912320',
    'Stoney Verify Beta',
    '1470388622095028254',
    '1470388622095028254',
    '1476977094729793710',
    '1478111361660751892',
    'ticket',
    '1412170622968008766',
    '1476072864812634132',
    '1357222629148328016',
    '1414682894873395210',
    '1358814936385716325',
    '1414672682192076911',
    '1385377584019144835',
    '1385377584019144835',
    24,
    240,
    240,
    true,
    '{}'::jsonb,
    jsonb_build_object('seeded_by', '20260426_guild_configs.sql')
)
on conflict (guild_id) do update set
    guild_name = excluded.guild_name,
    verify_channel_id = excluded.verify_channel_id,
    vc_verify_channel_id = excluded.vc_verify_channel_id,
    vc_verify_queue_channel_id = excluded.vc_verify_queue_channel_id,
    ticket_category_id = excluded.ticket_category_id,
    ticket_prefix = excluded.ticket_prefix,
    transcripts_channel_id = excluded.transcripts_channel_id,
    unverified_role_id = excluded.unverified_role_id,
    verified_role_id = excluded.verified_role_id,
    resident_role_id = excluded.resident_role_id,
    stoner_role_id = excluded.stoner_role_id,
    drunken_role_id = excluded.drunken_role_id,
    staff_role_id = excluded.staff_role_id,
    vc_staff_role_id = excluded.vc_staff_role_id,
    verify_kick_hours = excluded.verify_kick_hours,
    token_ttl_minutes = excluded.token_ttl_minutes,
    vc_request_ttl_minutes = excluded.vc_request_ttl_minutes,
    public_beta_enabled = excluded.public_beta_enabled,
    metadata = public.guild_configs.metadata || excluded.metadata,
    updated_at = now();

-- Quick check after running:
-- select guild_id, guild_name, ticket_category_id, staff_role_id, public_beta_enabled from public.guild_configs;
