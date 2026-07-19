from __future__ import annotations

"""Authoritative product defaults for Dank Shield SpamGuard.

A missing persisted security-settings row is a product default, not an explicit
owner choice. Explicit persisted values always override these defaults.
"""

from typing import Any

SPAM_GUARD_DEFAULT_ENABLED = True
SPAM_GUARD_DEFAULT_MODE = "timeout"


def default_spam_guard_settings(guild_id: int | str) -> dict[str, Any]:
    """Return a fresh copy of the single authoritative SpamGuard defaults."""

    return {
        "guild_id": str(int(guild_id)),
        "enabled": SPAM_GUARD_DEFAULT_ENABLED,
        "mode": SPAM_GUARD_DEFAULT_MODE,
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 12,
        "message_threshold": 5,
        "duplicate_threshold": 3,
        "invite_threshold": 2,
        "multi_invite_immediate": 2,
        "delete_history": 8,
        # Setup UI compatibility: this controls bounded cleanup work, while
        # delete_history is the runtime burst-history default.
        "delete_limit": 25,
        "timeout_minutes": 30,
        "cooldown_seconds": 20,
        "quarantine_role_id": "",
        "exempt_role_ids": [],
        "invite_allowed_role_ids": [],
        "allowed_channel_ids": [],
        "exempt_user_ids": [],
        "allowed_invite_codes": [],
    }


__all__ = [
    "SPAM_GUARD_DEFAULT_ENABLED",
    "SPAM_GUARD_DEFAULT_MODE",
    "default_spam_guard_settings",
]
