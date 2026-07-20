from __future__ import annotations

"""Canonical public Discord command-surface contract for Dank Shield.

Keep registration, pre-sync safety checks, documentation audits, and regression
checks pointed at this module instead of maintaining competing allowlists.
"""

# Top-level global application commands intentionally exposed by the normal
# public profile. The final item is a user context-menu command, not a slash
# command, but Discord counts it in the global application-command surface.
PUBLIC_GLOBAL_COMMAND_NAMES: tuple[str, ...] = (
    "dank",
    "mod",
    "ticket",
    "tickets",
    "ticket-intake",
    "ticket-category",
    "ticket-panel",
    "verify",
    "View Dank Profile",
)
PUBLIC_GLOBAL_COMMAND_COUNT = len(PUBLIC_GLOBAL_COMMAND_NAMES)

# Direct /dank children that belong to the stable public product surface.
# Nested children below these groups are owned by their feature modules.
PUBLIC_DANK_CHILDREN: frozenset[str] = frozenset(
    {
        "setup",
        "overview",
        "status",
        "diagnostics",
        "protection",
        "help",
        "commands",
        "cleanup",
        "members",
        "member-logs",
        "welcome",
        "profile",
        "roles",
        "modlog",
        "embed",
        "design",
    }
)

# Advanced, migration, repair, legacy, and direct setup aliases that must not be
# exposed as direct /dank children in the normal public profile. They may exist
# in explicit public-admin/dev profiles where their registrar is selected.
PUBLIC_HIDDEN_DANK_CHILDREN: frozenset[str] = frozenset(
    {
        "automod",
        "spam",
        "config-cache",
        "current",
        "archive-backfill",
        "cache",
        "config",
        "db-check",
        "health",
        "launch-check",
        "modlog-check",
        "permission-check",
        "production-audit",
        "refresh-config",
        "scoreboard",
        "setup-access",
        "setup-assistant",
        "setup-by-id",
        "setup-defaults",
        "setup-find",
        "setup-logs",
        "setup-picker",
        "setup-review",
        "setup-start",
        "setup-status",
        "setup-tickets",
        "setup-verify",
        "setup-verify-ids",
        "tickettool-check",
    }
)


def unexpected_public_dank_children(names: set[str] | frozenset[str]) -> list[str]:
    """Return direct public /dank children outside the canonical contract."""

    return sorted(str(name) for name in names if str(name) not in PUBLIC_DANK_CHILDREN)


def hidden_public_dank_children(names: set[str] | frozenset[str]) -> list[str]:
    """Return forbidden advanced/direct aliases present in a public /dank group."""

    return sorted(str(name) for name in names if str(name) in PUBLIC_HIDDEN_DANK_CHILDREN)


__all__ = [
    "PUBLIC_DANK_CHILDREN",
    "PUBLIC_GLOBAL_COMMAND_COUNT",
    "PUBLIC_GLOBAL_COMMAND_NAMES",
    "PUBLIC_HIDDEN_DANK_CHILDREN",
    "hidden_public_dank_children",
    "unexpected_public_dank_children",
]
