-- Bounded, opt-in recurring Embed Studio campaigns.
-- Recurrence is local civil time in an explicit IANA time zone.
-- A claim advances its occurrence before any Discord send to avoid duplicates.

create table if not exists public.dank_embed_studio_campaigns (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
    channel_id text not null check (channel_id ~ '^[0-9]{1,20}$'),
    scheduled_by text not null check (scheduled_by ~ '^[0-9]{1,20}$'),
    project jsonb not null check(jsonb_typeof(project)='object'
        and pg_column_size(project)<=45000),
    frequency text not null check(frequency in ('daily','weekly')),
    timezone text not null check(char_length(timezone) between 1 and 90),
    local_clock time without time zone not null,
    weekday smallint check(weekday between 1 and 7),
    next_fire_at timestamptz not null,
    enabled boolean not null default true,
    max_occurrences integer not null check(max_occurrences between 1 and 365),
    claimed_count integer not null default 0 check(claimed_count>=0),
    last_claim_id uuid,
    last_claimed_at timestamptz,
    last_message_id text,
    last_status text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check(frequency='daily' or weekday is not null),
    check(claimed_count<=max_occurrences)
);
create index if not exists dank_studio_campaign_due_idx
    on public.dank_embed_studio_campaigns(next_fire_at,id) where enabled=true;
create index if not exists dank_studio_campaign_guild_idx
    on public.dank_embed_studio_campaigns(guild_id,enabled,next_fire_at);

alter table public.dank_embed_studio_campaigns enable row level security;
revoke all on public.dank_embed_studio_campaigns from public,anon,authenticated;
grant select,insert,update on public.dank_embed_studio_campaigns to service_role;

create or replace function public.dank_embed_studio_next_campaign_fire(
    p_zone text,p_frequency text,p_weekday integer,p_clock time without time zone,
    p_after timestamptz
) returns timestamptz
language plpgsql stable security invoker set search_path=public,pg_temp
as $$
declare
    anchor_date date;
    candidate_local timestamp without time zone;
    candidate timestamptz;
    offset_days integer;
begin
    if p_zone is null or length(p_zone)>90 or p_clock is null
        or p_after is null or p_frequency not in ('daily','weekly')
        or (p_frequency='weekly' and (p_weekday is null or p_weekday not between 1 and 7)) then
        raise exception 'Invalid recurring campaign schedule';
    end if;
    anchor_date := (p_after at time zone p_zone)::date;
    for offset_days in 0..8 loop
        if p_frequency='daily' or
            extract(isodow from (anchor_date+offset_days))::integer=p_weekday then
            candidate_local := (anchor_date+offset_days)+p_clock;
            candidate := candidate_local at time zone p_zone;
            -- Skip nonexistent local wall-clock times during DST spring gaps.
            if candidate>p_after
                and (candidate at time zone p_zone)=candidate_local then
                return candidate;
            end if;
        end if;
    end loop;
    raise exception 'No valid next campaign occurrence in bounded horizon';
end;
$$;
revoke all on function public.dank_embed_studio_next_campaign_fire(text,text,integer,time without time zone,timestamptz)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_next_campaign_fire(text,text,integer,time without time zone,timestamptz)
    to service_role;

create or replace function public.dank_embed_studio_claim_campaigns(
    p_limit integer default 4,p_shard_count integer default 1,
    p_owned_shards integer[] default array[0]
) returns setof public.dank_embed_studio_campaigns
language plpgsql security invoker set search_path=public,pg_temp
as $$
begin
    if p_limit<1 or p_limit>4 or p_shard_count<1 or p_shard_count>4096
        or p_owned_shards is null or cardinality(p_owned_shards)<1
        or cardinality(p_owned_shards)>p_shard_count
        or exists(select 1 from unnest(p_owned_shards) shard
          where shard is null or shard<0 or shard>=p_shard_count) then
        raise exception 'Invalid campaign shard claim';
    end if;

    -- Late occurrences are consumed without sending; jump to the next
    -- future local civil time. Bound cleanups to prevent fleet-wide bursts.
    with old as (
        select id from public.dank_embed_studio_campaigns
        where enabled and next_fire_at<now()-interval '5 minutes'
        order by next_fire_at,id for update skip locked limit 64
    )
    update public.dank_embed_studio_campaigns c
        set next_fire_at=public.dank_embed_studio_next_campaign_fire(
                c.timezone,c.frequency,c.weekday,c.local_clock,now()),
            last_status='missed',updated_at=now()
    from old where c.id=old.id;

    return query
    with due as (
        select c.id from public.dank_embed_studio_campaigns c
        where c.enabled and c.next_fire_at<=now()
          and c.next_fire_at>=now()-interval '5 minutes'
          and (floor(c.guild_id::numeric/4194304)::bigint % p_shard_count)
              = any(p_owned_shards)
        order by c.next_fire_at,c.id for update skip locked limit p_limit
    )
    update public.dank_embed_studio_campaigns c
        set next_fire_at=public.dank_embed_studio_next_campaign_fire(
                c.timezone,c.frequency,c.weekday,c.local_clock,c.next_fire_at),
            claimed_count=c.claimed_count+1,
            enabled=(c.claimed_count+1<c.max_occurrences),
            last_claim_id=gen_random_uuid(),last_claimed_at=now(),
            last_status='claimed',updated_at=now()
    from due where c.id=due.id
    returning c.*;
end;
$$;
revoke all on function public.dank_embed_studio_claim_campaigns(integer,integer,integer[])
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_claim_campaigns(integer,integer,integer[])
    to service_role;

comment on table public.dank_embed_studio_campaigns is
    'Service-role-only daily/weekly announcements: atomic shard-owned at-most-once claims and bounded occurrence counts.';
