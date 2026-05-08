-- Runtime stability schema hardening for Dank Shield
-- Safe to run more than once.

-- Fixes production errors like:
--   Could not find the 'join_source' column of 'guild_members' in the schema cache
--   Could not find the 'join_source' column of 'member_joins' in the schema cache

create table if not exists public.guild_members (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text not null,
    username text,
    display_name text,
    joined_at timestamptz,
    departed_at timestamptz,
    join_source text,
    is_active boolean not null default true,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, user_id)
);

create table if not exists public.member_joins (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text not null,
    username text,
    display_name text,
    joined_at timestamptz,
    departed_at timestamptz,
    join_source text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, user_id)
);

alter table public.guild_members add column if not exists username text;
alter table public.guild_members add column if not exists display_name text;
alter table public.guild_members add column if not exists joined_at timestamptz;
alter table public.guild_members add column if not exists departed_at timestamptz;
alter table public.guild_members add column if not exists join_source text;
alter table public.guild_members add column if not exists is_active boolean not null default true;
alter table public.guild_members add column if not exists metadata jsonb not null default '{}'::jsonb;
alter table public.guild_members add column if not exists created_at timestamptz not null default now();
alter table public.guild_members add column if not exists updated_at timestamptz not null default now();

alter table public.member_joins add column if not exists username text;
alter table public.member_joins add column if not exists display_name text;
alter table public.member_joins add column if not exists joined_at timestamptz;
alter table public.member_joins add column if not exists departed_at timestamptz;
alter table public.member_joins add column if not exists join_source text;
alter table public.member_joins add column if not exists metadata jsonb not null default '{}'::jsonb;
alter table public.member_joins add column if not exists created_at timestamptz not null default now();
alter table public.member_joins add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_guild_members_guild_user on public.guild_members (guild_id, user_id);
create index if not exists idx_guild_members_guild_active on public.guild_members (guild_id, is_active);
create index if not exists idx_member_joins_guild_user on public.member_joins (guild_id, user_id);
create index if not exists idx_member_joins_join_source on public.member_joins (guild_id, join_source);
