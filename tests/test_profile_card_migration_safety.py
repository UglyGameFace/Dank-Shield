from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_optional_legacy_ticket_metadata_migration_skips_missing_table():
    source = (
        ROOT
        / "supabase"
        / "migrations"
        / "20260424_tickettool_parity_ticket_columns.sql"
    ).read_text(encoding="utf-8")

    assert "to_regclass('public.tickets') IS NOT NULL" in source
    assert "Skipping optional TicketTool metadata migration" in source
    assert "ALTER TABLE public.tickets" in source
    assert "EXECUTE $sql$" in source
    assert "CREATE INDEX IF NOT EXISTS idx_tickets_owner_id" in source
    assert "ALTER TABLE tickets" not in source


def test_later_guild_config_migration_reconciles_the_earlier_schema():
    source = (
        ROOT
        / "supabase"
        / "migrations"
        / "20260426_guild_configs.sql"
    ).read_text(encoding="utf-8")

    reconciliation = source.index("alter table public.guild_configs")
    enabled_index = source.index("idx_guild_configs_enabled")
    beta_index = source.index("idx_guild_configs_public_beta_enabled")
    seed_insert = source.index("insert into public.guild_configs")

    for column in (
        "guild_name text",
        "owner_id text",
        "enabled boolean not null default true",
        "public_beta_enabled boolean not null default false",
    ):
        assert f"add column if not exists {column}" in source

    assert reconciliation < enabled_index < seed_insert
    assert reconciliation < beta_index < seed_insert
    assert "CREATE TABLE IF NOT EXISTS does not add columns" in source


def test_live_profile_card_migration_is_idempotent_and_service_role_only():
    source = (
        ROOT
        / "supabase"
        / "migrations"
        / "20260725_live_profile_cards.sql"
    ).read_text(encoding="utf-8")

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
