from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql"


def replace_once(old: str, new: str) -> None:
    data = PATH.read_text(encoding="utf-8")
    count = data.count(old)
    if count != 1:
        raise SystemExit(f"migration: expected one match, found {count}: {old[:100]!r}")
    PATH.write_text(data.replace(old, new, 1), encoding="utf-8")


replace_once(
    "    historical_rows jsonb;\n    recovered text[] := array[]::text[];",
    "    historical_rows jsonb;\n    destructive_reset_at timestamptz;\n    recovered text[] := array[]::text[];",
)

old_history = '''    select v.snapshot -> 'rows'
      into historical_rows
      from public.guild_config_versions v
     where v.guild_id = btrim(p_guild_id)
       and v.config_table = 'ticket_categories'
       and exists (
            select 1
              from jsonb_array_elements(coalesce(v.snapshot -> 'rows', '[]'::jsonb)) item
             cross join lateral (
                select public.dank_ticket_category_key(item ->> 'slug', null) as category_key
             ) resolved
             where coalesce((item ->> 'is_enabled')::boolean, true) = true
               and resolved.category_key is not null
               and resolved.category_key not in ('report', 'appeal', 'support')
       )
     order by v.created_at desc, v.version_id desc
     limit 1;

    if historical_rows is null then
        return recovered;
    end if;
'''
new_history = '''    -- The old reset updated ticket rows and then wrote guild_configs in the
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
'''
replace_once(old_history, new_history)

replace_once(
    '''            if preserved_default is not null then
                update public.ticket_categories managed_row
                   set is_default = managed_row.managed_category_key = preserved_default,
                       updated_at = now()
                 where managed_row.guild_id::text = btrim(p_guild_id)
                   and managed_row.managed_by_dank = true
                   and managed_row.is_enabled = true;
            end if;
        else
''',
    '''            if preserved_default is not null then
                update public.ticket_categories managed_row
                   set is_default = managed_row.managed_category_key = preserved_default,
                       updated_at = now()
                 where managed_row.guild_id::text = btrim(p_guild_id)
                   and managed_row.managed_by_dank = true
                   and managed_row.is_enabled = true;

                update public.ticket_categories custom_row
                   set is_default = false,
                       updated_at = now()
                 where custom_row.guild_id::text = btrim(p_guild_id)
                   and custom_row.managed_by_dank = false
                   and custom_row.is_default = true;
            end if;
        else
''',
)

replace_once(
    "-- by the older reset function, it may recover the latest richer enabled set\n-- from that same guild's ticket_categories version history.",
    "-- by the older reset function, it recovers the last ticket-category snapshot\n-- from before that exact destructive-reset transaction.",
)

print("hardened DS-TICKET-CAT-037 history recovery")
