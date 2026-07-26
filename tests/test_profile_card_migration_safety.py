from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
PROFILE_MIGRATION = MIGRATIONS / "20260725_live_profile_cards.sql"
GUILD_CONFIG_MIGRATION = MIGRATIONS / "202604260001_guild_configs.sql"
TICKET_PARITY_MIGRATION = MIGRATIONS / "20260424_tickettool_parity_ticket_columns.sql"


def test_supabase_migration_versions_are_unique():
    versions = [path.name.split("_", 1)[0] for path in MIGRATIONS.glob("*.sql")]
    duplicates = sorted(version for version, count in Counter(versions).items() if count > 1)

    assert duplicates == []


def test_live_profile_card_migration_uses_its_own_version():
    assert PROFILE_MIGRATION.is_file()

    version = PROFILE_MIGRATION.name.split("_", 1)[0]
    matching = sorted(path.name for path in MIGRATIONS.glob(f"{version}_*.sql"))

    assert matching == [PROFILE_MIGRATION.name]


def test_ticket_parity_migration_skips_missing_legacy_table_safely():
    source = TICKET_PARITY_MIGRATION.read_text(encoding="utf-8").lower()

    guard = source.index("to_regclass('public.tickets') is null")
    notice = source.index("skipping tickettool parity columns")
    alter = source.index("alter table public.tickets")

    assert guard < notice < alter
    assert "create table" not in source
    assert "execute $ddl$" in source


def test_existing_guild_config_migration_reconciles_before_indexing():
    source = GUILD_CONFIG_MIGRATION.read_text(encoding="utf-8")

    reconciliation = source.index("alter table public.guild_configs")
    enabled_column = source.index("add column if not exists enabled")
    beta_column = source.index("add column if not exists public_beta_enabled")
    enabled_index = source.index("idx_guild_configs_enabled")
    beta_index = source.index("idx_guild_configs_public_beta_enabled")

    assert reconciliation < enabled_column < enabled_index
    assert reconciliation < beta_column < beta_index


def test_live_profile_card_migration_is_idempotent_and_service_role_only():
    source = PROFILE_MIGRATION.read_text(encoding="utf-8")

    for table in (
        "dank_profile_users",
        "dank_profile_guild_settings",
        "dank_live_profile_cards",
    ):
        assert f"create table if not exists public.{table}" in source
        assert f"alter table public.{table} enable row level security" in source
        assert f"revoke all on table public.{table} from anon, authenticated" in source

    assert "create policy" not in source.lower()
    assert "user_id text primary key" in source
    assert "primary key (guild_id, user_id)" in source
    assert "primary key (guild_id, channel_id)" in source
