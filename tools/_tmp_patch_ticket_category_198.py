from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    data = target.read_text(encoding="utf-8")
    count = data.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:80]!r}")
    target.write_text(data.replace(old, new, 1), encoding="utf-8")


service = "stoney_verify/tickets_new/managed_category_service.py"
replace_once(service, "MANAGED_CATALOG_VERSION = 3", "MANAGED_CATALOG_VERSION = 4")
replace_once(
    service,
    '''    {
        "category_key": "staff-complaint",
        "slug": "staff_complaint",
        "name": "Staff Complaint",
        "description": "Complaints or escalation requests involving staff or moderator behavior.",
        "intake_type": "report",
        "sort_order": 60,
        "is_default": False,
    },''',
    '''    {
        "category_key": "staff-complaint",
        "slug": "staff_complaint",
        "name": "Report Staff",
        "description": "Report or escalate staff, moderator, or administrator behavior.",
        "intake_type": "report",
        "sort_order": 60,
        "is_default": False,
    },''',
)
replace_once(
    service,
    '''    {
        "category_key": "cod-services",
        "slug": "cod_services",
        "name": "COD Services",
        "description": "Call of Duty, Warzone, Zombies, lobby, account, unlock, or service questions.",
        "intake_type": "cod_services",
        "sort_order": 80,
        "is_default": False,
    },''',
    '''    {
        "category_key": "cod-services",
        "slug": "cod_services",
        "name": "COD Modding Services",
        "description": "Legacy Call of Duty modding services for older titles: modded/challenge lobbies, unlocks, Zombies, recoveries, RGH/JTAG, and related help.",
        "intake_type": "cod_services",
        "sort_order": 80,
        "is_default": False,
    },''',
)
replace_once(
    service,
    '''    "cod-services": "cod-services",
    "cod-service": "cod-services",
    "call-of-duty": "cod-services",
    "call-of-duty-services": "cod-services",''',
    '''    "cod-services": "cod-services",
    "cod-service": "cod-services",
    "cod-modding": "cod-services",
    "cod-modding-services": "cod-services",
    "legacy-cod-modding": "cod-services",
    "call-of-duty": "cod-services",
    "call-of-duty-services": "cod-services",''',
)
replace_once(
    service,
    '''def _state_from_rows(
    cfg: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> CategorySetupState:
    active = dedupe_category_rows(rows, enabled_only=True, fallback=True)
    selected = tuple(
        canonical_category_key(row)
        for row in active
        if _managed(row) and canonical_category_key(row) in _CATALOG_BY_KEY
    )
    return CategorySetupState(
        rows=dedupe_category_rows(rows, enabled_only=False, fallback=False),
        active_rows=active,
        selected_keys=tuple(dict.fromkeys(selected)),
        required=_config_required(cfg),
        reason=_config_reason(cfg),
        version=_safe_int(_row_value(cfg, "ticket_category_setup_version", 0), 0),
    )
''',
    '''def _state_from_rows(
    cfg: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> CategorySetupState:
    all_rows = dedupe_category_rows(rows, enabled_only=False, fallback=False)
    required = _config_required(cfg)
    preserved = _configured_selected_keys(cfg) if required else ()

    # A safety review may temporarily change persisted row enablement, but it
    # must not erase a previously owner-confirmed member menu.  PR #193 began
    # preserving those keys in guild_configs; use them while review is pending.
    if preserved:
        selected_set = set(preserved)
        projected: List[Dict[str, Any]] = []
        for row in all_rows:
            key = canonical_category_key(row)
            if _managed(row):
                if key not in selected_set:
                    continue
                restored = dict(row)
                restored["is_enabled"] = True
                projected.append(restored)
            elif _row_enabled(row):
                projected.append(dict(row))
        active = dedupe_category_rows(projected, enabled_only=True, fallback=True)
        selected = tuple(
            key for key in preserved
            if any(
                _managed(row) and canonical_category_key(row) == key
                for row in active
            )
        )
    else:
        active = dedupe_category_rows(rows, enabled_only=True, fallback=True)
        selected = tuple(
            canonical_category_key(row)
            for row in active
            if _managed(row) and canonical_category_key(row) in _CATALOG_BY_KEY
        )

    return CategorySetupState(
        rows=all_rows,
        active_rows=active,
        selected_keys=tuple(dict.fromkeys(selected)),
        required=required,
        reason=_config_reason(cfg),
        version=_safe_int(_row_value(cfg, "ticket_category_setup_version", 0), 0),
    )
''',
)

