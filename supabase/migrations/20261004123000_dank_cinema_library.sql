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

create table if not exists public.dank_cinema_notifications (
    id uuid primary key default gen_random_uuid(),
    user_id bigint not null,
    guild_id bigint,
    kind text not null default 'info',
    title text not null,
    body text not null default '',
    action jsonb not null default '{}'::jsonb,
    read_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists idx_dank_cinema_notifications_user
    on public.dank_cinema_notifications (user_id, created_at desc);

alter table public.dank_cinema_users enable row level security;
alter table public.dank_cinema_user_media enable row level security;
alter table public.dank_cinema_notifications enable row level security;

revoke all on table public.dank_cinema_users from anon, authenticated;
revoke all on table public.dank_cinema_user_media from anon, authenticated;
revoke all on table public.dank_cinema_notifications from anon, authenticated;

grant all on table public.dank_cinema_users to service_role;
grant all on table public.dank_cinema_user_media to service_role;
grant all on table public.dank_cinema_notifications to service_role;

comment on table public.dank_cinema_users is
    'Service-role-only Dank Cinema user preferences keyed by Discord user id.';
comment on table public.dank_cinema_user_media is
    'Service-role-only Dank Cinema watchlist, history, movie progress, and episode progress.';
comment on table public.dank_cinema_notifications is
    'Service-role-only Dank Cinema notification inbox for Discord-linked users.';
