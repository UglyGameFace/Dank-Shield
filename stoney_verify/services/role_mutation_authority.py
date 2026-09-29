from __future__ import annotations

"""Shared live Discord role-mutation authority checks.

This module owns the reusable permission + hierarchy boundary for any Dank
Shield feature that asks the bot to mutate a guild role on behalf of a human
actor. Feature UIs may add stricter product permissions, but they must not
weaken these live Discord checks.

Automatic background policy enforcement that has no initiating human actor is
intentionally outside this helper's actor boundary and must use its own explicit
policy authority.
"""

from typing import Any

import discord


def actor_id(actor: Any) -> int:
    try:
        return int(getattr(actor, "id", 0) or 0)
    except Exception:
        return 0


def is_guild_owner(guild: discord.Guild, actor: Any) -> bool:
    uid = actor_id(actor)
    if uid <= 0:
        return False
    try:
        if int(getattr(guild, "owner_id", 0) or 0) == uid:
            return True
    except Exception:
        pass
    try:
        return int(getattr(getattr(guild, "owner", None), "id", 0) or 0) == uid
    except Exception:
        return False


def actor_can_manage_roles(guild: discord.Guild, actor: Any) -> bool:
    if is_guild_owner(guild, actor):
        return True
    if not isinstance(actor, discord.Member):
        return False
    perms = actor.guild_permissions
    return bool(perms.administrator or perms.manage_roles)


def bot_can_manage_roles(guild: discord.Guild) -> bool:
    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return False
    perms = me.guild_permissions
    return bool(perms.administrator or perms.manage_roles)


def role_mutation_blockers(
    guild: discord.Guild,
    actor: Any,
    role: discord.Role,
) -> list[str]:
    """Return live blockers for a human-requested role mutation.

    Discord's Manage Roles permission is hierarchy-scoped. A manager cannot use
    Dank Shield as a privilege trampoline to mutate a role at or above their own
    highest role merely because Dank Shield itself is positioned higher.
    """

    blockers: list[str] = []
    me = getattr(guild, "me", None)

    try:
        if role.is_default():
            blockers.append("@everyone cannot be edited by this tool.")
    except Exception:
        blockers.append("Dank Shield could not verify whether this is @everyone.")

    if bool(getattr(role, "managed", False)):
        blockers.append("Discord/integration-managed roles cannot be edited manually.")

    if not actor_can_manage_roles(guild, actor):
        blockers.append("You no longer have Manage Roles.")

    if not isinstance(me, discord.Member):
        blockers.append("Dank Shield could not resolve its server member.")
        return blockers

    if not bot_can_manage_roles(guild):
        blockers.append("Dank Shield is missing Manage Roles.")

    try:
        if not is_guild_owner(guild, actor):
            if not isinstance(actor, discord.Member):
                blockers.append("Your live server-member role hierarchy could not be resolved.")
            elif role >= actor.top_role:
                blockers.append("Your highest role must stay above the role you edit.")
    except Exception:
        blockers.append("Your role hierarchy could not be verified.")

    try:
        if int(me.id) != int(guild.owner_id) and role >= me.top_role:
            blockers.append("Dank Shield's highest role must stay above the role it edits.")
    except Exception:
        blockers.append("Dank Shield's role hierarchy could not be verified.")

    return blockers


__all__ = [
    "actor_can_manage_roles",
    "actor_id",
    "bot_can_manage_roles",
    "is_guild_owner",
    "role_mutation_blockers",
]
