-- Opt-in hourly refresh for bot-authored Embed Studio Smart Text.
-- No arbitrary expression evaluation, auto-pings or unattended new posts.
create table if not exists public.dank_embed_studio_live_posts (
    guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
    channel_id text not null check (channel_id ~ '^[0-9]{1,20}$'),
    message_id text not null check (message_id ~ '^[0-9]{1,20}$'),
    created_by text not null check (created_by ~ '^[0-9]{1,20}$'),
    nonce text not null check (nonce ~ '^[a-f0-9]{32}$'),
    project jsonb not null check (jsonb_typeof(project)='object'
                                 and pg_column_size(project) <= 45000),
    enabled boolean not null default true,
    next_refresh_at timestamptz not null default (now()+interval '1 hour'),
    last_claimed_at timestamptz,
    last_hash text check (last_hash is null or last_hash ~ '^[0-9a-f]{64}$'),
    last_status text,
    updated_at timestamptz not null default now(),
    primary key (guild_id,channel_id,message_id)
);
create index if not exists dank_studio_live_due_idx
    on public.dank_embed_studio_live_posts(next_refresh_at,guild_id)
    where enabled=true;
alter table public.dank_embed_studio_live_posts enable row level security;
revoke all on public.dank_embed_studio_live_posts from public,anon,authenticated;
grant select,insert,update on public.dank_embed_studio_live_posts to service_role;

create or replace function public.dank_embed_studio_claim_live(
    p_limit integer default 4,
    p_shard_count integer default 1,
    p_owned_shards integer[] default array[0]
) returns setof public.dank_embed_studio_live_posts
language plpgsql security invoker set search_path=public,pg_temp
as $$
begin
    if p_limit < 1 or p_limit > 4
       or p_shard_count < 1 or p_shard_count > 4096
       or p_owned_shards is null or cardinality(p_owned_shards) < 1
       or cardinality(p_owned_shards) > p_shard_count
       or exists(select 1 from unnest(p_owned_shards) shard
           where shard is null or shard < 0 or shard >= p_shard_count) then
        raise exception 'Invalid Studio live refresh shard ownership';
    end if;
    return query
    with due as (
        select live.guild_id,live.channel_id,live.message_id
        from public.dank_embed_studio_live_posts live
        where live.enabled=true and live.next_refresh_at<=now()
          and (floor(live.guild_id::numeric / 4194304)::bigint % p_shard_count)
              = any(p_owned_shards)
        order by live.next_refresh_at,live.guild_id
        for update skip locked
        limit p_limit
    )
    update public.dank_embed_studio_live_posts live
        set next_refresh_at=now()+interval '1 hour',
            last_claimed_at=now(), updated_at=now()
    from due where live.guild_id=due.guild_id
              and live.channel_id=due.channel_id
              and live.message_id=due.message_id
    returning live.*;
end;
$$;
revoke all on function public.dank_embed_studio_claim_live(integer,integer,integer[])
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_claim_live(integer,integer,integer[])
    to service_role;
comment on table public.dank_embed_studio_live_posts is
    'Opt-in, shard-owned, hourly updates of approved bot-authored Smart Text. Never sends a new Discord message.';
