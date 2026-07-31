-- Automatically provision the current managed catalog for newly configured guilds.
-- Catalog changes remain migration-versioned: each future catalog migration calls
-- reconcile_dank_ticket_categories(null), updating every existing guild.

create or replace function public.sync_dank_ticket_categories_for_new_guild()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    perform * from public.reconcile_dank_ticket_categories(new.guild_id::text);
    return new;
end;
$$;

do $$
begin
    if to_regclass('public.guild_configs') is not null then
        execute 'drop trigger if exists guild_configs_sync_dank_ticket_categories on public.guild_configs';
        execute 'create trigger guild_configs_sync_dank_ticket_categories after insert on public.guild_configs for each row execute function public.sync_dank_ticket_categories_for_new_guild()';
    else
        raise notice 'Skipping managed ticket-category auto-sync trigger because public.guild_configs does not exist.';
    end if;
end;
$$;

revoke all on function public.sync_dank_ticket_categories_for_new_guild() from public, anon, authenticated;
