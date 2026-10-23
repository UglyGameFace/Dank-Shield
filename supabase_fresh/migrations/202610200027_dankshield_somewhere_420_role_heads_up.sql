-- Gentle 4:20 preparation heads-up. Separate from public announcements.
-- Guild owner enables once; all holders of the selected @Stoners role are
-- eligible by default, but individual members may opt out immediately.
alter table public.dank_somewhere_420_schedules
  add column if not exists role_reminder_enabled boolean not null default false,
  add column if not exists role_reminder_role_id text,
  add column if not exists role_reminder_next_at timestamptz,
  add column if not exists role_reminder_last_date date;
create index if not exists idx_dank_420_role_due
  on public.dank_somewhere_420_schedules(role_reminder_next_at)
  where role_reminder_enabled = true;

create table if not exists public.dank_somewhere_420_role_optouts (
  guild_id text not null,
  user_id text not null,
  opted_out boolean not null default true,
  updated_at timestamptz not null default now(),
  primary key(guild_id,user_id)
);
create table if not exists public.dank_somewhere_420_role_delivery (
  user_id text not null,
  local_date date not null,
  claimed_at timestamptz not null default now(),
  primary key(user_id,local_date)
);
create index if not exists idx_dank_420_role_delivery_age
  on public.dank_somewhere_420_role_delivery(claimed_at);
alter table public.dank_somewhere_420_role_optouts enable row level security;
alter table public.dank_somewhere_420_role_delivery enable row level security;
revoke all on public.dank_somewhere_420_role_optouts from public,anon,authenticated;
revoke all on public.dank_somewhere_420_role_delivery from public,anon,authenticated;
grant select,insert,update on public.dank_somewhere_420_role_optouts to service_role;
grant select,insert on public.dank_somewhere_420_role_delivery to service_role;

-- Reserve guild waves atomically. Like the regular scheduler, old missed waves
-- are consumed, never replayed hours late, and DST is resolved per local day.
create or replace function public.dank_420_claim_role_waves(p_limit integer default 8)
returns setof public.dank_somewhere_420_schedules
language plpgsql security invoker set search_path = public
as $$
begin
  if p_limit not between 1 and 16 then
    raise exception 'invalid role wave batch size';
  end if;
  update public.dank_somewhere_420_schedules s
     set role_reminder_last_date = (s.role_reminder_next_at at time zone s.timezone)::date,
         role_reminder_next_at = (((s.role_reminder_next_at at time zone s.timezone)::date
                                    + 1 + time '16:15') at time zone s.timezone),
         updated_at=now()
   where s.kind='guild' and s.role_reminder_enabled
     and s.role_reminder_next_at < now() - interval '3 minutes';
  return query
  with due as (
     select s.id from public.dank_somewhere_420_schedules s
      where s.kind='guild' and s.role_reminder_enabled
        and s.role_reminder_next_at between now()-interval '3 minutes' and now()
        and s.role_reminder_last_date is distinct from
            (s.role_reminder_next_at at time zone s.timezone)::date
      order by s.role_reminder_next_at,s.id
      for update skip locked
      limit p_limit
  )
  update public.dank_somewhere_420_schedules s
     set role_reminder_last_date=(s.role_reminder_next_at at time zone s.timezone)::date,
         role_reminder_next_at=(((s.role_reminder_next_at at time zone s.timezone)::date
                                    + 1 + time '16:15') at time zone s.timezone),
         updated_at=now()
    from due where s.id=due.id
  returning s.*;
end;
$$;
revoke all on function public.dank_420_claim_role_waves(integer) from public,anon,authenticated;
grant execute on function public.dank_420_claim_role_waves(integer) to service_role;

-- One global DM per member per local day even across multiple guilds.
-- User opt-outs are checked inside the atomic claim; no retry on failures.
create or replace function public.dank_420_claim_role_member(
   p_guild text, p_user text, p_local_date date)
returns boolean language plpgsql security invoker set search_path=public
as $$
declare claimed boolean;
begin
  if p_guild !~ '^[0-9]{5,22}$' or p_user !~ '^[0-9]{5,22}$'
     or p_local_date is null then
    return false;
  end if;
  insert into public.dank_somewhere_420_role_delivery(user_id,local_date)
  select p_user,p_local_date
   where not exists (
      select 1 from public.dank_somewhere_420_role_optouts
       where guild_id=p_guild and user_id=p_user and opted_out
   )
  on conflict do nothing
  returning true into claimed;
  return coalesce(claimed,false);
end;
$$;
revoke all on function public.dank_420_claim_role_member(text,text,date) from public,anon,authenticated;
grant execute on function public.dank_420_claim_role_member(text,text,date) to service_role;

comment on table public.dank_somewhere_420_role_optouts is
  'Per-server explicit Stop Reminders setting. Role holders otherwise eligible after staff enables one quiet heads-up.';
