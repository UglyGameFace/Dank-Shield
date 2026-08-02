-- ============================================================
-- 202608020001_durable_invite_stats.sql
-- ------------------------------------------------------------
-- Durable, atomic, and replay-safe Invite Shield statistics.
--
-- One deleted Discord message maps to one event_hash.  The event row and the
-- guild total are committed in the same transaction so create/edit/fallback
-- listeners and process restarts cannot double-count the same deletion.
-- ============================================================

create table if not exists public.dank_invite_block_stats (
    guild_id text primary key,
    invites_blocked bigint not null default 0 check (invites_blocked >= 0),
    updated_at timestamptz not null default now()
);

create table if not exists public.dank_invite_block_events (
    event_hash text primary key check (length(event_hash) = 64),
    guild_id text not null,
    blocked_count integer not null check (blocked_count > 0),
    source text,
    created_at timestamptz not null default now()
);

create index if not exists idx_dank_invite_block_events_guild_created
    on public.dank_invite_block_events (guild_id, created_at desc);

alter table public.dank_invite_block_stats enable row level security;
alter table public.dank_invite_block_events enable row level security;

revoke all on table public.dank_invite_block_stats from anon, authenticated;
revoke all on table public.dank_invite_block_events from anon, authenticated;
grant all on table public.dank_invite_block_stats to service_role;
grant all on table public.dank_invite_block_events to service_role;

create or replace function public.record_dank_invite_block_event(
    p_event_hash text,
    p_guild_id text,
    p_blocked_count integer,
    p_seed_count bigint default 0,
    p_source text default null
)
returns table (
    applied boolean,
    invites_blocked bigint
)
language plpgsql
security definer
set search_path = public
as $$
declare
    inserted_event text;
    current_count bigint;
begin
    if nullif(btrim(p_event_hash), '') is null or length(p_event_hash) <> 64 then
        raise exception 'event hash must be a 64-character SHA-256 value';
    end if;
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;
    if coalesce(p_blocked_count, 0) <= 0 then
        raise exception 'blocked count must be positive';
    end if;

    insert into public.dank_invite_block_stats (
        guild_id,
        invites_blocked,
        updated_at
    )
    values (
        btrim(p_guild_id),
        greatest(0, coalesce(p_seed_count, 0)),
        now()
    )
    on conflict (guild_id) do update
    set invites_blocked = greatest(
            public.dank_invite_block_stats.invites_blocked,
            excluded.invites_blocked
        ),
        updated_at = case
            when excluded.invites_blocked > public.dank_invite_block_stats.invites_blocked
                then now()
            else public.dank_invite_block_stats.updated_at
        end;

    insert into public.dank_invite_block_events (
        event_hash,
        guild_id,
        blocked_count,
        source
    )
    values (
        lower(btrim(p_event_hash)),
        btrim(p_guild_id),
        p_blocked_count,
        nullif(left(coalesce(p_source, ''), 180), '')
    )
    on conflict (event_hash) do nothing
    returning event_hash into inserted_event;

    if inserted_event is not null then
        update public.dank_invite_block_stats as stats
        set invites_blocked = stats.invites_blocked + p_blocked_count,
            updated_at = now()
        where stats.guild_id = btrim(p_guild_id)
        returning stats.invites_blocked
        into current_count;

        return query select true, current_count;
        return;
    end if;

    select stats.invites_blocked
    into current_count
    from public.dank_invite_block_stats as stats
    where stats.guild_id = btrim(p_guild_id);

    return query select false, coalesce(current_count, 0);
end;
$$;

revoke all on function public.record_dank_invite_block_event(
    text,
    text,
    integer,
    bigint,
    text
) from public, anon, authenticated;

grant execute on function public.record_dank_invite_block_event(
    text,
    text,
    integer,
    bigint,
    text
) to service_role;

comment on table public.dank_invite_block_stats is
    'Per-guild durable Invite Shield totals. Service-role only.';
comment on table public.dank_invite_block_events is
    'Replay-safe event ledger for deleted invite messages. Service-role only.';
comment on function public.record_dank_invite_block_event is
    'Atomically deduplicates one deleted message and increments by its actual blocked invite-code count.';
