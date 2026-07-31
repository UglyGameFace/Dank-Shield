from __future__ import annotations

from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"

# Historical files created before migration automation used shorter numeric
# versions. They are already present in production and remain grandfathered.
# New migrations must use the 14-digit timestamp emitted by Supabase CLI.
LEGACY_VERSION_EXCEPTIONS = {
    "20260424",
    "202604260001",
    "20260429",
    "202605011835",
    "202606110001",
    "20260613",
    "20260711",
    "20260712",
    "20260720",
    "20260725",
    "202607270001",
    "202607310001",
    "202607310002",
}


def _migration_versions() -> list[tuple[str, str]]:
    migrations: list[tuple[str, str]] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version, separator, _name = path.name.partition("_")
        assert separator, f"migration filename has no name separator: {path.name}"
        assert version.isdigit(), f"migration version must be numeric: {path.name}"
        migrations.append((version, path.name))
    return migrations


def test_migration_versions_are_unique() -> None:
    migrations = _migration_versions()
    counts = Counter(version for version, _name in migrations)
    duplicates = sorted(version for version, count in counts.items() if count > 1)
    assert not duplicates, f"duplicate Supabase migration versions: {duplicates}"


def test_new_migrations_use_supabase_cli_timestamp_format() -> None:
    invalid = [
        name
        for version, name in _migration_versions()
        if len(version) != 14 and version not in LEGACY_VERSION_EXCEPTIONS
    ]
    assert not invalid, (
        "new Supabase migrations must use a unique 14-digit timestamp "
        f"(<timestamp>_<name>.sql): {invalid}"
    )


def test_known_broken_short_versions_do_not_return() -> None:
    versions = {version for version, _name in _migration_versions()}
    assert "20260426" not in versions
    assert "20260611" not in versions
    assert "20260426000200" in versions
    assert "20260611000200" in versions
