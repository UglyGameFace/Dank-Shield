-- Optional, separately approved enforcement ledger. The existing reminder tables
-- and their delivery behavior are unchanged. Apply explicitly after backup.
-- A confirmed delivery counts only when the server has opted into enforcement.
create table if not exists public.dank_verify_reminder_enforcement (
  guild_id text not null,
  user_id text not null,
  joined_at timestamptz not null,
  delivered_count integer not null default 0 check (delivered_count between 0 and 5),
  last_delivered_at timestamptz,
  action_claimed_at timestamptz,
  action_result text,
  primary key (guild_id, user_id, joined_at)
);

create index if not exists idx_dank_verify_reminder_enforcement_due
  on public.dank_verify_reminder_enforcement
  (guild_id, last_delivered_at)
  where delivered_count >= 3 and action_claimed_at is null;
