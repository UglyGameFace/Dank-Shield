-- New dedicated offline Supabase candidate only: original canonical profile
-- schema followed by its per-member live-card ownership migration.
-- Original production data, user identities and privacy settings are not
-- imported or rewritten here. Final source parity is a cutover prerequisite.
-- ORIGINAL: supabase/migrations/20260725_live_profile_cards.sql

-- ============================================================
-- 20260725_live_profile_cards.sql
-- ------------------------------------------------------------
-- Private member profile preferences, platform identities, and
-- bot-owned live-card message state.
--
-- Security:
-- - RLS stays enabled;
-- - no anon/authenticated policies are created;
-- - Dank Shield accesses these records with its service role;
-- - external identities are private until the member explicitly shares them.
--
-- Safe to run more than once.
-- ============================================================

create table if not exists public.dank_profile_users (
    user_id text primary key,
    preferences jsonb not null default '{}'::jsonb,
    platforms jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint dank_profile_users_preferences_object
        check (jsonb_typeof(preferences) = 'object'),
    constraint dank_profile_users_platforms_object
        check (jsonb_typeof(platforms) = 'object')
);

create table if not exists public.dank_profile_guild_settings (
    guild_id text not null,
    user_id text not null,
    settings jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id),
    constraint dank_profile_guild_settings_object
        check (jsonb_typeof(settings) = 'object')
);

create index if not exists idx_dank_profile_guild_settings_user
    on public.dank_profile_guild_settings (user_id, guild_id);

create table if not exists public.dank_live_profile_cards (
    guild_id text not null,
    channel_id text not null,
    message_id text,
    user_id text,
    trigger_message_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, channel_id)
);

create index if not exists idx_dank_live_profile_cards_guild
    on public.dank_live_profile_cards (guild_id, channel_id);

create index if not exists idx_dank_live_profile_cards_user
    on public.dank_live_profile_cards (user_id, guild_id, channel_id);

alter table public.dank_profile_users enable row level security;
alter table public.dank_profile_guild_settings enable row level security;
alter table public.dank_live_profile_cards enable row level security;

-- Explicitly keep member profile data inaccessible to public Supabase clients.
revoke all on table public.dank_profile_users from anon, authenticated;
revoke all on table public.dank_profile_guild_settings from anon, authenticated;
revoke all on table public.dank_live_profile_cards from anon, authenticated;

comment on table public.dank_profile_users is
    'Service-role-only global Dank Shield member profile preferences and platform identities.';
comment on table public.dank_profile_guild_settings is
    'Service-role-only per-guild member privacy overrides for Dank Shield profile cards.';
comment on table public.dank_live_profile_cards is
    'Service-role-only ownership state for one bot-authored live profile card per configured channel.';


-- LATER PRODUCTION SCHEMA: supabase/migrations/202607270001_live_profile_cards_per_member.sql

-- ============================================================
-- 202607270001_live_profile_cards_per_member.sql
-- ------------------------------------------------------------
-- Change live-profile ownership from one card per channel to one
-- card per member per channel. Existing channel rows are preserved.
--
-- Safe to run more than once. Service-role-only access and RLS from
-- the original profile migration remain unchanged.
-- ============================================================

do $migration$
declare
    current_primary_key text;
begin
    if to_regclass('public.dank_live_profile_cards') is null then
        raise notice 'Skipping per-member live profile migration: table does not exist.';
        return;
    end if;

    -- Rows created by the old runtime should always have a user id. Remove only
    -- unusable ownership rows before making the column part of the primary key.
    delete from public.dank_live_profile_cards
    where nullif(btrim(user_id), '') is null;

    alter table public.dank_live_profile_cards
        alter column user_id set not null;

    select conname
      into current_primary_key
      from pg_constraint
     where conrelid = 'public.dank_live_profile_cards'::regclass
       and contype = 'p'
     limit 1;

    if current_primary_key is not null then
        execute format(
            'alter table public.dank_live_profile_cards drop constraint %I',
            current_primary_key
        );
    end if;

    alter table public.dank_live_profile_cards
        add constraint dank_live_profile_cards_pkey
        primary key (guild_id, channel_id, user_id);

    create index if not exists idx_dank_live_profile_cards_channel
        on public.dank_live_profile_cards (guild_id, channel_id, updated_at desc);

    comment on table public.dank_live_profile_cards is
        'Service-role-only ownership state for one bot-authored live profile card per member per configured channel.';
end
$migration$;


-- Dedicated backend: make service-only access explicit.
revoke all on public.dank_profile_users from public, anon, authenticated;
revoke all on public.dank_profile_guild_settings from public, anon, authenticated;
revoke all on public.dank_live_profile_cards from public, anon, authenticated;
grant select, insert, update, delete on public.dank_profile_users to service_role;
grant select, insert, update, delete on public.dank_profile_guild_settings to service_role;
grant select, insert, update, delete on public.dank_live_profile_cards to service_role;
