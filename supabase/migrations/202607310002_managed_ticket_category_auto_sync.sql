-- Automatically provision the current managed catalog for newly configured guilds.
-- Catalog changes remain migration-versioned: each future catalog migration calls
-- reconcile_dank_ticket_categories(null), updating every existing guild.

create or replace function public.sync_dank_ticket_categories_for_new_guild()
returns trigger
language plpgsql
security definer
set search_path=public
as $$
begin
    perform * from public.reconcile_dank_ticket_categories(new.guild_id::text);
    return new;
end;
$$;

drop trigger if exists guild_configs_sync_dank_ticket_categories on public.guild_configs;
create trigger guild_configs_sync_dank_ticket_categories
after insert on public.guild_configs
for each row execute function public.sync_dank_ticket_categories_for_new_guild();

revoke all on function public.sync_dank_ticket_categories_for_new_guild() from public,anon,authenticated;
