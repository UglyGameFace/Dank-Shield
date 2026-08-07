-- ============================================================
-- DS-TICKET-CAT-022 preflight: release stale managed keys safely.
--
-- Historical rows can contain a valid managed_category_key that belongs to a
-- different reserved slug (for example slug=support, key=question). The partial
-- unique index then prevents the real Question row from reclaiming its key.
-- This preflight releases only those provably stale keys. It deletes nothing,
-- changes no enablement, and never touches non-managed custom rows.
-- ============================================================

create or replace function public.prepare_dank_ticket_category_repair(
    p_guild_id text default null
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    affected integer := 0;
begin
    update public.ticket_categories tc
       set managed_category_key = null,
           managed_catalog_version = null,
           updated_at = now()
     where tc.managed_by_dank = true
       and tc.managed_category_key is not null
       and (nullif(btrim(p_guild_id), '') is null or tc.guild_id::text = btrim(p_guild_id))
       and public.dank_ticket_category_key(tc.slug, null) is not null
       and public.dank_ticket_category_key(tc.slug, null) is distinct from
           trim(both '-' from regexp_replace(
               lower(btrim(coalesce(tc.managed_category_key, ''))),
               '[^a-z0-9]+', '-', 'g'
           ));

    get diagnostics affected = row_count;
    return affected;
end;
$$;

-- Release historical key swaps before the v3 repair migration runs. Safe to rerun.
select public.prepare_dank_ticket_category_repair(null);

revoke all on function public.prepare_dank_ticket_category_repair(text)
    from public, anon, authenticated;
grant execute on function public.prepare_dank_ticket_category_repair(text)
    to service_role;
