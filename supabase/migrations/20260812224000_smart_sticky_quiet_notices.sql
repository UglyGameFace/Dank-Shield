-- DS-STICKY-029: server-wide quiet-activity notices for Community Tools.
-- Service-role only: Discord users never access this table directly.

create table if not exists public.dank_quiet_notices (
    guild_id bigint primary key,
    channel_id bigint not null,
    enabled boolean not null default true,
    content text not null,
    inactivity_seconds integer not null default 7200
        check (inactivity_seconds between 300 and 604800),
    partner_name text,
    partner_url text,
    auto_clear boolean not null default true,
    last_activity_at timestamptz,
    last_notice_message_id bigint,
    last_notice_sent_at timestamptz,
    updated_by bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (char_length(content) between 1 and 1800),
    check (partner_name is null or char_length(partner_name) <= 100),
    check (partner_url is null or char_length(partner_url) <= 1000)
);

create index if not exists dank_quiet_notices_channel_id_idx
    on public.dank_quiet_notices (channel_id);

alter table public.dank_quiet_notices enable row level security;

revoke all on table public.dank_quiet_notices from anon, authenticated;
grant all on table public.dank_quiet_notices to service_role;

comment on table public.dank_quiet_notices is
    'Dank Shield server-wide inactivity notices; one configurable quiet notice per guild.';
