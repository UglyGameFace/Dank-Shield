"""Guard the fresh Supabase migration namespace and staged recurring campaign schema."""
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "supabase_fresh" / "migrations"
CAMPAIGNS = FRESH / "202611050001_dankshield_embed_studio_campaigns.sql"


def test_fresh_migration_versions_are_unique():
    files = sorted(FRESH.glob("*.sql"))
    versions = [path.name.split("_", 1)[0] for path in files]
    duplicates = [version for version, count in Counter(versions).items() if count > 1]
    assert not duplicates, f"duplicate fresh migration versions: {duplicates}"
    assert all(len(version) == 14 and version.isdigit() for version in versions)


def test_recurring_campaign_schema_is_separate_from_artwork_budgets():
    assert (FRESH / "202610200034_dankshield_embed_studio_asset_budgets.sql").exists()
    sql = CAMPAIGNS.read_text()
    assert "create table if not exists public.dank_embed_studio_campaigns" in sql
    assert "enable row level security" in sql
    assert "grant select,insert,update on public.dank_embed_studio_campaigns to service_role" in sql
    assert "dank_embed_studio_claim_campaigns" in sql
    assert "for update skip locked" in sql
    assert "p_owned_shards" in sql
    assert "202610200034_dankshield_embed_studio_campaigns" not in CAMPAIGNS.name


def test_recurring_schema_does_not_enable_runtime_or_change_credentials():
    sql = CAMPAIGNS.read_text().lower()
    assert "http_post" not in sql and "net.http" not in sql
    assert "create extension" not in sql
    assert "drop table" not in sql
    assert "service_role" in sql
