-- Dank Shield operation queue persistence
-- Run this in Supabase SQL Editor when direct DB bootstrap is not available.

create table if not exists public.bot_operation_jobs (
    id uuid primary key,
    guild_id text not null,
    actor_id text,
    operation_type text not null,
    risk_level text not null,
    source text not null,
    idempotency_key text not null,
    payload_hash text not null,
    status text not null,
    progress_current integer not null default 0,
    progress_total integer not null default 0,
    result_json jsonb not null default '{}'::jsonb,
    error_code text,
    error_message text,
    locked_by text,
    lock_expires_at timestamptz,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    unique (guild_id, idempotency_key)
);

alter table public.bot_operation_jobs add column if not exists actor_id text;
alter table public.bot_operation_jobs add column if not exists operation_type text not null default 'operation';
alter table public.bot_operation_jobs add column if not exists risk_level text not null default 'moderate';
alter table public.bot_operation_jobs add column if not exists source text not null default 'system';
alter table public.bot_operation_jobs add column if not exists idempotency_key text not null default '';
alter table public.bot_operation_jobs add column if not exists payload_hash text not null default '';
alter table public.bot_operation_jobs add column if not exists status text not null default 'queued';
alter table public.bot_operation_jobs add column if not exists progress_current integer not null default 0;
alter table public.bot_operation_jobs add column if not exists progress_total integer not null default 0;
alter table public.bot_operation_jobs add column if not exists result_json jsonb not null default '{}'::jsonb;
alter table public.bot_operation_jobs add column if not exists error_code text;
alter table public.bot_operation_jobs add column if not exists error_message text;
alter table public.bot_operation_jobs add column if not exists locked_by text;
alter table public.bot_operation_jobs add column if not exists lock_expires_at timestamptz;
alter table public.bot_operation_jobs add column if not exists started_at timestamptz;
alter table public.bot_operation_jobs add column if not exists finished_at timestamptz;

create index if not exists idx_bot_operation_jobs_guild_status_created
    on public.bot_operation_jobs (guild_id, status, created_at desc);

create index if not exists idx_bot_operation_jobs_type_status_created
    on public.bot_operation_jobs (operation_type, status, created_at desc);

create index if not exists idx_bot_operation_jobs_lock_expires
    on public.bot_operation_jobs (lock_expires_at)
    where lock_expires_at is not null;

-- Service-role-only runtime access is expected. Do not grant anonymous access.
