-- Safe, restartable, staff-authorized preview suppression.
-- Unlike the separate 24h advert retention ledger this table NEVER deletes posts.
create table if not exists public.dank_trusted_advert_collapse_jobs (
  guild_id text not null,
  channel_id text not null,
  bot_id text not null,
  requested_by text not null,
  status text not null default 'running'
    check (status in ('running','completed','cancelled','failed')),
  cursor_before text,
  manual_only boolean not null default false,
  scanned bigint not null default 0,
  collapsed bigint not null default 0,
  skipped bigint not null default 0,
  claim_version integer not null default 0,
  lease_until timestamptz,
  failures integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (guild_id, channel_id, bot_id)
);
create index if not exists dank_trusted_embed_jobs_due
  on public.dank_trusted_advert_collapse_jobs (updated_at)
  where status = 'running';
alter table public.dank_trusted_advert_collapse_jobs enable row level security;
revoke all on public.dank_trusted_advert_collapse_jobs from public, anon, authenticated;
grant select, insert, update on public.dank_trusted_advert_collapse_jobs to service_role;

-- Start is atomic; a second click cannot reset or steal a running cursor.
create or replace function public.dank_start_trusted_embed_job(
  p_guild_id text, p_channel_id text, p_bot_id text,
  p_requested_by text, p_cursor_before text default null,
  p_manual_only boolean default false
)
returns setof public.dank_trusted_advert_collapse_jobs
language sql security definer set search_path=''
as $$
  insert into public.dank_trusted_advert_collapse_jobs as job (
    guild_id, channel_id, bot_id, requested_by, cursor_before, manual_only
  )
  select p_guild_id, p_channel_id, p_bot_id, p_requested_by,
         p_cursor_before, coalesce(p_manual_only, false)
  where p_guild_id ~ '^[0-9]{15,20}$'
    and p_channel_id ~ '^[0-9]{15,20}$'
    and p_bot_id ~ '^[0-9]{15,20}$'
    and p_requested_by ~ '^[0-9]{15,20}$'
    and (p_cursor_before is null or p_cursor_before ~ '^[0-9]{15,21}$')
  on conflict (guild_id, channel_id, bot_id) do update
    set requested_by=excluded.requested_by,
        cursor_before=excluded.cursor_before, manual_only=excluded.manual_only,
        status='running', scanned=0, collapsed=0, skipped=0,
        claim_version=job.claim_version+1, lease_until=null,
        failures=0, last_error=null, created_at=now(), updated_at=now()
    where job.status in ('completed','cancelled','failed')
  returning *;
$$;

-- One process at a time; crashed/expired leases are recoverable on restart.
create or replace function public.dank_claim_trusted_embed_jobs(
  p_limit integer default 1
)
returns setof public.dank_trusted_advert_collapse_jobs
language sql security definer set search_path=''
as $$
  with due as (
    select guild_id, channel_id, bot_id
    from public.dank_trusted_advert_collapse_jobs
    where status='running'
      and (lease_until is null or lease_until <= now())
    order by updated_at, guild_id, channel_id
    limit least(2, greatest(1, coalesce(p_limit, 1)))
    for update skip locked
  )
  update public.dank_trusted_advert_collapse_jobs as job
    set lease_until=now()+interval '3 minutes',
        claim_version=job.claim_version+1, updated_at=now()
  from due
  where job.guild_id=due.guild_id
    and job.channel_id=due.channel_id and job.bot_id=due.bot_id
  returning job.*;
$$;

revoke all on function public.dank_start_trusted_embed_job(text,text,text,text,text,boolean)
  from public, anon, authenticated;
grant execute on function public.dank_start_trusted_embed_job(text,text,text,text,text,boolean)
  to service_role;
revoke all on function public.dank_claim_trusted_embed_jobs(integer)
  from public, anon, authenticated;
grant execute on function public.dank_claim_trusted_embed_jobs(integer)
  to service_role;
