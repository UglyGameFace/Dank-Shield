-- Durable guild-scoped security reputation for confirmed destructive actors.
-- Only service-role runtime access is permitted; Discord users do not query this
-- table directly.

create table if not exists public.guild_security_actor_reputation (
    guild_id bigint not null,
    user_id bigint not null,
    active boolean not null default true,
    classification text not null default 'confirmed_destructive_actor',
    source text not null default 'antinuke',
    is_bot boolean not null default false,
    incident_count integer not null default 1 check (incident_count >= 1),
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    last_reason text,
    related_user_id bigint,
    cleared_at timestamptz,
    cleared_by bigint,
    clear_reason text,
    primary key (guild_id, user_id)
);

create index if not exists idx_guild_security_actor_reputation_active
    on public.guild_security_actor_reputation (guild_id, active, last_seen_at desc);

create index if not exists idx_guild_security_actor_reputation_related
    on public.guild_security_actor_reputation (guild_id, related_user_id)
    where related_user_id is not null;

alter table public.guild_security_actor_reputation enable row level security;

revoke all on table public.guild_security_actor_reputation from public;
revoke all on table public.guild_security_actor_reputation from anon, authenticated;
grant select, insert, update, delete on table public.guild_security_actor_reputation to service_role;

comment on table public.guild_security_actor_reputation is
    'Guild-scoped durable hostile identity dispositions created only from confirmed AntiNuke incidents or hard identity links to an already confirmed hostile actor.';
comment on column public.guild_security_actor_reputation.related_user_id is
    'Confirmed hostile Discord user ID that a hard-proof linked identity inherited its disposition from; never populated from heuristic similarity alone.';
