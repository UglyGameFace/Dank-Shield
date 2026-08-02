from __future__ import annotations

"""Include the ticket-category v2 migration in direct-DSN bootstrap.

The public runtime already owns category selection and migration-aware fallbacks.
When the bot has a direct Postgres DSN, this registration makes the same
committed Supabase migration run automatically during the existing one-shot
schema bootstrap, so installed guilds receive the upgrade on deployment without
maintaining a second copy of the SQL.
"""

MIGRATION_FILE = "20260802042000_ticket_category_setup_selection.sql"


def apply() -> bool:
    try:
        from . import auto_schema_bootstrap as bootstrap
    except Exception as exc:
        print(
            "⚠️ ticket_category_schema_bootstrap_guard: "
            f"auto schema bootstrap unavailable: {exc!r}"
        )
        return False

    existing = tuple(getattr(bootstrap, "_BOOTSTRAP_MIGRATION_FILES", ()) or ())
    if MIGRATION_FILE not in existing:
        bootstrap._BOOTSTRAP_MIGRATION_FILES = (*existing, MIGRATION_FILE)

    try:
        print(
            "✅ ticket_category_schema_bootstrap_guard: "
            "ticket category v2 migration registered for direct-DSN startup"
        )
    except Exception:
        pass
    return True


apply()

__all__ = ["MIGRATION_FILE", "apply"]
