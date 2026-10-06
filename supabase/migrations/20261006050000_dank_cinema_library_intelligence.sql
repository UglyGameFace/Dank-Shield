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
