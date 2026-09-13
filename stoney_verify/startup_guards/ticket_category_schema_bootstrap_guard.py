from __future__ import annotations

"""Compatibility manifest for ticket-category migration ordering.

Historically this module mutated ``auto_schema_bootstrap`` at import time so the
bot could execute category migrations from a direct database connection during
startup. Runtime schema mutation is retired. The constants remain temporarily
for audits/tests and migration-order references, while the read-only readiness
guard owns its own diagnostic manifest explicitly.
"""

MIGRATION_FILE = "20260802042000_ticket_category_setup_selection.sql"
REPAIR_PREP_MIGRATION_FILE = "20260807215900_prepare_managed_ticket_category_repair.sql"
REPAIR_MIGRATION_FILE = "20260807220000_repair_managed_ticket_category_duplicates.sql"
PRESERVE_SELECTION_MIGRATION_FILE = "20260910163000_preserve_ticket_category_selection_on_review.sql"
RICH_SELECTION_RECOVERY_MIGRATION_FILE = "20260911113000_restore_rich_ticket_category_selection.sql"
MIGRATION_FILES = (
    MIGRATION_FILE,
    REPAIR_PREP_MIGRATION_FILE,
    REPAIR_MIGRATION_FILE,
    PRESERVE_SELECTION_MIGRATION_FILE,
    RICH_SELECTION_RECOVERY_MIGRATION_FILE,
)


def apply() -> bool:
    """Compatibility no-op.

    Migration execution belongs to the Supabase migration pipeline. Keeping this
    callable avoids breaking older imports while guaranteeing that importing the
    module cannot mutate another startup guard or the database.
    """
    return True


__all__ = [
    "MIGRATION_FILE",
    "REPAIR_PREP_MIGRATION_FILE",
    "REPAIR_MIGRATION_FILE",
    "PRESERVE_SELECTION_MIGRATION_FILE",
    "RICH_SELECTION_RECOVERY_MIGRATION_FILE",
    "MIGRATION_FILES",
    "apply",
]
