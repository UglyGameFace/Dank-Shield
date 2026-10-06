-- Dank Cinema Feed Center personalization, private feeds, and durable source health.
-- Service-role only. Discord users never access these tables directly.

create table if not exists public.dank_cinema_feed_rules (
    id uuid primary key default gen_random_uuid(),
    guild_id bigint not null,
    owner_user_id bigint,
    scope text not null default 'user',
    rule_type text not null default 'saved_search',
    name text not null default '',
    query text not null default '',
    media_type text,
    tmdb_id bigint,
    filters jsonb not null default '{}'::jsonb,
    actions jsonb not null default '{}'::jsonb,
    enabled boolean not null default true,
    created_by bigint not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint dank_cinema_feed_rules_scope_check
        check (scope in ('user', 'guild')),
    constraint dank_cinema_feed_rules_type_check
        check (
            rule_type in (
                'follow',
                'saved_search',
                'filter',
                'collection',
                'routing',
                'private_source'
            )
        ),
    constraint dank_cinema_feed_rules_media_type_check
        check (media_type is null or media_type in ('movie', 'tv')),
    constraint dank_cinema_feed_rules_tmdb_check
        check (tmdb_id is null or tmdb_id > 0),
    constraint dank_cinema_feed_rules_owner_check
        check (
            (scope = 'user' and owner_user_id is not null and owner_user_id > 0)
            or
            (scope = 'guild' and owner_user_id is null)
        ),
    constraint dank_cinema_feed_rules_private_source_scope_check
        check (rule_type <> 'private_source' or scope = 'user')
);

create index if not exists idx_dank_cinema_feed_rules_user
    on public.dank_cinema_feed_rules (guild_id, owner_user_id, updated_at desc)
    where scope = 'user';

create index if not exists idx_dank_cinema_feed_rules_guild
    on public.dank_cinema_feed_rules (guild_id, updated_at desc)
    where scope = 'guild';

create index if not exists idx_dank_cinema_feed_rules_identity
    on public.dank_cinema_feed_rules (guild_id, media_type, tmdb_id)
    where enabled = true and tmdb_id is not null;

create table if not exists public.dank_cinema_user_feed_discoveries (
    user_id bigint not null,
    guild_id bigint not null,
    source_id text not null,
    discovery_key text not null,
    title text not null,
    media_type text,
    tmdb_id bigint,
    metadata jsonb not null default '{}'::jsonb,
    playable boolean not null default true,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    primary key (user_id, guild_id, source_id, discovery_key),
    constraint dank_cinema_user_feed_discoveries_media_type_check
        check (media_type is null or media_type in ('movie', 'tv')),
    constraint dank_cinema_user_feed_discoveries_tmdb_check
        check (tmdb_id is null or tmdb_id > 0)
);

create index if not exists idx_dank_cinema_user_feed_discoveries_recent
    on public.dank_cinema_user_feed_discoveries
        (user_id, guild_id, first_seen_at desc);

create index if not exists idx_dank_cinema_user_feed_discoveries_tmdb
    on public.dank_cinema_user_feed_discoveries
        (user_id, guild_id, media_type, tmdb_id)
    where tmdb_id is not null;

create table if not exists public.dank_cinema_feed_source_health (
    guild_id bigint not null,
    source_id text not null,
    refresh_count bigint not null default 0,
    success_count bigint not null default 0,
    failure_count bigint not null default 0,
    total_results bigint not null default 0,
    last_result_count integer not null default 0,
    last_error text not null default '',
    last_refreshed_at timestamptz,
    last_success_at timestamptz,
    updated_at timestamptz not null default now(),
    primary key (guild_id, source_id),
    constraint dank_cinema_feed_source_health_counts_check
        check (
            refresh_count >= 0
            and success_count >= 0
            and failure_count >= 0
            and total_results >= 0
            and last_result_count >= 0
        )
);

alter table public.dank_cinema_feed_rules enable row level security;
alter table public.dank_cinema_user_feed_discoveries enable row level security;
alter table public.dank_cinema_feed_source_health enable row level security;

revoke all on table public.dank_cinema_feed_rules from anon, authenticated;
revoke all on table public.dank_cinema_user_feed_discoveries from anon, authenticated;
revoke all on table public.dank_cinema_feed_source_health from anon, authenticated;

grant all on table public.dank_cinema_feed_rules to service_role;
grant all on table public.dank_cinema_user_feed_discoveries to service_role;
grant all on table public.dank_cinema_feed_source_health to service_role;

comment on table public.dank_cinema_feed_rules is
    'Service-role-only personal and guild Cinema Feed Center rules, collections, routing, alerts, and private feed definitions.';

comment on table public.dank_cinema_user_feed_discoveries is
    'Service-role-only discoveries from private user-owned Cinema feed sources.';

comment on table public.dank_cinema_feed_source_health is
    'Service-role-only durable refresh outcome counters used for truthful Cinema source health and trust scoring.';