forms = "stoney_verify/startup_guards/ticket_form_default_templates_guard.py"
replace_once(
    forms,
    '''            "placeholder": "BO2, BO3, MWIII, BO6, BO7, Warzone, Zombies, etc.",''',
    '''            "placeholder": "BO1, BO2, BO3, WaW, MW2, MW3, Ghosts, Zombies, etc.",''',
)
replace_once(
    forms,
    '''            "label": "What COD question or service do you need help with?",
            "placeholder": "Describe what you need. Do not include passwords or private credentials.",''',
    '''            "label": "What legacy COD modding service do you need?",
            "placeholder": "Modded/challenge lobby, unlocks, Zombies, recovery, RGH/JTAG, or related legacy-title help. Do not include passwords or private credentials.",''',
)

schema = "stoney_verify/startup_guards/ticket_category_schema_bootstrap_guard.py"
replace_once(
    schema,
    '''PRESERVE_SELECTION_MIGRATION_FILE = "20260910163000_preserve_ticket_category_selection_on_review.sql"
MIGRATION_FILES = (
    MIGRATION_FILE,
    REPAIR_PREP_MIGRATION_FILE,
    REPAIR_MIGRATION_FILE,
    PRESERVE_SELECTION_MIGRATION_FILE,
)''',
    '''PRESERVE_SELECTION_MIGRATION_FILE = "20260910163000_preserve_ticket_category_selection_on_review.sql"
RICH_SELECTION_RECOVERY_MIGRATION_FILE = "20260911113000_restore_rich_ticket_category_selection.sql"
MIGRATION_FILES = (
    MIGRATION_FILE,
    REPAIR_PREP_MIGRATION_FILE,
    REPAIR_MIGRATION_FILE,
    PRESERVE_SELECTION_MIGRATION_FILE,
    RICH_SELECTION_RECOVERY_MIGRATION_FILE,
)''',
)
replace_once(
    schema,
    '''            "ticket category selection v2 + stale-key preflight + managed catalog repair v3 + selection-preservation repair registered for direct-DSN startup"''',
    '''            "ticket category selection v2 + stale-key preflight + managed catalog repair v4 + selection-preservation/history recovery registered for direct-DSN startup"''',
)
replace_once(
    schema,
    '''    "PRESERVE_SELECTION_MIGRATION_FILE",
    "MIGRATION_FILES",''',
    '''    "PRESERVE_SELECTION_MIGRATION_FILE",
    "RICH_SELECTION_RECOVERY_MIGRATION_FILE",
    "MIGRATION_FILES",''',
)

# Keep the static audit aligned with the canonical v4 catalog and migration.
audit = "tools/audit_ticket_category_menu.py"
replace_once(audit, '"MANAGED_CATALOG_VERSION = 3",', '"MANAGED_CATALOG_VERSION = 4",')
replace_once(
    audit,
    '''    "supabase/migrations/20260807220000_repair_managed_ticket_category_duplicates.sql",
]''',
    '''    "supabase/migrations/20260807220000_repair_managed_ticket_category_duplicates.sql",
    "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql",
]''',
)
replace_once(
    audit,
    '''        'REPAIR_MIGRATION_FILE = "20260807220000_repair_managed_ticket_category_duplicates.sql"',
        "MIGRATION_FILES",
        "stale-key preflight + managed catalog repair v3",''',
    '''        'REPAIR_MIGRATION_FILE = "20260807220000_repair_managed_ticket_category_duplicates.sql"',
        'RICH_SELECTION_RECOVERY_MIGRATION_FILE = "20260911113000_restore_rich_ticket_category_selection.sql"',
        "MIGRATION_FILES",
        "managed catalog repair v4",''',
)
insert_anchor = '''    "supabase/migrations/20260807220000_repair_managed_ticket_category_duplicates.sql": [
        "catalog_version integer",
        "false,3)",
        "dank_ticket_category_repair_key",
        "A reserved slug is stronger evidence than a stale stored key",
        "Unknown custom slugs are never adopted from display name",
        "use_saved_selection",
        "cfg_version >= 2 and cfg_required = false",
        "Repair every existing guild immediately",
        "reconcile_dank_ticket_categories(null)",
        "completed v2 selection is never invalidated",
    ],
'''
if insert_anchor not in (ROOT / audit).read_text(encoding="utf-8"):
    raise SystemExit("audit migration anchor missing")
