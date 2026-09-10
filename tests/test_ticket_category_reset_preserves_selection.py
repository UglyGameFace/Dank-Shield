from __future__ import annotations

import inspect
from pathlib import Path

from stoney_verify.startup_guards import ticket_category_schema_bootstrap_guard as schema_guard
from stoney_verify.tickets_new import managed_category_service as categories


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260910163000_preserve_ticket_category_selection_on_review.sql"


def test_preservation_migration_keeps_last_owner_selection() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "create or replace function public.require_dank_ticket_category_setup" in text
    assert "ticket_category_setup_selected_keys = gc.ticket_category_setup_selected_keys" in text
    assert "ticket_category_setup_version = 0" in text
    assert "ticket_category_setup_required = true" in text
    assert "managed_row.managed_category_key in ('report', 'appeal', 'support')" in text

    assignment = text.split("update public.guild_configs gc", 1)[1]
    assert "when p_reset_to_starter then '[]'::jsonb" not in assignment


def test_preservation_migration_stays_per_guild_and_service_role_only() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "where gc.guild_id::text = btrim(p_guild_id)" in text
    assert "where managed_row.guild_id::text = btrim(p_guild_id)" in text
    assert "where custom_row.guild_id::text = btrim(p_guild_id)" in text
    assert "revoke all on function public.require_dank_ticket_category_setup" in text
    assert "grant execute on function public.require_dank_ticket_category_setup" in text
    assert "to service_role" in text


def test_direct_dsn_bootstrap_registers_preservation_after_repairs() -> None:
    assert schema_guard.PRESERVE_SELECTION_MIGRATION_FILE == MIGRATION.name
    files = list(schema_guard.MIGRATION_FILES)
    assert files[-1] == schema_guard.PRESERVE_SELECTION_MIGRATION_FILE
    assert files.index(schema_guard.REPAIR_MIGRATION_FILE) < files.index(
        schema_guard.PRESERVE_SELECTION_MIGRATION_FILE
    )


def test_python_fallback_already_preserves_saved_selection() -> None:
    source = inspect.getsource(categories._mark_required_fallback_sync)
    assert '"ticket_category_setup_version": 0' in source
    assert "ticket_category_setup_selected_keys" not in source


def test_completed_selection_repair_still_requires_completed_setup() -> None:
    rows = categories.catalog_category_rows()
    cfg = {
        "ticket_category_setup_version": 0,
        "ticket_category_setup_required": True,
        "ticket_category_setup_selected_keys": ["verification", "bug", "support"],
    }
    assert categories._configured_selected_keys(cfg) == (
        "verification",
        "bug",
        "support",
    )
    assert categories._saved_selection_reconcile_needed(rows, cfg) is False


def test_history_restore_wrapper_realigns_selection_and_setup_state() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "restore_ticket_categories_snapshot_rows_only" in text
    assert "create or replace function public.restore_ticket_categories_snapshot" in text
    assert "ticket_category_setup_required = false" in text
    assert "ticket_category_setup_version = 2" in text
    assert "ticket_category_setup_selected_keys = to_jsonb(selected_keys)" in text
    assert "selection_aligned" in text
    assert "enabled_count" in text
    assert "ticket choice restore must leave at least one enabled category" in text


def test_history_restore_derives_builtins_from_slug_not_custom_name() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "public.dank_ticket_category_key(tc.slug, null)" in text
    assert "public.dank_ticket_category_key(tc.slug, tc.name)" not in text
    assert 'owner-created custom row named "Support" custom' in text


def test_history_restore_is_atomic_per_guild_and_private() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "restored := public.restore_ticket_categories_snapshot_rows_only" in text
    assert "where tc.guild_id::text = btrim(p_guild_id)" in text
    assert "where gc.guild_id::text = btrim(p_guild_id)" in text
    assert "guild config not found for ticket choice restore" in text
    assert "revoke all on function public.restore_ticket_categories_snapshot(text, jsonb)" in text
    assert "grant execute on function public.restore_ticket_categories_snapshot(text, jsonb) to service_role" in text
    assert "revoke all on function public.restore_ticket_categories_snapshot_rows_only" in text


def test_history_upgrade_is_idempotent_and_optional_when_history_not_installed() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert "to_regprocedure('public.restore_ticket_categories_snapshot_rows_only(text,jsonb)') is null" in text
    assert "to_regprocedure('public.restore_ticket_categories_snapshot(text,jsonb)') is not null" in text
    assert "if to_regprocedure('public.restore_ticket_categories_snapshot_rows_only(text,jsonb)') is not null then" in text
