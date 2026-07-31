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
        "verification_issue",
        "bug-report",
        "technical_support",
        "custom",
        "other",
        "general-support",
    ):
        assert f"'{alias}'" in sql


def test_reconciliation_is_durable_and_idempotent() -> None:
    sql = _sql(CATALOG)
    assert "create or replace function public.reconcile_dank_ticket_categories" in sql
    assert "managed_by_dank" in sql
    assert "managed_catalog_version" in sql
    assert "managed_category_key" in sql
    assert "create unique index if not exists ticket_categories_guild_managed_key_uidx" in sql
    assert "select * from public.reconcile_dank_ticket_categories(null)" in sql


def test_custom_categories_are_not_blanket_deleted() -> None:
    sql = _sql(CATALOG)
    assert "public.dank_ticket_category_key(tc.slug,tc.name)=c.category_key" in sql
    assert "delete from public.ticket_categories" in sql
    assert "delete from public.ticket_categories where guild_id" not in sql


def test_new_guilds_receive_current_catalog_automatically() -> None:
    sql = _sql(AUTO_SYNC)
    assert "after insert on public.guild_configs" in sql
    assert "reconcile_dank_ticket_categories(new.guild_id::text)" in sql


def test_rpc_is_service_role_only() -> None:
    sql = _sql(CATALOG)
    assert "revoke all on function public.reconcile_dank_ticket_categories(text) from public,anon,authenticated" in sql
    assert "grant execute on function public.reconcile_dank_ticket_categories(text) to service_role" in sql
