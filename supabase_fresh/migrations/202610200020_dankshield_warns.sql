-- Dedicated offline candidate only. Source: manual read-only audit run #39485389338.
-- The original public.warns has seven columns and UUID(id) PK.
-- No historical warning rows are imported by this schema migration.
create table public.warns (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  user_id text not null,
  username text,
  reason text not null,
  source_message text,
  created_at timestamptz default now()
);
create index warns_guild_user_created_idx
  on public.warns (guild_id, user_id, created_at desc);
alter table public.warns enable row level security;
revoke all on public.warns from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.warns to service_role;
comment on table public.warns is
  'Private per-guild moderation warning evidence; preserve until verified staff policy clearance, no automatic retention purge.';
