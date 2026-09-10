from __future__ import annotations

"""Register ticket-category migrations in direct-DSN bootstrap.

The public runtime already owns category selection and migration-aware fallbacks.
When the bot has a direct Postgres DSN, this registration makes the committed
Supabase migrations run automatically during the existing one-shot schema
bootstrap. Selection schema stays v2; the preflight safely releases historical
key swaps before the separate v3 catalog repair canonicalizes every managed row.
The final preservation migration keeps the last owner-confirmed selection when
a guild is temporarily forced back into ticket-menu review mode.
"""

MIGRATION_FILE = "20260802042000_ticket_category_setup_selection.sql"
REPAIR_PREP_MIGRATION_FILE = "20260807215900_prepare_managed_ticket_category_repair.sql"
REPAIR_MIGRATION_FILE = "20260807220000_repair_managed_ticket_category_duplicates.sql"
PRESERVE_SELECTION_MIGRATION_FILE = "20260910163000_preserve_ticket_category_selection_on_review.sql"
MIGRATION_FILES = (
    MIGRATION_FILE,
    REPAIR_PREP_MIGRATION_FILE,
    REPAIR_MIGRATION_FILE,
    PRESERVE_SELECTION_MIGRATION_FILE,
)


def apply() -> bool:
    try:
        from . import auto_schema_bootstrap as bootstrap
    except Exception as exc:
        print(
            "⚠️ ticket_category_schema_bootstrap_guard: "
            f"auto schema bootstrap unavailable: {exc!r}"
        )
        return False

    existing = list(getattr(bootstrap, "_BOOTSTRAP_MIGRATION_FILES", ()) or ())
    for migration in MIGRATION_FILES:
        if migration not in existing:
            existing.append(migration)
    bootstrap._BOOTSTRAP_MIGRATION_FILES = tuple(existing)

    try:
        print(
            "✅ ticket_category_schema_bootstrap_guard: "
            "ticket category selection v2 + stale-key preflight + managed catalog repair v3 + selection-preservation repair registered for direct-DSN startup"
        )
    except Exception:
        pass
    return True


apply()

__all__ = [
    "MIGRATION_FILE",
    "REPAIR_PREP_MIGRATION_FILE",
    "REPAIR_MIGRATION_FILE",
    "PRESERVE_SELECTION_MIGRATION_FILE",
    "MIGRATION_FILES",
    "apply",
]
