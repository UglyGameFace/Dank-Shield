-- OFFLINE dedicated Dank Shield ONLY. No Discloud runtime switch.
-- Original source hyncpbvqyjojxhjzcdzg inspected on 2026-10-20.
-- Source public.member_cleanup_settings is empty; preserve safe defaults.
create table public.member_cleanup_settings (
 guild_id text primary key,
 require_queue_confirmation boolean not null default true,
 allow_low_confidence_queue boolean not null default false,
 default_queue_limit integer not null default 10
  check(default_queue_limit between 1 and 20),
 updated_by text,
 updated_at timestamptz not null default now()
);
alter table public.member_cleanup_settings enable row level security;
revoke all on public.member_cleanup_settings from public,anon,authenticated,service_role;
grant select,insert,update,delete on public.member_cleanup_settings to service_role;

-- Source-aligned staff_action_logs history (3,369 source rows).
-- Moderation evidence is private, indexed for guild/time queries and
-- intentionally NOT expired, reexecuted or deleted automatically.
create table public.staff_action_logs (
 id bigint primary key,
 guild_id text not null,
 actor_id text,
 actor_display text not null,
 target_id text,
 target_display text,
 action text not null,
 reason text,
 duration text,
 metadata jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now()
);
create index staff_action_logs_guild_created_idx
 on public.staff_action_logs (guild_id,created_at desc,id desc);
create index staff_action_logs_guild_actor_created_idx
 on public.staff_action_logs (guild_id,actor_id,created_at desc);
alter table public.staff_action_logs enable row level security;
revoke all on public.staff_action_logs from public,anon,authenticated,service_role;
grant select,insert,update,delete on public.staff_action_logs to service_role;
comment on table public.staff_action_logs is
 'Private staff moderation evidence from original source. Preserve exactly; no automatic deletes or action replay.';
comment on table public.member_cleanup_settings is
 'Safe default requires confirmation; low-confidence member cleanup disabled by default.';
