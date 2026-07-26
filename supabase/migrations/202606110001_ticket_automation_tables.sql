-- Dank Shield ticket automation persistence
-- Supports SLA alerts, inactivity reminders, and optional auto-close state.

create table if not exists public.ticket_automation_settings (
    guild_id text primary key,
    enabled boolean not null default false,
    sla_breach_alerts_enabled boolean not null default true,
    inactivity_reminders_enabled boolean not null default true,
    auto_close_enabled boolean not null default false,
    inactivity_reminder_minutes integer not null default 240,
    auto_close_minutes integer not null default 1440,
    staff_alert_channel_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.ticket_automation_state (
    guild_id text not null,
    channel_id text not null,
    sla_breach_alert_sent_at timestamptz,
    last_inactivity_reminder_at timestamptz,
    auto_closed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, channel_id)
);

create index if not exists idx_ticket_automation_settings_enabled
    on public.ticket_automation_settings (enabled);

create index if not exists idx_ticket_automation_state_guild
    on public.ticket_automation_state (guild_id);

create index if not exists idx_ticket_automation_state_auto_closed
    on public.ticket_automation_state (auto_closed_at);

alter table public.ticket_automation_settings enable row level security;
alter table public.ticket_automation_state enable row level security;

-- The bot uses the service-role key. Keep normal client access closed by default.
drop policy if exists ticket_automation_settings_service_role_all on public.ticket_automation_settings;
create policy ticket_automation_settings_service_role_all
on public.ticket_automation_settings
for all
to service_role
using (true)
with check (true);

drop policy if exists ticket_automation_state_service_role_all on public.ticket_automation_state;
create policy ticket_automation_state_service_role_all
on public.ticket_automation_state
for all
to service_role
using (true)
with check (true);
