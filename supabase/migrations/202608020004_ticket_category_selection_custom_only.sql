-- Final category-selection writer.
--
-- Qualify every ticket_categories column because the function's TABLE return
-- names are also PL/pgSQL variables. Also allow an explicit "custom choices
-- only" save when the guild already has at least one enabled custom category.

create or replace function public.save_dank_ticket_category_selection(
    p_guild_id text,
    p_selected_keys text[],
    p_actor_id text default null,
    p_actor_name text default null
)
returns table(category_key text, slug text, name text, is_enabled boolean, is_default boolean)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    selected_keys text[];
    invalid_keys text[];
    default_key text;
    custom_default_id uuid;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    select coalesce(array_agg(distinct btrim(chosen.value)), array[]::text[])
      into selected_keys
      from unnest(coalesce(p_selected_keys, array[]::text[])) as chosen(value)
     where nullif(btrim(chosen.value), '') is not null;

    if coalesce(array_length(selected_keys, 1), 0) = 0
       and not exists (
           select 1
             from public.ticket_categories custom_row
            where custom_row.guild_id::text = btrim(p_guild_id)
              and custom_row.managed_by_dank = false
              and custom_row.is_enabled = true
       ) then
        raise exception 'choose at least one ticket category';
    end if;

    select coalesce(array_agg(chosen.value), array[]::text[])
      into invalid_keys
      from unnest(selected_keys) as chosen(value)
     where not exists (
        select 1
          from public.dank_ticket_category_catalog() catalog
         where catalog.category_key = chosen.value
     );

    if coalesce(array_length(invalid_keys, 1), 0) > 0 then
        raise exception 'unknown managed ticket category key(s): %', invalid_keys;
    end if;

    perform * from public.reconcile_dank_ticket_categories(btrim(p_guild_id));

    update public.ticket_categories managed_row
       set is_enabled = managed_row.managed_category_key = any(selected_keys),
           is_default = false,
           updated_at = now()
     where managed_row.guild_id::text = btrim(p_guild_id)
       and managed_row.managed_by_dank = true;

    default_key := null;
    if coalesce(array_length(selected_keys, 1), 0) > 0 then
        if 'support' = any(selected_keys) then
            default_key := 'support';
        else
            select catalog.category_key
              into default_key
              from public.dank_ticket_category_catalog() catalog
             where catalog.category_key = any(selected_keys)
             order by catalog.sort_order, catalog.category_key
             limit 1;
        end if;

        update public.ticket_categories managed_row
           set is_default = managed_row.managed_category_key = default_key,
               updated_at = now()
         where managed_row.guild_id::text = btrim(p_guild_id)
           and managed_row.managed_by_dank = true;

        update public.ticket_categories custom_row
           set is_default = false,
               updated_at = now()
         where custom_row.guild_id::text = btrim(p_guild_id)
           and custom_row.managed_by_dank = false
           and custom_row.is_default = true;
    else
        -- Explicit custom-only setup: keep an existing custom fallback or choose
        -- the first enabled custom row deterministically.
        select custom_row.id
          into custom_default_id
          from public.ticket_categories custom_row
         where custom_row.guild_id::text = btrim(p_guild_id)
           and custom_row.managed_by_dank = false
           and custom_row.is_enabled = true
           and custom_row.is_default = true
         order by coalesce(custom_row.sort_order, 9999), custom_row.created_at, custom_row.id
         limit 1;

        if custom_default_id is null then
            select custom_row.id
              into custom_default_id
              from public.ticket_categories custom_row
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false
               and custom_row.is_enabled = true
             order by coalesce(custom_row.sort_order, 9999), custom_row.created_at, custom_row.id
             limit 1;
        end if;

        update public.ticket_categories custom_row
           set is_default = custom_row.id = custom_default_id,
               updated_at = now()
         where custom_row.guild_id::text = btrim(p_guild_id)
           and custom_row.managed_by_dank = false;
    end if;

    update public.guild_configs gc
       set ticket_category_setup_required = false,
           ticket_category_setup_required_reason = null,
           ticket_category_setup_version = 2,
           ticket_category_setup_selected_keys = to_jsonb(selected_keys),
           ticket_category_setup_completed_at = now(),
           ticket_category_setup_completed_by_id = nullif(btrim(coalesce(p_actor_id, '')), ''),
           ticket_category_setup_completed_by_name = nullif(btrim(coalesce(p_actor_name, '')), ''),
           setup_completed = false,
           setup_completion_invalidated_at = now(),
           setup_completion_invalidated_reason = 'Ticket menu choices changed.',
           updated_at = now()
     where gc.guild_id::text = btrim(p_guild_id);

    return query
    select managed_row.managed_category_key,
           managed_row.slug,
           managed_row.name,
           managed_row.is_enabled,
           managed_row.is_default
      from public.ticket_categories managed_row
     where managed_row.guild_id::text = btrim(p_guild_id)
       and managed_row.managed_by_dank = true
       and managed_row.is_enabled = true
     order by managed_row.sort_order, managed_row.name;
end;
$$;

revoke all on function public.save_dank_ticket_category_selection(text, text[], text, text) from public, anon, authenticated;
grant execute on function public.save_dank_ticket_category_selection(text, text[], text, text) to service_role;
