-- Durable, opt-in Embed Studio one-shot scheduled announcements.
-- Indexed due-row claims; never iterates all guilds or sends from a SQL function.
-- No automatic migration, no schema writes on Discord startup.

create table if not exists public.dank_embed_studio_scheduled_posts (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
    channel_id text not null check (channel_id ~ '^[0-9]{1,20}$'),
    scheduled_by text not null check (scheduled_by ~ '^[0-9]{1,20}$'),
    project jsonb not null check (jsonb_typeof(project) = 'object'
        and pg_column_size(project) <= 45000),
    execute_at timestamptz not null,
    status text not null default 'pending'
        check (status in ('pending','claimed','sent','failed','missed','cancelled')),
    claimed_at timestamptz,
    sent_message_id text,
    result_code text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists dank_studio_due_idx
    on public.dank_embed_studio_scheduled_posts(execute_at,id)
    where status='pending';
create index if not exists dank_studio_schedules_guild_idx
    on public.dank_embed_studio_scheduled_posts(guild_id,status,execute_at desc);

alter table public.dank_embed_studio_scheduled_posts enable row level security;
revoke all on table public.dank_embed_studio_scheduled_posts from public,anon,authenticated;
grant select,insert,update on public.dank_embed_studio_scheduled_posts to service_role;

create or replace function public.dank_embed_studio_claim_due(p_limit integer default 8)
returns setof public.dank_embed_studio_scheduled_posts
language plpgsql security invoker set search_path=public,pg_temp
as $$
begin
    if p_limit < 1 or p_limit > 8 then
        raise exception 'Invalid Studio schedule claim batch';
    end if;
    -- Never send stale advertisements/announcements hours after an outage.
    update public.dank_embed_studio_scheduled_posts
        set status='missed',result_code='too_late',updated_at=now()
    where status='pending' and execute_at < now()-interval '10 minutes';

    return query
    with ready as (
        select p.id from public.dank_embed_studio_scheduled_posts p
        where p.status='pending' and p.execute_at <= now()
          and p.execute_at >= now()-interval '10 minutes'
        order by p.execute_at,p.id
        for update skip locked
        limit p_limit
    )
    update public.dank_embed_studio_scheduled_posts s
        set status='claimed',claimed_at=now(),updated_at=now()
    from ready where s.id=ready.id
    returning s.*;
end;
$$;
revoke all on function public.dank_embed_studio_claim_due(integer)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_claim_due(integer) to service_role;
comment on table public.dank_embed_studio_scheduled_posts is
    'Private one-shot Studio jobs with at-most-once claims; claimed events are never auto-replayed.';
