from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPABASE_ROOT = ROOT / "supabase"
MIGRATION = SUPABASE_ROOT / "migrations" / "20260913154500_canonical_runtime_schema_authority.sql"
ROLE_STATE_RECONCILIATION = (
    SUPABASE_ROOT
    / "migrations"
    / "20260913160000_reconcile_guild_member_role_state_constraint.sql"
)
AUTO_GUARD = ROOT / "stoney_verify" / "startup_guards" / "auto_schema_bootstrap.py"
QUEUE_GUARD = ROOT / "stoney_verify" / "startup_guards" / "operation_queue_schema_guard.py"
CATEGORY_COMPAT = ROOT / "stoney_verify" / "startup_guards" / "ticket_category_schema_bootstrap_guard.py"
TICKET_DOCTOR = ROOT / "stoney_verify" / "startup_guards" / "ticket_panel_doctor_stability_guard.py"
TICKET_PANEL = ROOT / "stoney_verify" / "commands_ext" / "public_ticket_panel_clean.py"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-supabase-migrations.yml"


def _text(path: Path) -> str:
    assert path.exists(), f"missing required file: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_canonical_runtime_schema_has_committed_migration_ownership() -> None:
    sql = _text(MIGRATION).lower()

    required_tables = (
        "public.tickets",
        "public.ticket_notes",
        "public.ticket_messages",
        "public.activity_feed_events",
        "public.guild_members",
        "public.member_joins",
        "public.member_events",
        "public.member_activity_scan_locks",
        "public.member_cleanup_settings",
    )
    for table in required_tables:
        assert f"create table if not exists {table}" in sql

    # These are representative columns that were missing from the historical
    # runtime-only/bootstrap definitions and are required by current services.
    for column in (
        "panel_message_id",
        "last_activity_at",
        "message_type",
        "attachments jsonb",
        "event_family",
        "actor_user_id",
        "search_text",
        "entry_truth_quality",
        "entry_confidence",
        "entry_quality_reason",
        "risk_reasons jsonb",
        "suspicion_flags jsonb",
    ):
        assert column in sql

    assert "begin;" in sql
    assert "commit;" in sql
    assert "duplicate historical ticket numbers" in sql
    assert "duplicate historical channel ids" in sql
    assert "duplicate historical thread ids" in sql


def test_post_canonical_role_state_contract_reconciles_fresh_and_dirty_history() -> None:
    sql = _text(ROLE_STATE_RECONCILIATION).lower()

    assert "guild_members_role_state_check" in sql
    assert "role_state ~ '^[a-z][a-z0-9_]{0,63}$'" in sql
    assert "not valid" in sql
    assert "validate constraint guild_members_role_state_check" in sql
    assert "legacy rows contain nonconforming role_state values" in sql
    assert "drop constraint if exists guild_members_role_state_check" in sql


def test_runtime_schema_guards_are_read_only() -> None:
    forbidden = (
        "psycopg.connect",
        "cur.execute(",
        "create table",
        "alter table",
        "create index",
        "drop table",
        "migration.read_text",
        "supabase_db_url",
        "postgres_prisma_url",
    )

    for path in (AUTO_GUARD, QUEUE_GUARD):
        source = _text(path).lower()
        for marker in forbidden:
            assert marker not in source, f"runtime schema mutation path returned in {path.name}: {marker}"
        assert 'schema_sql = ""' in source


def test_runtime_health_does_not_advertise_retired_direct_dsn_repair() -> None:
    for path in (TICKET_DOCTOR, TICKET_PANEL):
        source = _text(path)
        assert "SUPABASE_DB_URL" not in source
        assert "POSTGRES_PRISMA_URL" not in source
        assert "_db_url_present" not in source
        assert "runtime startup will not alter production schema" in source


def test_no_standalone_schema_sql_exists_outside_migration_chain() -> None:
    standalone_sql = sorted(path.name for path in SUPABASE_ROOT.glob("*.sql"))
    assert standalone_sql == [], (
        "schema-changing SQL under supabase/ must live in supabase/migrations/: "
        + ", ".join(standalone_sql)
    )


def test_category_compatibility_manifest_has_no_import_side_effect() -> None:
    source = _text(CATEGORY_COMPAT)
    lowered = source.lower()

    assert "from . import auto_schema_bootstrap" not in source
    assert "bootstrap._BOOTSTRAP_MIGRATION_FILES" not in source
    assert "Migration execution belongs to the Supabase migration pipeline" in source
    assert "return True" in source

    # The read-only owner lists category migrations explicitly rather than being
    # mutated by another startup module.
    auto_source = _text(AUTO_GUARD)
    for migration in (
        "20260802042000_ticket_category_setup_selection.sql",
        "20260807215900_prepare_managed_ticket_category_repair.sql",
        "20260807220000_repair_managed_ticket_category_duplicates.sql",
        "20260910163000_preserve_ticket_category_selection_on_review.sql",
        "20260911113000_restore_rich_ticket_category_selection.sql",
    ):
        assert migration in auto_source

    assert "apply()\n\n__all__" not in source
    assert "direct-dsn startup" not in lowered


def test_schema_readiness_points_to_canonical_migration_chain() -> None:
    source = _text(AUTO_GUARD)
    assert "20260913154500_canonical_runtime_schema_authority.sql" in source
    assert "20260913160000_reconcile_guild_member_role_state_constraint.sql" in source
    assert "supabase/2026-05-08_runtime_stability_schema.sql" not in source

    for table in (
        '"tickets"',
        '"ticket_notes"',
        '"ticket_messages"',
        '"activity_feed_events"',
        '"guild_members"',
        '"member_joins"',
        '"member_events"',
        '"member_activity_scan_locks"',
        '"member_cleanup_settings"',
    ):
        assert table in source


def test_production_schema_changes_flow_through_supabase_cli() -> None:
    workflow = _text(DEPLOY_WORKFLOW)
    assert "supabase migration list" in workflow
    assert "supabase db push --dry-run" in workflow
    assert "supabase db push" in workflow


def test_production_schema_deploy_waits_for_canonical_ci() -> None:
    workflow = _text(DEPLOY_WORKFLOW)

    # Production promotion must be a privileged follow-up to the canonical CI
    # run, never a sibling push workflow racing the same main commit.
    assert "workflow_run:" in workflow
    assert 'workflows: ["Dank Shield CI"]' in workflow
    assert "types: [completed]" in workflow
    assert "branches: [main]" in workflow
    assert "\n  push:\n" not in workflow

    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow

    # Manual recovery is deliberately explicit and immutable: it must target a
    # full commit SHA that is verified to belong to canonical main history.
    assert "workflow_dispatch:" in workflow
    assert "target_sha:" in workflow
    assert "^[0-9a-f]{40}$" in workflow
    assert "git merge-base --is-ancestor" in workflow

    assert "environment: production" in workflow
    assert "group: supabase-production-migrations" in workflow
    assert "cancel-in-progress: false" in workflow
