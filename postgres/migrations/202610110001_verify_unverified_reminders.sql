-- Dank Shield verification reminder ledger on private Discloud PostgreSQL.
-- Run ONCE during deployment, explicitly, with the PostgreSQL credentials.
-- No Supabase-specific roles/RLS/functions or bot-startup DDL are required.
create table if not exists public.dank_verify_reminder_delivery (
    guild_id text not null,
    user_id text not null,
    joined_at timestamptz not null,
    last_claimed_at timestamptz not null default now(),
    attempts integer not null default 1 check (attempts between 1 and 5),
    primary key (guild_id, user_id)
);

-- Cleanup only considers records idle for at least 90 days. Membership
-- must independently be confirmed departed by Discord REST before deletion.
create index if not exists idx_dank_verify_reminders_idle_scan
    on public.dank_verify_reminder_delivery
    (last_claimed_at, guild_id, user_id);
