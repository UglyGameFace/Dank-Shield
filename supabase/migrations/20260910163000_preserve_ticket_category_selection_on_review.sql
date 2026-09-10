-- Preserve the last owner-confirmed ticket-category selection when runtime
-- safety marks a guild's ticket menu as needing review, and make historical
-- Ticket Choices restores authoritative again.
--
-- Two old paths could strand a guild on the three starter choices:
-- 1. require_dank_ticket_category_setup(..., true) reduced live rows to the
--    starter trio AND erased ticket_category_setup_selected_keys.
-- 2. Backups & History restored ticket_categories rows but did not realign the
--    guild_configs selection/version fields, so the canonical service could
--    legitimately reduce the restored rows again on the next read.
--
-- Both repairs remain strictly per guild. Nothing here enables categories for
-- any guild other than the explicit p_guild_id supplied to the function.

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
           -- The live starter-row reset is temporary safety state. Preserve
           -- the last owner-confirmed selection as recovery evidence instead
           -- of destroying it. required=true/version=0 prevents these keys
           -- from being treated as active until the owner confirms setup again.
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

-- Upgrade the existing history restore RPC without duplicating its dynamic row
-- restoration implementation. On the first run, preserve the original function
-- under an internal rows-only name. The public RPC then wraps that atomic restore
-- and, in the same database transaction, derives the restored managed selection
-- from enabled rows and makes guild_configs agree with it.
do $migration$
begin
    if to_regprocedure('public.restore_ticket_categories_snapshot_rows_only(text,jsonb)') is null
       and to_regprocedure('public.restore_ticket_categories_snapshot(text,jsonb)') is not null then
        execute 'alter function public.restore_ticket_categories_snapshot(text, jsonb) rename to restore_ticket_categories_snapshot_rows_only';
    end if;

    -- Some deployments may not have the optional version-history migration yet.
    -- In that case, leave history unavailable rather than breaking bot startup.
    if to_regprocedure('public.restore_ticket_categories_snapshot_rows_only(text,jsonb)') is not null then
        execute $wrapper$
            create or replace function public.restore_ticket_categories_snapshot(
                p_guild_id text,
                p_rows jsonb
            )
            returns jsonb
            language plpgsql
            security definer
            set search_path = public, pg_temp
            as $body$
            declare
                restored jsonb;
                selected_keys text[];
                enabled_count integer;
            begin
                if nullif(btrim(p_guild_id), '') is null then
                    raise exception 'guild_id is required';
                end if;

                restored := public.restore_ticket_categories_snapshot_rows_only(
                    btrim(p_guild_id),
                    p_rows
                );

                select count(*)
                  into enabled_count
                  from public.ticket_categories tc
                 where tc.guild_id::text = btrim(p_guild_id)
                   and coalesce(tc.is_enabled, true) = true;

                if enabled_count < 1 then
                    raise exception 'ticket choice restore must leave at least one enabled category';
                end if;

                select coalesce(
                    array_agg(distinct resolved.category_key order by resolved.category_key),
                    array[]::text[]
                )
                  into selected_keys
                  from (
                    select coalesce(
                        case
                            when tc.managed_by_dank = true
                             and exists (
                                select 1
                                  from public.dank_ticket_category_catalog() catalog
                                 where catalog.category_key = tc.managed_category_key
                             )
                            then tc.managed_category_key
                            else null
                        end,
                        -- Historical pre-managed built-ins are recovered only
                        -- from reserved slugs. Passing NULL for the name keeps
                        -- an owner-created custom row named "Support" custom.
                        public.dank_ticket_category_key(tc.slug, null)
                    ) as category_key
                      from public.ticket_categories tc
                     where tc.guild_id::text = btrim(p_guild_id)
                       and coalesce(tc.is_enabled, true) = true
                  ) resolved
                 where resolved.category_key is not null
                   and exists (
                        select 1
                          from public.dank_ticket_category_catalog() catalog
                         where catalog.category_key = resolved.category_key
                   );

                update public.guild_configs gc
                   set ticket_category_setup_required = false,
                       ticket_category_setup_required_reason = null,
                       ticket_category_setup_version = 2,
                       ticket_category_setup_selected_keys = to_jsonb(selected_keys),
                       ticket_category_setup_completed_at = now(),
                       ticket_category_setup_completed_by_id = null,
                       ticket_category_setup_completed_by_name = 'Backups & History restore',
                       setup_completed = false,
                       setup_completion_invalidated_at = now(),
                       setup_completion_invalidated_reason = 'Ticket choices restored from history; review setup completion.',
                       updated_at = now()
                 where gc.guild_id::text = btrim(p_guild_id);

                if not found then
                    raise exception 'guild config not found for ticket choice restore';
                end if;

                return coalesce(restored, '{}'::jsonb)
                    || jsonb_build_object(
                        'selection_aligned', true,
                        'selected_keys', to_jsonb(selected_keys),
                        'enabled_count', enabled_count
                    );
            end;
            $body$;
        $wrapper$;

        execute 'revoke all on function public.restore_ticket_categories_snapshot(text, jsonb) from public, anon, authenticated';
        execute 'grant execute on function public.restore_ticket_categories_snapshot(text, jsonb) to service_role';
        execute 'revoke all on function public.restore_ticket_categories_snapshot_rows_only(text, jsonb) from public, anon, authenticated, service_role';
    end if;
end;
$migration$;
