-- Offline dedicated Supabase ONLY; no old invite-event history imported.
-- Runtime contract: stoney_verify/durable_invite_stats.py and canonical
-- supabase/migrations/20260802225500_durable_invite_stats.sql.
create table public.dank_invite_block_stats (
  guild_id text primary key check (btrim(guild_id) <> ''),
  invites_blocked bigint not null default 0 check (invites_blocked >= 0),
  updated_at timestamptz not null default now()
);
create table public.dank_invite_block_events (
  event_hash text primary key check (event_hash ~ '^[0-9a-f]{64}$'),
  guild_id text not null check (btrim(guild_id) <> ''),
  blocked_count integer not null check (blocked_count > 0),
  source text,
  created_at timestamptz not null default now()
);
create index dank_invite_block_events_guild_created_idx
  on public.dank_invite_block_events (guild_id, created_at desc);
alter table public.dank_invite_block_stats enable row level security;
alter table public.dank_invite_block_events enable row level security;
revoke all on public.dank_invite_block_stats from public, anon, authenticated;
revoke all on public.dank_invite_block_events from public, anon, authenticated;
grant select,insert,update,delete on public.dank_invite_block_stats to service_role;
grant select,insert,update,delete on public.dank_invite_block_events to service_role;

-- Only seed the monotonic totals already present in migrated config snapshots.
-- Do not materialize individual old events or historical event hashes.
with config_seeds as (
  select g.guild_id, coalesce((
    select max(v.value::bigint)
    from jsonb_array_elements_text(jsonb_build_array(
      g.meta #>> '{security_stats_counts,invites_blocked}',
      g.metadata #>> '{security_stats_counts,invites_blocked}',
      g.config #>> '{security_stats_counts,invites_blocked}',
      g.settings #>> '{security_stats_counts,invites_blocked}',
      g.meta ->> 'invites_blocked', g.metadata ->> 'invites_blocked',
      g.config ->> 'invites_blocked', g.settings ->> 'invites_blocked'
    )) as v(value) where v.value ~ '^[0-9]{1,18}$'
  ),0) as legacy_count
  from public.guild_configs as g
)
insert into public.dank_invite_block_stats(guild_id,invites_blocked)
select guild_id,legacy_count from config_seeds where legacy_count > 0
on conflict(guild_id) do update set
 invites_blocked = greatest(public.dank_invite_block_stats.invites_blocked,excluded.invites_blocked),
 updated_at = case
   when excluded.invites_blocked > public.dank_invite_block_stats.invites_blocked
   then now() else public.dank_invite_block_stats.updated_at end;

create or replace function public.record_dank_invite_block_event(
 p_event_hash text,p_guild_id text,p_blocked_count integer,
 p_seed_count bigint default 0,p_source text default null
) returns table(applied boolean, invites_blocked bigint)
language plpgsql security invoker
set search_path = pg_catalog, public, pg_temp
as $rpc$
declare
 inserted_hash text;
 current_count bigint;
 cleaned_hash text := lower(btrim(p_event_hash));
 cleaned_guild text := btrim(p_guild_id);
begin
 if cleaned_hash is null or cleaned_hash !~ '^[0-9a-f]{64}$' then
  raise exception 'event hash must be 64 lowercase hex characters';
 end if;
 if cleaned_guild is null or cleaned_guild = '' then
  raise exception 'guild ID required';
 end if;
 if p_blocked_count is null or p_blocked_count <= 0 then
  raise exception 'blocked count must be positive';
 end if;
 insert into public.dank_invite_block_stats(guild_id,invites_blocked,updated_at)
 values(cleaned_guild,greatest(0,coalesce(p_seed_count,0)),now())
 on conflict(guild_id) do update set
  invites_blocked = greatest(
   public.dank_invite_block_stats.invites_blocked,excluded.invites_blocked),
  updated_at = case
   when excluded.invites_blocked > public.dank_invite_block_stats.invites_blocked
   then now() else public.dank_invite_block_stats.updated_at end;

 insert into public.dank_invite_block_events(event_hash,guild_id,blocked_count,source)
 values(cleaned_hash,cleaned_guild,p_blocked_count,nullif(left(coalesce(p_source,''),180),''))
 on conflict(event_hash) do nothing returning event_hash into inserted_hash;
 if inserted_hash is not null then
  update public.dank_invite_block_stats as s
  set invites_blocked=s.invites_blocked+p_blocked_count,updated_at=now()
  where s.guild_id=cleaned_guild
  returning s.invites_blocked into current_count;
  return query select true,current_count;
  return;
 end if;
 select s.invites_blocked into current_count
 from public.dank_invite_block_stats as s where s.guild_id=cleaned_guild;
 return query select false,coalesce(current_count,0);
end $rpc$;
revoke all on function public.record_dank_invite_block_event(text,text,integer,bigint,text)
 from public,anon,authenticated;
grant execute on function public.record_dank_invite_block_event(text,text,integer,bigint,text)
 to service_role;
comment on table public.dank_invite_block_events is
 'Minimal event hashes for future replay safety; no historical blocked-message data imported.';
comment on table public.dank_invite_block_stats is
 'Per-guild monotonic totals seeded from migrated configs; source write catch-up still required.';
