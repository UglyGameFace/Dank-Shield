-- OFFLINE dedicated Dank Shield project only. Source 531 operation jobs
-- all terminal, source 50 commands all completed. DO NOT import/replay them.
-- Canonical original: supabase/migrations/20260613_bot_operation_jobs.sql
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

-- Canonical hardening: supabase/migrations/20260811175500_operation_queue_security_hardening.sql
-- DS-BACKLOG-027 / #56
-- Harden and normalize the persistent operation queue used by bot/dashboard.

begin;

-- The original June migration accepted application-generated UUIDs but did not
-- give the database a default. The direct-bootstrap path does. Normalize the
-- historical table so both creation paths now expose the same contract.
alter table if exists public.bot_operation_jobs
    alter column id set default gen_random_uuid();

alter table if exists public.bot_operation_jobs enable row level security;

revoke all on table public.bot_operation_jobs from anon;
revoke all on table public.bot_operation_jobs from authenticated;
grant select, insert, update, delete on table public.bot_operation_jobs to service_role;

-- Keep status/risk/source data truthful even when a future caller bypasses the
-- Python model. NOT VALID lets an existing production table upgrade safely;
-- validation then proves current rows conform before the migration completes.
alter table public.bot_operation_jobs
    drop constraint if exists bot_operation_jobs_status_check;
alter table public.bot_operation_jobs
    add constraint bot_operation_jobs_status_check
    check (status in ('queued','running','waiting_rate_limit','partial','succeeded','failed','cancelled','expired')) not valid;
alter table public.bot_operation_jobs validate constraint bot_operation_jobs_status_check;

alter table public.bot_operation_jobs
    drop constraint if exists bot_operation_jobs_risk_level_check;
alter table public.bot_operation_jobs
    add constraint bot_operation_jobs_risk_level_check
    check (risk_level in ('safe','moderate','dangerous')) not valid;
alter table public.bot_operation_jobs validate constraint bot_operation_jobs_risk_level_check;

alter table public.bot_operation_jobs
    drop constraint if exists bot_operation_jobs_source_check;
alter table public.bot_operation_jobs
    add constraint bot_operation_jobs_source_check
    check (source in ('discord_command','dashboard','scheduler','startup','system')) not valid;
alter table public.bot_operation_jobs validate constraint bot_operation_jobs_source_check;

create index if not exists idx_bot_operation_jobs_active_recovery
    on public.bot_operation_jobs (status, lock_expires_at, created_at)
    where status in ('queued','running','waiting_rate_limit');

create index if not exists idx_bot_operation_jobs_guild_operation_active
    on public.bot_operation_jobs (guild_id, operation_type, status, created_at desc)
    where status in ('queued','running','waiting_rate_limit');

commit;

-- Source-verified 11-column dashboard command queue. Unlike old source
-- which has RLS off/public grants, this copy is private backend-only.
create table public.bot_commands (
 id uuid primary key default gen_random_uuid(),
 guild_id text not null,
 action text not null,
 payload jsonb not null default '{}'::jsonb,
 status text not null default 'pending',
 result jsonb not null default '{}'::jsonb,
 error text,
 requested_by text,
 created_at timestamptz not null default now(),
 picked_up_at timestamptz,
 completed_at timestamptz,
 constraint bot_commands_status_check
  check (status in ('pending','processing','completed','failed'))
);
create index bot_commands_guild_status_created_idx
 on public.bot_commands(guild_id,status,created_at);
create index bot_commands_status_created_idx
 on public.bot_commands(status,created_at);
alter table public.bot_commands enable row level security;
revoke all on table public.bot_commands,public.bot_operation_jobs
 from public,anon,authenticated,service_role;
grant select,insert,update,delete on table public.bot_commands,
 public.bot_operation_jobs to service_role;
comment on table public.bot_commands is
 'Private Discord dashboard command queue. No old terminal commands replayed.';
comment on table public.bot_operation_jobs is
 'Private per-guild operation queue. Old terminal jobs excluded from import.';
