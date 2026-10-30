-- Dedicated Dank Shield: backend-only status heartbeat missing from the live schema.
-- Additive and idempotent. This creates no Discord messages and edits no existing data.
create table if not exists public.bot_status_heartbeats (
    bot_id text primary key,
    status text not null check (status in ('online', 'degraded', 'offline')),
    last_seen_at timestamptz not null,
    guild_count integer not null default 0 check (guild_count >= 0),
    updated_at timestamptz not null default now(),
    constraint bot_status_heartbeat_bot_id_nonempty check (
        length(btrim(bot_id)) between 1 and 128
    )
);

alter table public.bot_status_heartbeats enable row level security;
revoke all on public.bot_status_heartbeats from public, anon, authenticated;
grant select, insert, update on public.bot_status_heartbeats to service_role;

-- The Discord worker writes one row per bot using ON CONFLICT (bot_id).
-- No anonymous/authenticated access, notification triggers or periodic jobs.
