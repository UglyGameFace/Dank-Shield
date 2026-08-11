-- DS-STICKY-026: persistent StickyBot-style Community Tools.
-- Service-role only: Discord users never access these tables directly.

create table if not exists public.dank_stickies (
    channel_id bigint primary key,
    guild_id bigint not null,
    enabled boolean not null default true,
    content text not null default '',
    mode text not null default 'plain'
        check (mode in ('plain', 'embed', 'poll')),
    title text,
    color integer not null default 5793266
        check (color between 0 and 16777215),
    image_url text,
    thumbnail_url text,
    interval_seconds integer not null default 15
        check (interval_seconds between 15 and 3600),
    message_threshold integer not null default 5
        check (message_threshold between 1 and 100),
    use_webhook boolean not null default false,
    sender_name text,
    sender_avatar_url text,
    last_message_id bigint,
    last_sent_at timestamptz,
    updated_by bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (char_length(content) <= 1900),
    check (title is null or char_length(title) <= 256),
    check (sender_name is null or char_length(sender_name) <= 80),
    check (mode <> 'poll' or use_webhook = false)
);

create index if not exists dank_stickies_guild_id_idx
    on public.dank_stickies (guild_id);

create table if not exists public.dank_sticky_polls (
    channel_id bigint primary key
        references public.dank_stickies(channel_id) on delete cascade,
    guild_id bigint not null,
    question text not null
        check (char_length(question) between 1 and 300),
    options jsonb not null default '[]'::jsonb,
    votes jsonb not null default '{}'::jsonb,
    state text not null default 'active'
        check (state in ('active', 'paused', 'ended')),
    updated_by bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (jsonb_typeof(options) = 'array'),
    check (jsonb_array_length(options) between 2 and 7),
    check (jsonb_typeof(votes) = 'object')
);

create index if not exists dank_sticky_polls_guild_id_idx
    on public.dank_sticky_polls (guild_id);

alter table public.dank_stickies enable row level security;
alter table public.dank_sticky_polls enable row level security;

revoke all on table public.dank_stickies from anon, authenticated;
revoke all on table public.dank_sticky_polls from anon, authenticated;

grant all on table public.dank_stickies to service_role;
grant all on table public.dank_sticky_polls to service_role;

comment on table public.dank_stickies is
    'Dank Shield persistent channel sticky configuration; raw webhook URLs/tokens are intentionally not stored.';
comment on table public.dank_sticky_polls is
    'Dank Shield sticky-poll state and one-choice-per-user vote map.';
