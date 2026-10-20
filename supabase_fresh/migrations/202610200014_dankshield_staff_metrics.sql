-- Dedicated, offline DankShield project only.
-- Runtime-equivalent minimum for metrics_sync_worker and tickets workspace.
-- Do not switch Discloud source until historical staff-metric rows are
-- independently reconciled from the original Supabase/Aiven source.
create table public.staff_metrics (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    staff_id text not null,
    staff_name text not null default '',
    tickets_handled integer not null default 0 check (tickets_handled >= 0),
    approvals integer not null default 0 check (approvals >= 0),
    denials integer not null default 0 check (denials >= 0),
    avg_response_minutes integer not null default 0 check (avg_response_minutes >= 0),
    last_active timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint staff_metrics_guild_staff_unique unique (guild_id, staff_id),
    constraint staff_metrics_guild_id_present check (btrim(guild_id) <> ''),
    constraint staff_metrics_staff_id_present check (btrim(staff_id) <> '')
);
create index staff_metrics_guild_last_active_idx
  on public.staff_metrics (guild_id, last_active desc nulls last);
alter table public.staff_metrics enable row level security;
revoke all on public.staff_metrics from public, anon, authenticated;
grant select,insert,update,delete on public.staff_metrics to service_role;
