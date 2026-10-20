-- NEW dedicated Supabase only: viamriafkbqhquvrsnua
-- Restore ticket-number allocation while retaining legacy duplicates.
-- RLS and service-role-only access remain enforced.
create unique index uq_dank_ticket_channel on public.tickets(channel_id) where channel_id is not null;
create unique index uq_dank_ticket_thread on public.tickets(discord_thread_id) where nullif(discord_thread_id,'') is not null;
create unique index uq_dank_ticket_category_key on public.ticket_categories(guild_id,category_key);
create unique index uq_dank_ticket_managed_category on public.ticket_categories(guild_id,managed_category_key) where managed_by_dank=true and managed_category_key is not null;

CREATE OR REPLACE FUNCTION public.reserve_ticket_number(p_guild_id text)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
revoke all on function public.reserve_ticket_number(text) from public,anon,authenticated;
grant execute on function public.reserve_ticket_number(text) to service_role;
-- Don't add unique(guild_id,ticket_number): the source has 2 historical
-- duplicate-number groups. Future allocator reads the greatest existing
-- ticket number and reserves the next value under PostgreSQL row lock.
