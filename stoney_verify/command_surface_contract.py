from __future__ import annotations

"""Canonical public Discord command-surface contract for Dank Shield.

Implementation modules may temporarily register broader command groups during
startup so their services/listeners/persistent views remain loaded. The final
DS-COMMAND-UX-024 compactor reduces only the Discord-visible application-command
tree to this contract before global sync.
"""

# Top-level global application commands intentionally exposed by the normal
# public profile after final compaction. The final item is a user context-menu
# command, not a slash command, but Discord counts it in the global surface.
PUBLIC_GLOBAL_COMMAND_NAMES: tuple[str, ...] = (
    "dank",
    "mod",
    "ticket",
    "tickets",
    "verify",
    "View Dank Profile",
)
PUBLIC_GLOBAL_COMMAND_COUNT = len(PUBLIC_GLOBAL_COMMAND_NAMES)

# Direct /dank children in the final public product surface. Normal feature
# work is reached from the Home mega menu; Upload is retained only because a
# Discord button cannot provide an attachment input field.
PUBLIC_DANK_CHILDREN: frozenset[str] = frozenset({"home", "upload"})

# Advanced, migration, repair, legacy, and redundant direct aliases that must
# not be exposed as direct /dank children after final public compaction. Their
# actions remain available through guided UI centers or loaded implementation
# services where appropriate.
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
        # Former normal-product shortcuts now owned by the Home mega menu.
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


def unexpected_public_dank_children(names: set[str] | frozenset[str]) -> list[str]:
    """Return direct public /dank children outside the canonical contract."""

    return sorted(str(name) for name in names if str(name) not in PUBLIC_DANK_CHILDREN)


def hidden_public_dank_children(names: set[str] | frozenset[str]) -> list[str]:
    """Return forbidden direct aliases present in a final public /dank group."""

    return sorted(str(name) for name in names if str(name) in PUBLIC_HIDDEN_DANK_CHILDREN)


__all__ = [
    "PUBLIC_DANK_CHILDREN",
    "PUBLIC_GLOBAL_COMMAND_COUNT",
    "PUBLIC_GLOBAL_COMMAND_NAMES",
    "PUBLIC_HIDDEN_DANK_CHILDREN",
    "hidden_public_dank_children",
    "unexpected_public_dank_children",
]
