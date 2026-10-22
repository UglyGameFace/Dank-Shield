-- It's 4:20 Somewhere v2. Service-role-only scheduled announcements and personal reminders.
-- Apply before enabling celebrations. Safe to rerun; never touches /toke sessions.
create table if not exists public.dank_somewhere_420_schedules (
    id text primary key,
    kind text not null check (kind in ('guild','member')),
    guild_id text,
    user_id text,
    timezone text not null default 'UTC',
    channel_id text,
    favorite_city text not null default '',
    notify_role boolean not null default false,
    quiet_start smallint not null default 22 check (quiet_start between 0 and 23),
    quiet_end smallint not null default 8 check (quiet_end between 0 and 23),
    enabled boolean not null default false,
    next_fire_at timestamptz not null,
    last_local_date date,
    last_claimed_at timestamptz,
    updated_at timestamptz not null default now(),
    constraint dank_420_identity check (
        (kind = 'guild' and id = 'g:' || guild_id and guild_id is not null and user_id is null)
        or (kind = 'member' and id = 'u:' || user_id and user_id is not null and guild_id is null)
    )
);
create index if not exists idx_dank_420_due
    on public.dank_somewhere_420_schedules(next_fire_at)
    where enabled = true;
alter table public.dank_somewhere_420_schedules enable row level security;
revoke all on public.dank_somewhere_420_schedules from public, anon, authenticated;
grant select, insert, update, delete on public.dank_somewhere_420_schedules to service_role;

-- Once-only reservation, before any Discord send. A claimed event is never
-- replayed on failure/restart; no duplicate pings after a process crash.
-- Next date is recomputed in local civil time to respect DST transitions.
create or replace function public.dank_420_claim_due(p_limit integer default 16)
returns setof public.dank_somewhere_420_schedules
language plpgsql security invoker set search_path = public
as $$
begin
    if p_limit < 1 or p_limit > 25 then
        raise exception 'Invalid 4:20 claim batch size';
    end if;

    -- Skip late messages rather than waking people hours after the event.
    -- This also heals old downtime; any skipped day is explicitly consumed.
    update public.dank_somewhere_420_schedules s
       set last_local_date = (s.next_fire_at at time zone s.timezone)::date,
           next_fire_at = (((s.next_fire_at at time zone s.timezone)::date
                            + interval '1 day' + time '16:20') at time zone s.timezone),
           updated_at = now()
     where s.enabled and s.next_fire_at < now() - interval '3 minutes';

    return query
    with eligible as (
        select s.id from public.dank_somewhere_420_schedules s
         where s.enabled
           and s.next_fire_at <= now()
           and s.next_fire_at >= now() - interval '3 minutes'
           and s.last_local_date is distinct from
               (s.next_fire_at at time zone s.timezone)::date
         order by s.next_fire_at, s.id
         for update skip locked
         limit p_limit
    )
    update public.dank_somewhere_420_schedules s
       set last_local_date = (s.next_fire_at at time zone s.timezone)::date,
           next_fire_at = (((s.next_fire_at at time zone s.timezone)::date
                            + interval '1 day' + time '16:20') at time zone s.timezone),
           last_claimed_at = now(), updated_at = now()
      from eligible e where s.id = e.id
    returning s.*;
end;
$$;
revoke all on function public.dank_420_claim_due(integer) from public, anon, authenticated;
grant execute on function public.dank_420_claim_due(integer) to service_role;

comment on table public.dank_somewhere_420_schedules is
  'Opt-in world 4:20 schedules, one globally per guild or person, with atomic at-most-once delivery claims.';
