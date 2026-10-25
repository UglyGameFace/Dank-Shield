-- Manual-only verification reminder claims. Additive; automatic reminder rows stay untouched.
-- Apply explicitly to the existing private Discloud PostgreSQL database before enabling manual sends.
create table if not exists public.dank_verify_manual_guild_budget (
    guild_id text primary key,
    last_claimed_at timestamptz not null default now(),
    window_started_at timestamptz not null default now(),
    window_claims integer not null default 1 check (window_claims between 1 and 4)
);
create table if not exists public.dank_verify_manual_member_delivery (
    guild_id text not null,
    user_id text not null,
    joined_at timestamptz not null,
    last_claimed_at timestamptz not null default now(),
    attempts integer not null default 1 check (attempts >= 1),
    primary key (guild_id, user_id)
);
create index if not exists idx_dank_verify_manual_stale
    on public.dank_verify_manual_member_delivery
    (last_claimed_at, guild_id, user_id);
-- No public/member GRANT. The private bot database account owns these ledgers.

-- Existing live installations may still have the old two-per-join check.
-- An explicit rerun of this migration upgrades that constraint in place.
alter table public.dank_verify_manual_member_delivery
    drop constraint if exists dank_verify_manual_member_delivery_attempts_check;
alter table public.dank_verify_manual_member_delivery
    add constraint dank_verify_manual_member_delivery_attempts_check check (attempts >= 1);
