-- Native birthdays. Service-role-only; no birth years or age-verification shortcuts.
-- Apply to the intended dedicated Supabase project before enabling the feature.
create table if not exists public.dank_birthday_members (
    guild_id text not null,
    user_id text not null,
    birth_month smallint not null check (birth_month between 1 and 12),
    birth_day smallint not null check (birth_day between 1 and 31),
    public_wishes boolean not null default false,
    birthday_role boolean not null default false,
    dm_wishes boolean not null default false,
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id),
    constraint dank_birthday_valid_day check (
        birth_day <= extract(day from (make_date(2000, birth_month, 1) + interval '1 month - 1 day'))
    )
);
create index if not exists dank_birthdays_day_idx
    on public.dank_birthday_members(guild_id, birth_month, birth_day);

create table if not exists public.dank_birthday_guild_settings (
    guild_id text primary key,
    enabled boolean not null default false,
    timezone text not null default 'UTC',
    channel_id text,
    role_id text,
    leap_day_policy text not null default 'feb28'
        check (leap_day_policy in ('feb28', 'mar1')),
    announce_hour smallint not null default 9
        check (announce_hour between 0 and 23),
    next_fire_at timestamptz not null,
    last_local_date date,
    updated_at timestamptz not null default now(),
    constraint dank_birthday_enabled_channel check (not enabled or channel_id is not null)
);
create index if not exists dank_birthday_guild_due_idx
    on public.dank_birthday_guild_settings(next_fire_at)
    where enabled = true;

-- A claim is consumed BEFORE Discord delivery to avoid repeated announcements
-- after an ambiguous network timeout. The ledger also prevents duplicate role grants.
create table if not exists public.dank_birthday_deliveries (
    guild_id text not null,
    user_id text not null,
    local_date date not null,
    kind text not null check (kind in ('announce', 'role', 'dm')),
    claimed_at timestamptz not null default now(),
    primary key (guild_id, user_id, local_date, kind)
);

-- Store the lease before adding the Discord role. A crash after add is recoverable.
create table if not exists public.dank_birthday_role_leases (
    guild_id text not null,
    user_id text not null,
    role_id text not null,
    expires_at timestamptz not null,
    next_try_at timestamptz not null,
    primary key (guild_id, user_id)
);
create index if not exists dank_birthday_role_due_idx
    on public.dank_birthday_role_leases(next_try_at);

alter table public.dank_birthday_members enable row level security;
alter table public.dank_birthday_guild_settings enable row level security;
alter table public.dank_birthday_deliveries enable row level security;
alter table public.dank_birthday_role_leases enable row level security;
revoke all on public.dank_birthday_members from public, anon, authenticated;
revoke all on public.dank_birthday_guild_settings from public, anon, authenticated;
revoke all on public.dank_birthday_deliveries from public, anon, authenticated;
revoke all on public.dank_birthday_role_leases from public, anon, authenticated;
grant select, insert, update, delete on public.dank_birthday_members to service_role;
grant select, insert, update, delete on public.dank_birthday_guild_settings to service_role;
grant select, insert, update, delete on public.dank_birthday_deliveries to service_role;
grant select, insert, update, delete on public.dank_birthday_role_leases to service_role;

-- Indexed due queue: no scan of every guild each minute and no replay of
-- old birthdays after downtime. Locks protect concurrent workers/shards.
create or replace function public.dank_birthdays_claim_due(p_limit integer default 24)
returns setof public.dank_birthday_guild_settings
language plpgsql security invoker set search_path = public
as $$
begin
    if p_limit < 1 or p_limit > 50 then
        raise exception 'invalid birthday claim size';
    end if;
    return query
    with due as (
        select s.guild_id from public.dank_birthday_guild_settings s
        where s.enabled and s.next_fire_at <= now()
        order by s.next_fire_at, s.guild_id
        for update skip locked limit p_limit
    )
    update public.dank_birthday_guild_settings s
       set last_local_date = (now() at time zone s.timezone)::date,
           next_fire_at = (((now() at time zone s.timezone)::date + 1
                           + make_interval(hours => s.announce_hour))
                           at time zone s.timezone),
           updated_at = now()
      from due where s.guild_id = due.guild_id
    returning s.*;
end;
$$;

create or replace function public.dank_birthdays_claim_delivery(
    p_guild_id text, p_user_id text, p_local_date date, p_kind text
)
returns boolean language plpgsql security invoker set search_path = public
as $$
declare affected bigint;
begin
    insert into public.dank_birthday_deliveries(guild_id, user_id, local_date, kind)
        values (p_guild_id, p_user_id, p_local_date, p_kind)
    on conflict do nothing;
    get diagnostics affected = row_count;
    return affected > 0;
end;
$$;

-- Due role leases are reserved for five minutes. A failed Discord removal
-- will be tried again; a successful one must explicitly delete its lease.
create or replace function public.dank_birthdays_claim_expired_roles(p_limit integer default 24)
returns setof public.dank_birthday_role_leases
language plpgsql security invoker set search_path = public
as $$
begin
    if p_limit < 1 or p_limit > 50 then
        raise exception 'invalid birthday role batch';
    end if;
    return query
    with due as (
        select l.guild_id, l.user_id from public.dank_birthday_role_leases l
        where l.expires_at <= now() and l.next_try_at <= now()
        order by l.next_try_at
        for update skip locked limit p_limit
    )
    update public.dank_birthday_role_leases l
       set next_try_at = now() + interval '5 minutes'
      from due
     where l.guild_id = due.guild_id and l.user_id = due.user_id
    returning l.*;
end;
$$;

revoke all on function public.dank_birthdays_claim_due(integer) from public, anon, authenticated;
revoke all on function public.dank_birthdays_claim_delivery(text,text,date,text) from public, anon, authenticated;
revoke all on function public.dank_birthdays_claim_expired_roles(integer) from public, anon, authenticated;
grant execute on function public.dank_birthdays_claim_due(integer) to service_role;
grant execute on function public.dank_birthdays_claim_delivery(text,text,date,text) to service_role;
grant execute on function public.dank_birthdays_claim_expired_roles(integer) to service_role;
