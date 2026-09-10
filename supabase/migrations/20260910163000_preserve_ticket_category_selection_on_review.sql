-- Preserve the last owner-confirmed ticket-category selection when runtime
-- safety marks a guild's ticket menu as needing review.
--
-- Older require_dank_ticket_category_setup() behavior intentionally switched
-- the live managed rows to the small starter trio, but also erased
-- guild_configs.ticket_category_setup_selected_keys. That destroyed the only
-- lightweight authoritative hint the runtime could use to distinguish a
-- temporary safety reset from an owner's actual saved selection.
--
-- The live rows may still be reduced to the safe starter set while review is
-- required. The saved selection is now retained for recovery/history and is
-- ignored by reconciliation until setup is completed again because
-- ticket_category_setup_required=true and ticket_category_setup_version=0.

create or replace function public.require_dank_ticket_category_setup(
    p_guild_id text,
    p_reason text default 'Choose the ticket options this server actually uses.',
    p_reset_to_starter boolean default true
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    custom_exists boolean;
    custom_default_id text;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    perform * from public.reconcile_dank_ticket_categories(btrim(p_guild_id));

    if p_reset_to_starter then
        select exists (
            select 1
            from public.ticket_categories custom_row
            where custom_row.guild_id::text = btrim(p_guild_id)
              and custom_row.managed_by_dank = false
              and custom_row.is_enabled = true
        ) into custom_exists;

        update public.ticket_categories managed_row
           set is_enabled = (not custom_exists)
                            and managed_row.managed_category_key in ('report', 'appeal', 'support'),
               is_default = (not custom_exists)
                            and managed_row.managed_category_key = 'support',
               updated_at = now()
         where managed_row.guild_id::text = btrim(p_guild_id)
           and managed_row.managed_by_dank = true;

        if custom_exists then
            select custom_row.id::text
              into custom_default_id
              from public.ticket_categories custom_row
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false
               and custom_row.is_enabled = true
             order by
               case when custom_row.is_default then 0 else 1 end,
               coalesce(custom_row.sort_order, 9999),
               custom_row.created_at,
               custom_row.id
             limit 1;

            update public.ticket_categories custom_row
               set is_default = custom_row.id::text = custom_default_id,
                   updated_at = now()
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false;
        else
            update public.ticket_categories custom_row
               set is_default = false,
                   updated_at = now()
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false
               and custom_row.is_default = true;
        end if;
    end if;

    update public.guild_configs gc
       set ticket_category_setup_required = true,
           ticket_category_setup_required_reason = left(
               coalesce(nullif(btrim(p_reason), ''),
                        'Choose the ticket options this server actually uses.'),
               500
           ),
           ticket_category_setup_version = 0,
           -- Do NOT clear ticket_category_setup_selected_keys here. The live
           -- starter-row reset is temporary safety state, while these keys are
           -- the last owner-confirmed selection and remain useful for recovery.
           ticket_category_setup_selected_keys = gc.ticket_category_setup_selected_keys,
           setup_completed = false,
           setup_completion_invalidated_at = now(),
           setup_completion_invalidated_reason = 'Ticket menu setup requires confirmation.',
           updated_at = now()
     where gc.guild_id::text = btrim(p_guild_id);
end;
$$;

revoke all on function public.require_dank_ticket_category_setup(text, text, boolean)
    from public, anon, authenticated;
grant execute on function public.require_dank_ticket_category_setup(text, text, boolean)
    to service_role;
