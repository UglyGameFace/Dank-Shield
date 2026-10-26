-- Opt-in partner advertising cleanup ledger. Does not alter Invite Shield
-- settings or delete a single message during migration.
create table if not exists public.dank_trusted_advert_messages (
  guild_id text not null,
  channel_id text not null,
  bot_id text not null,
  message_id text not null,
  posted_at timestamptz not null,
  delete_after timestamptz not null,
  state text not null default 'pending',
  claimed_until timestamptz,
  attempts integer not null default 0,
  last_error text,
  updated_at timestamptz not null default now(),
  primary key (guild_id, message_id),
  constraint trusted_ad_state check (state in ('pending','claimed','done','cancelled','failed')),
  constraint trusted_ad_age check (delete_after >= posted_at + interval '24 hours')
);
create index if not exists dank_trusted_ads_due
  on public.dank_trusted_advert_messages (delete_after)
  where state in ('pending', 'claimed');

alter table public.dank_trusted_advert_messages enable row level security;
revoke all on public.dank_trusted_advert_messages from public, anon, authenticated;
grant select, insert, update, delete on public.dank_trusted_advert_messages to service_role;

-- Atomic, rate-bounded cross-process claim. No global Discord history scans.
create or replace function public.dank_claim_due_trusted_adverts(p_limit integer default 8)
returns setof public.dank_trusted_advert_messages
language sql security definer set search_path=''
as $$
  with due as (
    select guild_id, message_id
    from public.dank_trusted_advert_messages
    where (
      (state = 'pending' and delete_after <= now())
      or (state = 'claimed' and claimed_until <= now())
    ) and attempts < 8
    order by delete_after, guild_id, message_id
    limit least(16, greatest(1, coalesce(p_limit, 8)))
    for update skip locked
  )
  update public.dank_trusted_advert_messages as m
  set state='claimed', claimed_until=now() + interval '5 minutes',
      attempts=m.attempts+1, updated_at=now()
  from due
  where m.guild_id=due.guild_id and m.message_id=due.message_id
  returning m.*;
$$;
revoke all on function public.dank_claim_due_trusted_adverts(integer)
  from public, anon, authenticated;
grant execute on function public.dank_claim_due_trusted_adverts(integer)
  to service_role;
