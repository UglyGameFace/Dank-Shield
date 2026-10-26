-- Private, opt-in LFG Atlas publication queue. Never visible to anonymous clients.
-- Explicit session host/co-host and guild consent are checked in the enqueue RPC.
create table if not exists public.dank_community_atlas_publications (
  session_id uuid primary key,
  guild_id text not null,
  actor_id text not null,
  game text not null,
  title text not null,
  region text not null,
  contact text not null,
  expires_at timestamptz not null,
  state text not null default 'queued'
    check (state in ('queued','publishing','posted','removing','removed','unknown','failed')),
  remote_id text,
  remote_url text,
  edit_token text,
  lease_until timestamptz,
  claim_version integer not null default 0,
  delete_attempts integer not null default 0,
  revoke_requested boolean not null default false,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint atlas_publication_token_size check (edit_token is null or length(edit_token) between 10 and 512),
  constraint atlas_publication_contact check (
    contact ~ '^https://(discord[.]gg/|discord[.]com/invite/)[A-Za-z0-9-]{2,64}$'
  )
);
create index if not exists dank_atlas_publications_work
  on public.dank_community_atlas_publications (updated_at)
  where state in ('queued','publishing','posted','removing');
alter table public.dank_community_atlas_publications enable row level security;
revoke all on public.dank_community_atlas_publications from public, anon, authenticated;
grant select, insert, update on public.dank_community_atlas_publications to service_role;

create or replace function public.dank_queue_atlas_publication(
  p_session uuid, p_guild_id text, p_actor_id text,
  p_region text, p_hours integer default 2
)
returns setof public.dank_community_atlas_publications
language plpgsql security definer set search_path=''
as $$
begin
  if p_guild_id !~ '^[0-9]{15,20}$'
     or p_actor_id !~ '^[0-9]{15,20}$'
     or p_region !~ '^[A-Za-z0-9 +_-]{2,40}$'
     or p_hours not in (1,2,4,12,24) then
    return;
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_guild_id, 0));
  if (
    select count(*) from public.dank_community_atlas_publications
    where guild_id=p_guild_id and state in ('queued','publishing','posted','removing')
      and expires_at > now()
  ) >= 8 then
    return;
  end if;

  return query
  insert into public.dank_community_atlas_publications as prior (
    session_id, guild_id, actor_id, game, title, region, contact, expires_at
  )
  select s.id, s.guild_id, p_actor_id,
    left(s.game_name, 80),
    left('Looking for players: ' ||
      pg_catalog.regexp_replace(s.game_name, '[^a-zA-Z0-9 .+_-]', '', 'g'), 80),
    p_region,
    h.public_lfg_invite_url,
    least(
      now() + p_hours * interval '1 hour',
      s.created_at + h.max_session_lifetime_seconds * interval '1 second'
    )
  from public.dank_community_sessions s
  join public.dank_community_hub_settings h on h.guild_id=s.guild_id
  join public.dank_community_session_members m
    on m.session_id=s.id and m.guild_id=s.guild_id
    and m.user_id=p_actor_id and m.role in ('host','cohost')
    and m.left_at is null
  where s.id=p_session and s.guild_id=p_guild_id
    and s.public_lfg_opt_in is true and s.privacy='public'
    and s.state in ('open','forming','ready','active')
    and s.created_at + h.max_session_lifetime_seconds * interval '1 second' > now()
    and s.last_activity_at + h.idle_timeout_seconds * interval '1 second' > now()
    and h.enabled is true and h.maintenance_mode is false
    and h.public_lfg_enabled is true
    and h.public_lfg_display_name is not null
    and h.public_lfg_invite_url ~ '^https://(discord[.]gg/|discord[.]com/invite/)[A-Za-z0-9-]{2,64}$'
    and length(btrim(s.game_name)) between 2 and 80
  on conflict (session_id) do update
    set guild_id=excluded.guild_id, actor_id=excluded.actor_id,
        game=excluded.game, title=excluded.title, region=excluded.region,
        contact=excluded.contact, expires_at=excluded.expires_at,
        state='queued', remote_id=null, remote_url=null, edit_token=null,
        lease_until=null, delete_attempts=0, revoke_requested=false, last_error=null,
        claim_version=prior.claim_version+1,
        updated_at=now()
    where prior.state in ('removed','failed') and prior.edit_token is null
  returning *;
end;
$$;

create or replace function public.dank_claim_atlas_publications(p_limit integer default 2)
returns setof public.dank_community_atlas_publications
language sql security definer set search_path=''
as $$
  with due as (
    select p.session_id,
      case when p.state='queued' then 'publishing' else 'removing' end as next_state
    from public.dank_community_atlas_publications p
    left join public.dank_community_sessions s on s.id=p.session_id and s.guild_id=p.guild_id
    left join public.dank_community_hub_settings h on h.guild_id=p.guild_id
    where (
      p.state='queued'
      or (
        p.state in ('posted','removing')
        and p.edit_token is not null
        and (p.state='removing' or p.revoke_requested is true or p.expires_at <= now()
          or s.id is null or s.public_lfg_opt_in is distinct from true
          or s.privacy is distinct from 'public'
          or s.state not in ('open','forming','ready','active')
          or h.enabled is distinct from true
          or h.maintenance_mode is distinct from false
          or h.public_lfg_enabled is distinct from true
          or h.public_lfg_invite_url is distinct from p.contact
          or s.last_activity_at + h.idle_timeout_seconds * interval '1 second' <= now()
          or s.created_at + h.max_session_lifetime_seconds * interval '1 second' <= now()
        )
      )
    )
      and (p.lease_until is null or p.lease_until <= now())
      and (p.state='queued' or p.delete_attempts < 8)
    order by p.updated_at, p.session_id
    limit least(4,greatest(1,coalesce(p_limit,2)))
    for update of p skip locked
  )
  update public.dank_community_atlas_publications as p
    set state=due.next_state,
        lease_until=now() + interval '3 minutes',
        claim_version=p.claim_version+1, updated_at=now()
  from due where p.session_id=due.session_id
  returning p.*;
$$;
revoke all on function public.dank_queue_atlas_publication(uuid,text,text,text,integer)
  from public, anon, authenticated;
grant execute on function public.dank_queue_atlas_publication(uuid,text,text,text,integer)
  to service_role;
revoke all on function public.dank_claim_atlas_publications(integer)
  from public, anon, authenticated;
grant execute on function public.dank_claim_atlas_publications(integer)
  to service_role;
