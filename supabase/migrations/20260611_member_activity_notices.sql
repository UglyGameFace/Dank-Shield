-- Dank Shield member activity notice persistence
-- Stores scheduled/delivered DM notice state for /dank members activity review.

create table if not exists public.member_activity_notices (
    notice_id text primary key,
    guild_id text not null,
    guild_name text,
    user_id text not null,
    user_display_name text,
    scope text,
    status text not null default 'scheduled',
    send_at timestamptz,
    deadline_at timestamptz,
    created_by text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    sent_at timestamptz,
    attempted_at timestamptz,
    responded_at timestamptz,
    response text,
    note text,
    confidence text,
    inactivity_days integer,
    removable boolean not null default false,
    error text,
    dm_message_id text
);

create index if not exists idx_member_activity_notices_guild_status
    on public.member_activity_notices (guild_id, status);

create index if not exists idx_member_activity_notices_user_status
    on public.member_activity_notices (user_id, status);

create index if not exists idx_member_activity_notices_send_at
    on public.member_activity_notices (send_at);

create index if not exists idx_member_activity_notices_deadline_at
    on public.member_activity_notices (deadline_at);

alter table public.member_activity_notices enable row level security;

-- The bot uses the service-role key. Keep normal client access closed by default.
drop policy if exists member_activity_notices_service_role_all on public.member_activity_notices;
create policy member_activity_notices_service_role_all
on public.member_activity_notices
for all
to service_role
using (true)
with check (true);
