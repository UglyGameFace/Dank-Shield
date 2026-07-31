-- ============================================================
-- 20260731141000_ticket_counter_durability.sql
-- ------------------------------------------------------------
-- Make per-guild ticket numbers a durable database-owned sequence.
--
-- Guarantees:
-- - Never lowers an existing counter.
-- - Backfills each guild from the highest persisted ticket number.
-- - Reserves numbers atomically through one service-role-only RPC.
-- - Keeps the direct table fallback available to the service role.
-- - Safe to run more than once.
-- ============================================================

create table if not exists public.ticket_counters (
    guild_id text primary key,
    last_ticket_number integer not null default 0,
    updated_at timestamptz not null default now()
);

alter table public.ticket_counters
    add column if not exists last_ticket_number integer not null default 0,
    add column if not exists updated_at timestamptz not null default now();

-- Seed or raise counters from every historical ticket row already persisted.
do $backfill$
begin
    if to_regclass('public.tickets') is not null then
        insert into public.ticket_counters (guild_id, last_ticket_number, updated_at)
        select
            t.guild_id::text,
            greatest(coalesce(max(t.ticket_number), 0), 0)::integer,
            now()
        from public.tickets t
        where nullif(btrim(t.guild_id::text), '') is not null
        group by t.guild_id::text
        on conflict (guild_id) do update
            set last_ticket_number = greatest(
                    public.ticket_counters.last_ticket_number,
                    excluded.last_ticket_number
                ),
                updated_at = now();
    end if;
end
$backfill$;

create or replace function public.reserve_ticket_number(p_guild_id text)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
    v_guild_id text := nullif(btrim(p_guild_id), '');
    v_history_floor integer := 0;
    v_next integer;
begin
    if v_guild_id is null then
        raise exception 'guild_id is required';
    end if;

    if to_regclass('public.tickets') is not null then
        execute
            'select coalesce(max(ticket_number), 0)::integer
               from public.tickets
              where guild_id::text = $1
                and ticket_number is not null'
        into v_history_floor
        using v_guild_id;
    end if;

    insert into public.ticket_counters (guild_id, last_ticket_number, updated_at)
    values (v_guild_id, greatest(v_history_floor, 0), now())
    on conflict (guild_id) do update
        set last_ticket_number = greatest(
                public.ticket_counters.last_ticket_number,
                excluded.last_ticket_number
            ),
            updated_at = now();

    update public.ticket_counters
       set last_ticket_number = last_ticket_number + 1,
           updated_at = now()
     where guild_id = v_guild_id
     returning last_ticket_number into v_next;

    if v_next is null or v_next <= 0 then
        raise exception 'failed to reserve ticket number for guild %', v_guild_id;
    end if;

    return v_next;
end
$function$;

alter table public.ticket_counters enable row level security;

revoke all on table public.ticket_counters from public, anon, authenticated;
grant select, insert, update on table public.ticket_counters to service_role;

revoke all on function public.reserve_ticket_number(text) from public, anon, authenticated;
grant execute on function public.reserve_ticket_number(text) to service_role;

create index if not exists idx_ticket_counters_updated_at
    on public.ticket_counters (updated_at desc);

-- Keep the historical lookup fast. Only enforce uniqueness when existing data
-- is clean; never destroy or rewrite duplicate historical rows in a migration.
do $indexes$
begin
    if to_regclass('public.tickets') is null then
        return;
    end if;

    execute 'create index if not exists idx_tickets_guild_ticket_number_desc
             on public.tickets (guild_id, ticket_number desc)
             where ticket_number is not null';

    if not exists (
        select 1
        from public.tickets t
        where t.ticket_number is not null
        group by t.guild_id, t.ticket_number
        having count(*) > 1
        limit 1
    ) then
        execute 'create unique index if not exists uq_tickets_guild_ticket_number
                 on public.tickets (guild_id, ticket_number)
                 where ticket_number is not null';
    else
        raise notice 'Skipping uq_tickets_guild_ticket_number because duplicate historical ticket numbers exist.';
    end if;
end
$indexes$;

comment on table public.ticket_counters is
    'Durable per-guild ticket-number floor. Service-role only.';
comment on function public.reserve_ticket_number(text) is
    'Atomically reserves the next never-reused ticket number for one Discord guild.';