replace_once(
    audit,
    insert_anchor,
    insert_anchor + '''    "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql": [
        "COD Modding Services",
        "Report Staff",
        "recover_dank_ticket_category_selection_from_history",
        "guild_config_versions",
        "ticket_category_setup_selected_keys",
        "reconcile_dank_ticket_categories(null)",
    ],
''',
)

# Update and extend behavioral regression coverage.
tests = "tests/test_ticket_category_setup_selection.py"
replace_once(
    tests,
    '''    assert categories.MANAGED_CATALOG_VERSION == 3
    rows = categories.catalog_category_rows()
    assert {row["managed_catalog_version"] for row in rows} == {3}''',
    '''    assert categories.MANAGED_CATALOG_VERSION == 4
    rows = categories.catalog_category_rows()
    assert {row["managed_catalog_version"] for row in rows} == {4}''',
)
test_data = (ROOT / tests).read_text(encoding="utf-8")
append_marker = "\ndef test_review_state_restores_preserved_owner_selection() -> None:\n"
if append_marker not in test_data:
    test_data += '''\n\ndef test_review_state_restores_preserved_owner_selection() -> None:\n    rows = categories.catalog_category_rows()\n    for row in rows:\n        key = categories.canonical_category_key(row)\n        row["is_enabled"] = key in set(categories.SAFE_STARTER_KEYS)\n        row["is_default"] = key == "support"\n\n    cfg = {\n        "ticket_category_setup_required": True,\n        "ticket_category_setup_version": 0,\n        "ticket_category_setup_selected_keys": [\n            "report",\n            "staff-complaint",\n            "cod-services",\n            "partnership",\n            "support",\n        ],\n    }\n    state = categories._state_from_rows(cfg, rows)\n\n    assert state.required is True\n    assert state.selected_keys == (\n        "report",\n        "staff-complaint",\n        "cod-services",\n        "partnership",\n        "support",\n    )\n    assert set(_keys(state.active_rows)) == set(state.selected_keys)\n    assert {row["name"] for row in state.active_rows} >= {\n        "Report a Member",\n        "Report Staff",\n        "COD Modding Services",\n        "Partnerships",\n        "Support",\n    }\n\n\ndef test_review_state_without_preserved_selection_stays_on_safe_starter() -> None:\n    rows = categories.catalog_category_rows()\n    cfg = {\n        "ticket_category_setup_required": True,\n        "ticket_category_setup_version": 0,\n        "ticket_category_setup_selected_keys": [],\n    }\n    state = categories._state_from_rows(cfg, rows)\n    assert set(_keys(state.active_rows)) == set(categories.SAFE_STARTER_KEYS)\n\n\ndef test_catalog_restores_legacy_coding_and_staff_labels() -> None:\n    rows = {row["category_key"]: row for row in categories.CATEGORY_CATALOG}\n    assert rows["staff-complaint"]["name"] == "Report Staff"\n    assert rows["cod-services"]["name"] == "COD Modding Services"\n    cod_description = rows["cod-services"]["description"].lower()\n    assert "legacy" in cod_description\n    assert "rgh/jtag" in cod_description\n    assert "warzone" not in cod_description\n    assert rows["partnership"]["name"] == "Partnerships"\n    assert rows["report"]["name"] == "Report a Member"\n\n\ndef test_cod_default_form_is_legacy_modding_specific() -> None:\n    questions = forms.DEFAULT_TEMPLATES["cod"]\n    joined = " ".join(str(item.get("placeholder") or "") for item in questions).lower()\n    labels = " ".join(str(item.get("label") or "") for item in questions).lower()\n    assert "bo2" in joined and "bo3" in joined and "waw" in joined\n    assert "rgh/jtag" in joined\n    assert "warzone" not in joined\n    assert "modding service" in labels\n'''
    (ROOT / tests).write_text(test_data, encoding="utf-8")

