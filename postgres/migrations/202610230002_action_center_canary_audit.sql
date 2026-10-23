-- 4D.4 manually applied AFTER private 4C claims schema has been verified.
-- Stores operator-approved one-member canary attempts, NEVER message content.
-- This migration is NOT called by startup, the bot, CI against production,
-- or by a GitHub deployment workflow.
create table if not exists public.dank_action_center_canary_audit (
    operation_id uuid primary key,
    guild_id text not null,
    target_user_id text not null,
    operator_user_id text not null,
    revision integer not null check (revision > 0),
    requested_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null check (status in ('requested','sent','cancelled','claim_denied','blocked','send_failed','unavailable','disabled','stale','excluded')),
    claimed boolean not null default false,
    sent boolean not null default false,
    constraint action_canary_sent_claim check (not sent or claimed),
    constraint action_canary_finished check (
      (status = 'requested' and finished_at is null)
      or (status <> 'requested' and finished_at is not null)
    )
);
create index if not exists idx_action_canary_audit_requested
    on public.dank_action_center_canary_audit(requested_at, guild_id);
-- A stable, minimal audit record is written before any canary delivery.
-- Future lifecycle cleanup needs audited backup/retention before activation.
