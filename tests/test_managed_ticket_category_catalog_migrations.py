from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "supabase/migrations/202607310001_managed_ticket_category_catalog.sql"
AUTO_SYNC = ROOT / "supabase/migrations/202607310002_managed_ticket_category_auto_sync.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_catalog_has_all_current_managed_categories() -> None:
    sql = _sql(CATALOG)
    required = {
        "verification",
        "account-access",
        "payments-refunds",
        "appeal",
        "report",
        "staff-complaint",
        "bug",
        "cod-services",
        "service-request",
        "vouch-referral",
        "giveaway-reward",
        "content-media",
        "partnership",
        "question",
        "support",
    }
    for key in required:
        assert f"'{key}'" in sql


def test_known_legacy_aliases_are_canonicalized() -> None:
    sql = _sql(CATALOG)
    for alias in (
        "verification-help",
        "verification-issue",
        "bug-report",
        "technical-support",
        "custom",
        "other",
        "general-support",
    ):
        assert f"'{alias}'" in sql


def test_missing_ticket_category_table_is_bootstrapped() -> None:
    sql = _sql(CATALOG)
    assert "create table if not exists public.ticket_categories" in sql
    assert "alter table public.ticket_categories" in sql
    assert "add column if not exists managed_by_dank" in sql


def test_reconciliation_is_durable_and_idempotent() -> None:
    sql = _sql(CATALOG)
    assert "create or replace function public.reconcile_dank_ticket_categories" in sql
    assert "managed_by_dank" in sql
    assert "managed_catalog_version" in sql
    assert "managed_category_key" in sql
    assert "create unique index ticket_categories_guild_managed_key_uidx" in sql
    assert "select * from public.reconcile_dank_ticket_categories(null)" in sql


def test_custom_categories_are_not_matched_by_substrings() -> None:
    sql = _sql(CATALOG)
    assert "else null" in sql
    assert "like '%bug%'" not in sql
    assert "like '%technical%'" not in sql
    assert "like '%verification%'" not in sql


def test_custom_categories_are_outside_managed_uniqueness() -> None:
    sql = _sql(CATALOG)
    assert "where managed_by_dank = true and managed_category_key is not null" in sql


def test_duplicate_cleanup_is_limited_to_managed_or_exact_alias_rows() -> None:
    sql = _sql(CATALOG)
    assert "delete from public.ticket_categories tc" in sql
    assert "tc.managed_by_dank = true and tc.managed_category_key = c.category_key" in sql
    assert "public.dank_ticket_category_key(tc.slug, tc.name) = c.category_key" in sql
    assert "unknown custom rows return null" in sql


def test_new_guilds_receive_current_catalog_automatically() -> None:
    sql = _sql(AUTO_SYNC)
    assert "to_regclass('public.guild_configs') is not null" in sql
    assert "after insert on public.guild_configs" in sql
    assert "reconcile_dank_ticket_categories(new.guild_id::text)" in sql


def test_rpc_is_service_role_only() -> None:
    sql = _sql(CATALOG)
    assert "revoke all on function public.reconcile_dank_ticket_categories(text) from public, anon, authenticated" in sql
    assert "grant execute on function public.reconcile_dank_ticket_categories(text) to service_role" in sql
