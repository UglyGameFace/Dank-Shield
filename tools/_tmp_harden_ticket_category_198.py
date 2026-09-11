from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    data = target.read_text(encoding="utf-8")
    count = data.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:100]!r}")
    target.write_text(data.replace(old, new, 1), encoding="utf-8")


def replace_count(path: str, old: str, new: str, expected: int) -> None:
    target = ROOT / path
    data = target.read_text(encoding="utf-8")
    count = data.count(old)
    if count != expected:
        raise SystemExit(f"{path}: expected {expected} matches, found {count}: {old[:100]!r}")
    target.write_text(data.replace(old, new), encoding="utf-8")


migration_path = "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql"
migration = ROOT / migration_path
data = migration.read_text(encoding="utf-8")
start_marker = "    -- Never overwrite surviving owner evidence.  Recovery exists only for\n"
end_marker = "    update public.ticket_categories tc\n"
start = data.find(start_marker)
end = data.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("migration recovery markers not found")
replacement = r'''    -- Surviving owner evidence is authoritative.  Validate it against the
    -- current catalog and use it to realign row flags; never replace it from
    -- history.  History is consulted only when the old reset erased the field.
    if jsonb_typeof(current_selected) = 'array'
       and jsonb_array_length(current_selected) > 0 then
        select coalesce(array_agg(distinct chosen.value order by chosen.value), array[]::text[])
          into recovered
          from jsonb_array_elements_text(current_selected) chosen(value)
         where exists (
            select 1
              from public.dank_ticket_category_catalog() catalog
             where catalog.category_key = chosen.value
         );

        if coalesce(array_length(recovered, 1), 0) < 1 then
            return array[]::text[];
        end if;
    else
        if to_regclass('public.guild_config_versions') is null then
            return recovered;
        end if;

        -- The old reset updated ticket rows and then wrote guild_configs in the
        -- same transaction.  PostgreSQL now() is transaction-stable, so every
        -- intermediate row-trigger snapshot from that destructive reset shares the
        -- reset timestamp.  Recover the last ticket snapshot strictly *before* it,
        -- not an intermediate partially-reset snapshot.
        select v.created_at
          into destructive_reset_at
          from public.guild_config_versions v
         where v.guild_id = btrim(p_guild_id)
           and v.config_table in ('guild_configs', 'guild_config')
           and coalesce((v.snapshot ->> 'ticket_category_setup_required')::boolean, false) = true
           and coalesce(v.snapshot ->> 'ticket_category_setup_required_reason', '')
               ilike '%previous setup enabled duplicate or excessive%'
         order by v.created_at desc, v.version_id desc
         limit 1;

        if destructive_reset_at is null then
            return recovered;
        end if;

        select v.snapshot -> 'rows'
          into historical_rows
          from public.guild_config_versions v
         where v.guild_id = btrim(p_guild_id)
           and v.config_table = 'ticket_categories'
           and v.created_at < destructive_reset_at
         order by v.created_at desc, v.version_id desc
         limit 1;

        if historical_rows is null then
            return recovered;
        end if;

        select coalesce(array_agg(distinct resolved.category_key order by resolved.category_key), array[]::text[])
          into recovered
          from jsonb_array_elements(coalesce(historical_rows, '[]'::jsonb)) item
         cross join lateral (
            select public.dank_ticket_category_key(item ->> 'slug', null) as category_key
         ) resolved
         where coalesce((item ->> 'is_enabled')::boolean, true) = true
           and resolved.category_key is not null
           and exists (
                select 1
                  from public.dank_ticket_category_catalog() catalog
                 where catalog.category_key = resolved.category_key
           );

        if coalesce(array_length(recovered, 1), 0) < 1 then
            return array[]::text[];
        end if;

        update public.guild_configs gc
           set ticket_category_setup_selected_keys = to_jsonb(recovered),
               updated_at = now()
         where gc.guild_id::text = btrim(p_guild_id)
           and coalesce(gc.ticket_category_setup_required, false) = true
           and coalesce(gc.ticket_category_setup_selected_keys, '[]'::jsonb) = '[]'::jsonb;
    end if;

'''
data = data[:start] + replacement + data[end:]
migration.write_text(data, encoding="utf-8")

