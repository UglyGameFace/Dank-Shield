-- Action Center 4C: additive, INACTIVE delivery claim ledger on existing
-- private PostgreSQL. Never run on bot startup; never change verify ledger.
-- Apply only after explicitly verifying DANK_VERIFY_REMINDER_DATABASE_URL.
create table if not exists public.dank_action_center_delivery (
    guild_id text not null,
    user_id text not null,
    condition text not null check (condition in ('member_setup_revision')),
    condition_token text not null,
    last_claimed_at timestamptz not null default now(),
    attempts integer not null default 1 check (attempts between 1 and 3),
    primary key(guild_id,user_id,condition,condition_token)
);
create index if not exists idx_dank_action_delivery_age
    on public.dank_action_center_delivery(last_claimed_at,guild_id,user_id);
create table if not exists public.dank_action_center_daily_budget (
    guild_id text not null,
    local_day date not null,
    claims integer not null default 0 check (claims between 0 and 10),
    primary key(guild_id,local_day)
);
create index if not exists idx_dank_action_budget_expiry
    on public.dank_action_center_daily_budget(local_day,guild_id);
-- No RPC, scheduled task, REST send or public privileges are created.
-- A later reviewed 4D implementation must reserve per-member and per-guild
-- budgets in ONE transaction, before send, and recheck owner completion.
