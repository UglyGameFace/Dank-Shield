-- Preserve existing owner-built ticket menus when the v2 catalog is installed.
--
-- The v2 catalog must be globally available, but a guild that already had only
-- unknown/custom categories must not suddenly show the temporary managed
-- Report/Appeal/Support starter set. Keep those custom choices live, disable the
-- newly inserted managed starter rows, and require the owner to explicitly pick
-- any built-in categories they want.

with custom_existing_guilds as (
    select tc.guild_id::text as guild_id
    from public.ticket_categories tc
    join public.guild_configs gc
      on gc.guild_id::text = tc.guild_id::text
    group by tc.guild_id::text, gc.ticket_category_setup_version,
             gc.ticket_category_setup_required
    having coalesce(gc.ticket_category_setup_version, 0) < 2
       and coalesce(gc.ticket_category_setup_required, false) = false
       and count(*) filter (where tc.managed_by_dank = false) > 0
       and count(*) filter (where tc.managed_by_dank = true and tc.is_enabled = true) = 3
       and count(*) filter (
            where tc.managed_by_dank = true
              and tc.is_enabled = true
              and tc.managed_category_key not in ('report', 'appeal', 'support')
       ) = 0
)
update public.ticket_categories tc
   set is_enabled = false,
       is_default = false,
       updated_at = now()
  from custom_existing_guilds target
 where tc.guild_id::text = target.guild_id
   and tc.managed_by_dank = true;

-- Keep routing deterministic for a custom menu that did not previously name a
-- fallback. Existing custom defaults are preserved.
with needs_custom_default as (
    select tc.guild_id::text as guild_id
    from public.ticket_categories tc
    join public.guild_configs gc
      on gc.guild_id::text = tc.guild_id::text
    group by tc.guild_id::text, gc.ticket_category_setup_version,
             gc.ticket_category_setup_required
    having coalesce(gc.ticket_category_setup_version, 0) < 2
       and coalesce(gc.ticket_category_setup_required, false) = false
       and count(*) filter (where tc.managed_by_dank = false and tc.is_enabled = true) > 0
       and count(*) filter (where tc.is_enabled = true and tc.is_default = true) = 0
), chosen as (
    select distinct on (tc.guild_id::text)
           tc.guild_id::text as guild_id,
           tc.id
      from public.ticket_categories tc
      join needs_custom_default needed
        on needed.guild_id = tc.guild_id::text
     where tc.managed_by_dank = false
       and tc.is_enabled = true
     order by tc.guild_id::text, coalesce(tc.sort_order, 9999), tc.created_at, tc.id
)
update public.ticket_categories tc
   set is_default = true,
       updated_at = now()
  from chosen
 where tc.id = chosen.id;

update public.guild_configs gc
   set ticket_category_setup_required = true,
       ticket_category_setup_required_reason =
           'Your custom ticket choices were preserved. Confirm whether this server also wants any built-in Dank Shield choices.',
       ticket_category_setup_version = 0,
       updated_at = now()
 where coalesce(gc.ticket_category_setup_version, 0) < 2
   and coalesce(gc.ticket_category_setup_required, false) = false
   and exists (
       select 1
       from public.ticket_categories custom_row
       where custom_row.guild_id::text = gc.guild_id::text
         and custom_row.managed_by_dank = false
   )
   and not exists (
       select 1
       from public.ticket_categories managed_enabled
       where managed_enabled.guild_id::text = gc.guild_id::text
         and managed_enabled.managed_by_dank = true
         and managed_enabled.is_enabled = true
   );
