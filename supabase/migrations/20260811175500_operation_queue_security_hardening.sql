-- DS-BACKLOG-027 / #56
-- Harden the persistent operation queue used by the bot/dashboard.

begin;

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