migration = ROOT / "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql"
migration.write_text(r'''-- ============================================================
-- DS-TICKET-CAT-037 — restore rich ticket choices through review.
--
-- Fixes two production regressions:
-- 1. guilds put into category review could be stranded on the three starter
--    rows even though a prior owner-confirmed selection was preserved;
-- 2. historical rich category intent drifted into generic labels, notably
--    COD Services (expanded to modern titles) and Staff Complaint.
--
-- This migration never invents a per-guild selection.  It first trusts an
-- already-preserved guild_configs selection.  If that evidence was destroyed
-- by the older reset function, it may recover the latest richer enabled set
-- from that same guild's ticket_categories version history.  Guild isolation
-- remains strict throughout.
-- ============================================================

create or replace function public.dank_ticket_category_catalog()
returns table(
    category_key text, slug text, name text, description text,
    intake_type text, match_keywords jsonb, sort_order integer,
    is_default boolean, catalog_version integer
)
language sql
immutable
as $$ values
('verification','verification_issue','Verification','Help with verification or approval issues.','verification','["verification","verify","unverified","secure upload","vc verify","selfie","approval"]'::jsonb,10,false,4),
('account-access','account_access','Account / Access','Account access, login, hacked account, email, password, and 2FA issues.','account','["account","login","email","password","2fa","locked out","hacked","compromised"]'::jsonb,20,false,4),
('payments-refunds','payments_refunds','Payments / Refunds','Payments, orders, receipts, invoices, refunds, and chargebacks.','purchase','["payment","purchase","refund","chargeback","receipt","invoice","order"]'::jsonb,30,false,4),
('appeal','appeal','Appeal','Appeal a moderation action or access restriction.','appeal','["appeal","ban appeal","unban","kick appeal","timeout appeal","warn appeal"]'::jsonb,40,false,4),
('report','report','Report a Member','Report a member, scam, harassment, abuse, spam, raid, or rule violation.','report','["report","scam","abuse","harassment","threat","raid","spam","rule break"]'::jsonb,50,false,4),
('staff-complaint','staff_complaint','Report Staff','Report or escalate staff, moderator, or administrator behavior.','report','["report staff","staff complaint","staff issue","staff abuse","moderator report","admin report"]'::jsonb,60,false,4),
('bug','technical_support','Bug / Technical Support','Site bugs, panel problems, bot issues, broken flows, and technical failures.','bug','["bug","broken","not working","error","glitch","failed","technical support"]'::jsonb,70,false,4),
('cod-services','cod_services','COD Modding Services','Legacy Call of Duty modding services for older titles: modded/challenge lobbies, unlocks, Zombies, recoveries, RGH/JTAG, and related help.','cod_services','["cod modding","legacy cod","bo1","bo2","bo3","waw","mw2","mw3","ghosts","zombies","modded lobby","challenge lobby","unlock all","recovery","rgh","jtag"]'::jsonb,80,false,4),
('game-services','game_services','Game Services','Route general or current-game service questions to the right staff.','game_services','["game services","game help","account help","lobby help","platform support"]'::jsonb,90,false,4),
('service-request','service_request','Service Requests','General service requests, carries, boosts, recoveries, and fulfillment questions.','custom','["service","boost","carry","recovery service","unlock service","rank help"]'::jsonb,100,false,4),
('vouch-referral','vouch_referral','Vouch / Invite / Referral','Invite credit, referral rewards, vouch issues, and who-invited-who questions.','custom','["vouch","invite","invite credit","referral","referrer","invite reward"]'::jsonb,110,false,4),
('giveaway-reward','giveaway_reward','Giveaway / Reward Issues','Giveaway prizes, missing rewards, winner disputes, and reward claims.','custom','["giveaway","reward","prize","claim prize","missing prize","winner issue"]'::jsonb,120,false,4),
('content-media','content_media','Content / Media Requests','Graphics, thumbnails, banners, content requests, media edits, and promo assets.','custom','["content","media","graphic","design","editing","video","thumbnail","banner"]'::jsonb,130,false,4),
('partnership','partnership','Partnerships','Partnerships, sponsorships, collaborations, and promotions.','partnership','["partnership","partner","collab","collaboration","sponsor","promotion"]'::jsonb,140,false,4),
('question','question','Other Question','Ask something that does not fit the other options.','question','["question","questions","how to","how do i"]'::jsonb,150,false,4),
('support','support','Support','General help from staff.','general','["support","help","general support","assistance"]'::jsonb,999,true,4);
$$;

-- The v3 reconciler reads the catalog dynamically, so changing the catalog
-- function is sufficient to materialize the v4 labels/keywords without adding
-- another competing reconciliation owner.
select * from public.reconcile_dank_ticket_categories(null);

create or replace function public.recover_dank_ticket_category_selection_from_history(
    p_guild_id text
)
returns text[]
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    current_selected jsonb := '[]'::jsonb;
    historical_rows jsonb;
    recovered text[] := array[]::text[];
    default_key text;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    select coalesce(gc.ticket_category_setup_selected_keys, '[]'::jsonb)
      into current_selected
      from public.guild_configs gc
     where gc.guild_id::text = btrim(p_guild_id)
       and coalesce(gc.ticket_category_setup_required, false) = true
     limit 1;

    if not found then
        return recovered;
    end if;

    -- Never overwrite surviving owner evidence.  Recovery exists only for
    -- guilds whose older safety reset already erased the selection field.
    if jsonb_typeof(current_selected) = 'array'
       and jsonb_array_length(current_selected) > 0 then
        return recovered;
    end if;

    if to_regclass('public.guild_config_versions') is null then
        return recovered;
    end if;

    select v.snapshot -> 'rows'
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

    update public.ticket_categories tc
       set is_enabled = tc.managed_category_key = any(recovered),
           is_default = false,
           updated_at = now()
     where tc.guild_id::text = btrim(p_guild_id)
       and tc.managed_by_dank = true;

    if 'support' = any(recovered) then
        default_key := 'support';
    else
        select catalog.category_key
          into default_key
          from public.dank_ticket_category_catalog() catalog
         where catalog.category_key = any(recovered)
         order by catalog.sort_order, catalog.category_key
         limit 1;
    end if;

    if default_key is not null then
        update public.ticket_categories tc
           set is_default = tc.managed_category_key = default_key,
               updated_at = now()
         where tc.guild_id::text = btrim(p_guild_id)
           and tc.managed_by_dank = true
           and tc.is_enabled = true;
    end if;

    return recovered;
end;
$$;

revoke all on function public.recover_dank_ticket_category_selection_from_history(text)
    from public, anon, authenticated;
grant execute on function public.recover_dank_ticket_category_selection_from_history(text)
    to service_role;

-- Repair already-stranded guilds once.  The function refuses to overwrite any
-- surviving selection, so this is safe and idempotent on repeated migration runs.
select public.recover_dank_ticket_category_selection_from_history(gc.guild_id::text)
  from public.guild_configs gc
 where coalesce(gc.ticket_category_setup_required, false) = true;

-- Future forced reviews keep a surviving selection visible instead of
-- re-collapsing the row flags to the starter trio.
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
    preserved_keys text[] := array[]::text[];
    preserved_default text;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    perform * from public.reconcile_dank_ticket_categories(btrim(p_guild_id));

    select coalesce(array_agg(chosen.value order by chosen.value), array[]::text[])
      into preserved_keys
      from public.guild_configs gc
      cross join lateral jsonb_array_elements_text(
        coalesce(gc.ticket_category_setup_selected_keys, '[]'::jsonb)
      ) chosen(value)
     where gc.guild_id::text = btrim(p_guild_id)
       and exists (
            select 1 from public.dank_ticket_category_catalog() catalog
             where catalog.category_key = chosen.value
       );

    if p_reset_to_starter then
        select exists (
            select 1
              from public.ticket_categories custom_row
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false
               and custom_row.is_enabled = true
        ) into custom_exists;

        if coalesce(array_length(preserved_keys, 1), 0) > 0 then
            update public.ticket_categories managed_row
               set is_enabled = managed_row.managed_category_key = any(preserved_keys),
                   is_default = false,
                   updated_at = now()
             where managed_row.guild_id::text = btrim(p_guild_id)
               and managed_row.managed_by_dank = true;

            if 'support' = any(preserved_keys) then
                preserved_default := 'support';
            else
                select catalog.category_key
                  into preserved_default
                  from public.dank_ticket_category_catalog() catalog
                 where catalog.category_key = any(preserved_keys)
                 order by catalog.sort_order, catalog.category_key
                 limit 1;
            end if;

            if preserved_default is not null then
                update public.ticket_categories managed_row
                   set is_default = managed_row.managed_category_key = preserved_default,
                       updated_at = now()
                 where managed_row.guild_id::text = btrim(p_guild_id)
                   and managed_row.managed_by_dank = true
                   and managed_row.is_enabled = true;
            end if;
        else
            update public.ticket_categories managed_row
               set is_enabled = (not custom_exists)
                                and managed_row.managed_category_key in ('report', 'appeal', 'support'),
                   is_default = (not custom_exists)
                                and managed_row.managed_category_key = 'support',
                   updated_at = now()
             where managed_row.guild_id::text = btrim(p_guild_id)
               and managed_row.managed_by_dank = true;
        end if;

        if custom_exists and coalesce(array_length(preserved_keys, 1), 0) = 0 then
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
        elsif not custom_exists then
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
''', encoding="utf-8")

print("patched DS-TICKET-CAT-037 rich selection recovery")
