-- Dank Shield Pro membership: covered-guild links (Phase 1, 2026-11-18).
-- A Pro account (Discord app-subscription entitlement holder) links up to 3
-- guilds; each guild has at most one owning account. Billing/entitlement truth
-- stays with Discord — this table only records coverage choices. Apply
-- explicitly after backup; the bot treats a missing table as "no links".
create table if not exists public.dank_membership_guild_links (
  user_id bigint not null,
  guild_id bigint not null,
  linked_at timestamptz not null default now(),
  primary key (user_id, guild_id),
  unique (guild_id)
);

create index if not exists idx_dank_membership_guild_links_user
  on public.dank_membership_guild_links (user_id, linked_at);
