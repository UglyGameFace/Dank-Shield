-- Dank Shield dedicated Supabase ONLY; no live source changes or data import.
-- Preserves the canonical Cinema Library, Feed, intelligence and standalone
-- playback schemas in exact original sequence, with service_role CRUD grants.
-- Member private histories, preferences, and active playback not copied here.

-- SOURCE: supabase/migrations/20261004123000_dank_cinema_library.sql
-- Dank Cinema user library, progress, profile preferences, and notifications.
-- Service-role only. Discord users never access these tables directly.

create table if not exists public.dank_cinema_users (
    user_id bigint primary key,
    preferences jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.dank_cinema_user_media (
    user_id bigint not null,
    media_type text not null,
    tmdb_id bigint not null,
    season_number integer not null default 0,
    episode_number integer not null default 0,
    title text not null default '',
    metadata jsonb not null default '{}'::jsonb,
    watchlisted boolean not null default false,
    progress_seconds double precision not null default 0,
    duration_seconds double precision not null default 0,
    completed boolean not null default false,
    last_watched_at timestamptz,
    watchlisted_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (user_id, media_type, tmdb_id, season_number, episode_number),
    constraint dank_cinema_user_media_type_check
        check (media_type in ('movie', 'tv', 'episode')),
    constraint dank_cinema_user_media_tmdb_check
        check (tmdb_id > 0),
    constraint dank_cinema_user_media_season_check
        check (season_number >= 0),
    constraint dank_cinema_user_media_episode_check
        check (episode_number >= 0),
    constraint dank_cinema_user_media_progress_check
        check (progress_seconds >= 0),
    constraint dank_cinema_user_media_duration_check
        check (duration_seconds >= 0)
);

create index if not exists idx_dank_cinema_user_media_recent
    on public.dank_cinema_user_media (user_id, last_watched_at desc)
    where last_watched_at is not null;

create index if not exists idx_dank_cinema_user_media_watchlist
    on public.dank_cinema_user_media (user_id, watchlisted_at desc)
    where watchlisted = true;

create index if not exists idx_dank_cinema_user_media_series_progress
    on public.dank_cinema_user_media (user_id, tmdb_id, season_number, episode_number)
    where media_type = 'episode';

create table if not exists public.dank_cinema_feed_discoveries (
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
    primary key (guild_id, source_id, discovery_key),
    constraint dank_cinema_feed_discoveries_media_type_check
        check (media_type is null or media_type in ('movie', 'tv')),
    constraint dank_cinema_feed_discoveries_tmdb_check
        check (tmdb_id is null or tmdb_id > 0)
);

create index if not exists idx_dank_cinema_feed_discoveries_recent
    on public.dank_cinema_feed_discoveries (guild_id, first_seen_at desc);

create index if not exists idx_dank_cinema_feed_discoveries_tmdb
    on public.dank_cinema_feed_discoveries (guild_id, media_type, tmdb_id)
    where tmdb_id is not null;

create table if not exists public.dank_cinema_notifications (
    id uuid primary key default gen_random_uuid(),
    user_id bigint not null,
    guild_id bigint,
    kind text not null default 'info',
    title text not null,
    body text not null default '',
    action jsonb not null default '{}'::jsonb,
    dedupe_key text,
    read_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists idx_dank_cinema_notifications_user
    on public.dank_cinema_notifications (user_id, created_at desc);

create unique index if not exists idx_dank_cinema_notifications_dedupe
    on public.dank_cinema_notifications (user_id, dedupe_key)
    where dedupe_key is not null;

alter table public.dank_cinema_users enable row level security;
alter table public.dank_cinema_user_media enable row level security;
alter table public.dank_cinema_feed_discoveries enable row level security;
alter table public.dank_cinema_notifications enable row level security;

revoke all on table public.dank_cinema_users from anon, authenticated;
revoke all on table public.dank_cinema_user_media from anon, authenticated;
revoke all on table public.dank_cinema_feed_discoveries from anon, authenticated;
revoke all on table public.dank_cinema_notifications from anon, authenticated;

grant all on table public.dank_cinema_users to service_role;
grant all on table public.dank_cinema_user_media to service_role;
grant all on table public.dank_cinema_feed_discoveries to service_role;
grant all on table public.dank_cinema_notifications to service_role;

comment on table public.dank_cinema_users is
    'Service-role-only Dank Cinema user preferences keyed by Discord user id.';
comment on table public.dank_cinema_user_media is
    'Service-role-only Dank Cinema watchlist, history, movie progress, and episode progress.';
comment on table public.dank_cinema_notifications is
    'Service-role-only Dank Cinema notification inbox for Discord-linked users.';


comment on table public.dank_cinema_feed_discoveries is
    'Service-role-only durable discoveries from configured Cinema feed/source refreshes.';


-- NEXT CANONICAL DEPENDENCY


-- SOURCE: supabase/migrations/20261006031500_dank_cinema_feed_personalization.sql
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
                'person',
                'genre',
                'studio',
                'franchise',
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


-- NEXT CANONICAL DEPENDENCY


-- SOURCE: supabase/migrations/20261006050000_dank_cinema_library_intelligence.sql
-- Dank Cinema bot-native Library intelligence.
-- Extends the existing service-role-only Cinema Library without introducing
-- cross-platform scrobbling or a second playback authority.

alter table public.dank_cinema_user_media
    add column if not exists favorite boolean not null default false,
    add column if not exists favorite_at timestamptz,
    add column if not exists rating smallint,
    add column if not exists rated_at timestamptz,
    add column if not exists play_count integer not null default 0,
    add column if not exists first_watched_at timestamptz,
    add column if not exists last_completed_at timestamptz;

do $$
begin
    if not exists (
        select 1
          from pg_constraint
         where conname = 'dank_cinema_user_media_rating_check'
           and conrelid = 'public.dank_cinema_user_media'::regclass
    ) then
        alter table public.dank_cinema_user_media
            add constraint dank_cinema_user_media_rating_check
            check (rating is null or rating between 1 and 10);
    end if;

    if not exists (
        select 1
          from pg_constraint
         where conname = 'dank_cinema_user_media_play_count_check'
           and conrelid = 'public.dank_cinema_user_media'::regclass
    ) then
        alter table public.dank_cinema_user_media
            add constraint dank_cinema_user_media_play_count_check
            check (play_count >= 0);
    end if;
end
$$;

create index if not exists idx_dank_cinema_user_media_favorites
    on public.dank_cinema_user_media (user_id, favorite_at desc)
    where favorite = true;

create index if not exists idx_dank_cinema_user_media_ratings
    on public.dank_cinema_user_media (user_id, rating desc, rated_at desc)
    where rating is not null;

create index if not exists idx_dank_cinema_user_media_completed
    on public.dank_cinema_user_media (user_id, last_completed_at desc)
    where play_count > 0;

create table if not exists public.dank_cinema_watch_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id bigint not null,
    session_key text not null,
    guild_id bigint,
    room_id text not null default '',
    session_mode text not null default 'private',
    candidate_id text not null default '',
    media_type text not null,
    tmdb_id bigint not null,
    series_id bigint,
    season_number integer not null default 0,
    episode_number integer not null default 0,
    title text not null default '',
    metadata jsonb not null default '{}'::jsonb,
    is_host boolean not null default false,
    max_progress_seconds double precision not null default 0,
    duration_seconds double precision not null default 0,
    completed boolean not null default false,
    started_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint dank_cinema_watch_sessions_user_key_unique
        unique (user_id, session_key),
    constraint dank_cinema_watch_sessions_mode_check
        check (session_mode in ('private', 'watch_party')),
    constraint dank_cinema_watch_sessions_media_type_check
        check (media_type in ('movie', 'episode')),
    constraint dank_cinema_watch_sessions_tmdb_check
        check (tmdb_id > 0),
    constraint dank_cinema_watch_sessions_series_check
        check (series_id is null or series_id > 0),
    constraint dank_cinema_watch_sessions_season_check
        check (season_number >= 0),
    constraint dank_cinema_watch_sessions_episode_check
        check (episode_number >= 0),
    constraint dank_cinema_watch_sessions_progress_check
        check (max_progress_seconds >= 0),
    constraint dank_cinema_watch_sessions_duration_check
        check (duration_seconds >= 0)
);

create index if not exists idx_dank_cinema_watch_sessions_user_recent
    on public.dank_cinema_watch_sessions (user_id, last_seen_at desc);

create index if not exists idx_dank_cinema_watch_sessions_user_completed
    on public.dank_cinema_watch_sessions (user_id, completed_at desc)
    where completed = true;

create index if not exists idx_dank_cinema_watch_sessions_guild_recent
    on public.dank_cinema_watch_sessions (guild_id, last_seen_at desc)
    where guild_id is not null;

create table if not exists public.dank_cinema_lists (
    id uuid primary key default gen_random_uuid(),
    user_id bigint not null,
    name text not null,
    description text not null default '',
    position integer not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint dank_cinema_lists_position_check
        check (position >= 0)
);

create unique index if not exists idx_dank_cinema_lists_user_name
    on public.dank_cinema_lists (user_id, lower(name));

create index if not exists idx_dank_cinema_lists_user_order
    on public.dank_cinema_lists (user_id, position, created_at);

create table if not exists public.dank_cinema_list_items (
    list_id uuid not null
        references public.dank_cinema_lists(id) on delete cascade,
    user_id bigint not null,
    media_type text not null,
    tmdb_id bigint not null,
    season_number integer not null default 0,
    episode_number integer not null default 0,
    title text not null default '',
    metadata jsonb not null default '{}'::jsonb,
    position integer not null default 0,
    added_at timestamptz not null default now(),
    primary key (
        list_id,
        media_type,
        tmdb_id,
        season_number,
        episode_number
    ),
    constraint dank_cinema_list_items_media_type_check
        check (media_type in ('movie', 'tv', 'episode')),
    constraint dank_cinema_list_items_tmdb_check
        check (tmdb_id > 0),
    constraint dank_cinema_list_items_season_check
        check (season_number >= 0),
    constraint dank_cinema_list_items_episode_check
        check (episode_number >= 0),
    constraint dank_cinema_list_items_position_check
        check (position >= 0)
);

create index if not exists idx_dank_cinema_list_items_user
    on public.dank_cinema_list_items (user_id, added_at desc);

create index if not exists idx_dank_cinema_list_items_order
    on public.dank_cinema_list_items (list_id, position, added_at);

alter table public.dank_cinema_watch_sessions enable row level security;
alter table public.dank_cinema_lists enable row level security;
alter table public.dank_cinema_list_items enable row level security;

revoke all on table public.dank_cinema_watch_sessions from anon, authenticated;
revoke all on table public.dank_cinema_lists from anon, authenticated;
revoke all on table public.dank_cinema_list_items from anon, authenticated;

grant all on table public.dank_cinema_watch_sessions to service_role;
grant all on table public.dank_cinema_lists to service_role;
grant all on table public.dank_cinema_list_items to service_role;

comment on table public.dank_cinema_watch_sessions is
    'Service-role-only one-row-per-user Theater viewing session used for history, rewatch counts, and Cinema statistics.';

comment on table public.dank_cinema_lists is
    'Service-role-only custom personal Dank Cinema lists.';

comment on table public.dank_cinema_list_items is
    'Service-role-only ordered items belonging to custom Dank Cinema lists.';


-- NEXT CANONICAL DEPENDENCY


-- SOURCE: supabase/migrations/20261006051500_dank_cinema_standalone_playback.sql
-- Keep Open Dank Cinema playback distinct from Discord Private Sessions and Watch Parties.
-- This migration is intentionally additive so a deployment that already applied the
-- Library Intelligence migration can safely accept the new website-only mode.

do $migration$
begin
    if to_regclass('public.dank_cinema_watch_sessions') is null then
        raise exception 'dank_cinema_watch_sessions must exist before standalone playback migration';
    end if;

    alter table public.dank_cinema_watch_sessions
        drop constraint if exists dank_cinema_watch_sessions_mode_check;

    alter table public.dank_cinema_watch_sessions
        add constraint dank_cinema_watch_sessions_mode_check
        check (session_mode in ('private', 'watch_party', 'standalone'));
end;
$migration$;

comment on column public.dank_cinema_watch_sessions.session_mode is
    'Playback surface: private, watch_party, or standalone Open Dank Cinema.';

-- Canonical originals grant ALL to service_role; reduce to actual app CRUD
-- surface and exclude table TRUNCATE, TRIGGER, REFERENCES etc.
revoke all on public.dank_cinema_users from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_users to service_role;
revoke all on public.dank_cinema_user_media from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_user_media to service_role;
revoke all on public.dank_cinema_feed_discoveries from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_feed_discoveries to service_role;
revoke all on public.dank_cinema_notifications from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_notifications to service_role;
revoke all on public.dank_cinema_feed_rules from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_feed_rules to service_role;
revoke all on public.dank_cinema_user_feed_discoveries from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_user_feed_discoveries to service_role;
revoke all on public.dank_cinema_feed_source_health from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_feed_source_health to service_role;
revoke all on public.dank_cinema_watch_sessions from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_watch_sessions to service_role;
revoke all on public.dank_cinema_lists from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_lists to service_role;
revoke all on public.dank_cinema_list_items from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.dank_cinema_list_items to service_role;
