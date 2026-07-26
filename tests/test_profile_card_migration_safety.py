from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
PROFILE_MIGRATION = MIGRATIONS / "20260725_live_profile_cards.sql"


def test_live_profile_card_migration_uses_its_own_version():
    assert PROFILE_MIGRATION.is_file()

    version = PROFILE_MIGRATION.name.split("_", 1)[0]
    matching = sorted(path.name for path in MIGRATIONS.glob(f"{version}_*.sql"))

    assert matching == [PROFILE_MIGRATION.name]


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
