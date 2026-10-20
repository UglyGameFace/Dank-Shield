-- Offline dedicated DankShield ONLY: source-audited member_events contract.
-- Original 2026-10-20 database: 632 source records, 13 columns, UUID id PK.
-- Recreate feed trigger behavior; historical import must disable trigger
-- TRANSACTION-LOCALLY and reenable it before COMMIT to avoid old-feed replay.
create table public.member_events (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  user_id text not null,
  actor_id text,
  actor_name text,
  event_type text not null,
  title text,
  reason text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  description text,
  source text,
  meta jsonb not null default '{}'::jsonb
);
create index member_events_guild_user_created_idx
  on public.member_events(guild_id,user_id,created_at desc);
create index member_events_guild_type_created_idx
  on public.member_events(guild_id,event_type,created_at desc);
alter table public.member_events enable row level security;
revoke all on public.member_events from public,anon,authenticated,service_role;
grant select,insert,update,delete on public.member_events to service_role;

-- Source-equivalent function from the live-source pg_get_functiondef:
-- every NEW member event populates the existing activity_feed_events table.
create or replace function public.trg_member_events_to_activity_feed()
returns trigger language plpgsql
set search_path=pg_catalog,public
as $function$
declare v_family text;
begin
  v_family := case
    when lower(coalesce(new.event_type,'')) like '%verification%' then 'verification'
    when lower(coalesce(new.event_type,'')) like '%role%' then 'member'
    when lower(coalesce(new.event_type,'')) like '%voice%' then 'voice'
    when lower(coalesce(new.event_type,'')) like '%ban%'
      or lower(coalesce(new.event_type,'')) like '%kick%'
      or lower(coalesce(new.event_type,'')) like '%timeout%'
      or lower(coalesce(new.event_type,'')) like '%warn%' then 'moderation'
    else 'member'
  end;
  perform public.activity_feed_log(
    p_guild_id := new.guild_id,
    p_event_family := v_family,
    p_event_type := new.event_type,
    p_source := 'bot',
    p_actor_user_id := new.actor_id,
    p_actor_name := new.actor_name,
    p_target_user_id := new.user_id,
    p_target_name := coalesce(new.title,new.user_id),
    p_related_table := 'member_events',
    p_related_id := new.id::text,
    p_title := coalesce(new.title,initcap(replace(new.event_type,'_',' '))),
    p_description := coalesce(new.title,initcap(replace(new.event_type,'_',' '))),
    p_reason := new.reason,
    p_metadata := coalesce(new.metadata,'{}'::jsonb),
    p_created_at := new.created_at
  );
  return new;
end;
$function$;
revoke all on function public.trg_member_events_to_activity_feed()
from public,anon,authenticated;
create trigger member_events_to_activity_feed_ai after insert on public.member_events
for each row execute function public.trg_member_events_to_activity_feed();

comment on table public.member_events is
  'Private, guild-scoped member action evidence. Historical records retained; no automatic moderator-evidence pruning.';