workflow = ".github/workflows/ticket-category-repair-sql.yml"
replace_count(
    workflow,
    '      - "supabase/migrations/20260910163000_preserve_ticket_category_selection_on_review.sql"\n',
    '      - "supabase/migrations/20260910163000_preserve_ticket_category_selection_on_review.sql"\n      - "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql"\n',
    2,
)
replace_once(
    workflow,
    "            ('reset-guild'),\n            ('history-guild'),",
    "            ('reset-guild'),\n            ('erased-guild'),\n            ('history-guild'),",
)
replace_once(
    workflow,
    """          select * from public.save_dank_ticket_category_selection(
            'reset-guild', array['verification','bug','partnership']::text[], 'owner-3', 'Owner Three'
          );
          select * from public.save_dank_ticket_category_selection(
            'untouched-guild', array['account-access','support']::text[], 'owner-4', 'Owner Four'
          );
""",
    """          select * from public.save_dank_ticket_category_selection(
            'reset-guild', array['verification','bug','partnership']::text[], 'owner-3', 'Owner Three'
          );
          select * from public.save_dank_ticket_category_selection(
            'erased-guild', array['report','staff-complaint','cod-services','partnership','support']::text[], 'owner-legacy', 'Legacy Owner'
          );
          -- Reproduce the old destructive v2 review path before the preservation
          -- migration existed.  This erases selected_keys and collapses rows.
          select public.require_dank_ticket_category_setup(
            'erased-guild',
            'The previous setup enabled duplicate or excessive built-in ticket choices. Choose only what this server needs.',
            true
          );
          select * from public.save_dank_ticket_category_selection(
            'untouched-guild', array['account-access','support']::text[], 'owner-4', 'Owner Four'
          );
""",
)
replace_once(
    workflow,
    "      - name: Apply preflight, v3 repair, and selection preservation twice",
    "      - name: Apply preflight, v3 repair, preservation, and v4 recovery twice",
)
replace_once(
    workflow,
    """            psql -v ON_ERROR_STOP=1 -f supabase/migrations/20260807220000_repair_managed_ticket_category_duplicates.sql
            psql -v ON_ERROR_STOP=1 -f supabase/migrations/20260910163000_preserve_ticket_category_selection_on_review.sql
""",
    """            psql -v ON_ERROR_STOP=1 -f supabase/migrations/20260807220000_repair_managed_ticket_category_duplicates.sql
            psql -v ON_ERROR_STOP=1 -f supabase/migrations/20260910163000_preserve_ticket_category_selection_on_review.sql
            psql -v ON_ERROR_STOP=1 -f supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql
""",
)
replace_once(
    workflow,
    "                   (c.slug,c.name,c.name,c.description,c.intake_type,c.sort_order,3);",
    "                   (c.slug,c.name,c.name,c.description,c.intake_type,c.sort_order,4);",
)
replace_once(
    workflow,
    """            if live_keys <> array['appeal','report','support']::text[] then
              raise exception 'forced review did not use safe starters: %', live_keys;
            end if;
""",
    """            if live_keys <> array['bug','partnership','verification']::text[] then
              raise exception 'forced review did not preserve owner live selection: %', live_keys;
            end if;
""",
)

anchor = "      - name: Verify history restore realigns canonical guild selection\n"
insert = r'''      - name: Verify v4 rich recovery and legacy labels
        shell: bash
        run: |
          psql -v ON_ERROR_STOP=1 <<'SQL'
          do $$
          declare
            erased_live text[];
            erased_saved text[];
            erased_required boolean;
            reset_live text[];
            cod_name text;
            cod_description text;
            staff_name text;
            partnership_name text;
            untouched_keys text[];
          begin
            select array_agg(managed_category_key order by managed_category_key)
              into erased_live
              from public.ticket_categories
             where guild_id='erased-guild'
               and managed_by_dank=true
               and is_enabled=true;
            if erased_live <> array['cod-services','partnership','report','staff-complaint','support']::text[] then
              raise exception 'erased guild rich selection was not recovered: %', erased_live;
            end if;

            select ticket_category_setup_required,
                   (select array_agg(v order by v)
                      from jsonb_array_elements_text(ticket_category_setup_selected_keys) x(v))
              into erased_required, erased_saved
              from public.guild_configs
             where guild_id='erased-guild';
            if erased_required is not true then
              raise exception 'history recovery falsely completed owner review';
            end if;
            if erased_saved <> array['cod-services','partnership','report','staff-complaint','support']::text[] then
              raise exception 'erased guild saved selection not recovered: %', erased_saved;
            end if;

            -- A surviving saved selection must also realign the row flags.
            perform public.require_dank_ticket_category_setup(
              'reset-guild', 'CI v4 forced review', true
            );
            select array_agg(managed_category_key order by managed_category_key)
              into reset_live
              from public.ticket_categories
             where guild_id='reset-guild'
               and managed_by_dank=true
               and is_enabled=true;
            if reset_live <> array['bug','partnership','verification']::text[] then
              raise exception 'surviving saved selection was not kept live: %', reset_live;
            end if;

            select name, description into cod_name, cod_description
              from public.ticket_categories
             where guild_id='selected-guild'
               and managed_by_dank=true
               and managed_category_key='cod-services';
            select name into staff_name
              from public.ticket_categories
             where guild_id='selected-guild'
               and managed_by_dank=true
               and managed_category_key='staff-complaint';
            select name into partnership_name
              from public.ticket_categories
             where guild_id='selected-guild'
               and managed_by_dank=true
               and managed_category_key='partnership';
            if cod_name <> 'COD Modding Services'
               or position('Legacy Call of Duty modding services' in cod_description) = 0
               or position('Warzone' in cod_description) > 0 then
              raise exception 'COD legacy label/description drifted: % / %', cod_name, cod_description;
            end if;
            if staff_name <> 'Report Staff' then
              raise exception 'staff category label drifted: %', staff_name;
            end if;
            if partnership_name <> 'Partnerships' then
              raise exception 'partnership category label drifted: %', partnership_name;
            end if;

            select array_agg(managed_category_key order by managed_category_key)
              into untouched_keys
              from public.ticket_categories
             where guild_id='untouched-guild'
               and managed_by_dank=true
               and is_enabled=true;
            if untouched_keys <> array['account-access','support']::text[] then
              raise exception 'v4 recovery leaked across guilds: %', untouched_keys;
            end if;
          end $$;
          SQL

'''
replace_once(workflow, anchor, insert + anchor)

print("hardened v4 ticket-category SQL validation")
