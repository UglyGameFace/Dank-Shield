from __future__ import annotations

import traceback
from typing import Any, Optional

import discord

from ..members_new.service import (
    sync_all_members,
    sync_member,
    sync_member_remove,
    sync_role_members,
    reconcile_departed_members,
)

# ============================================================
# Structured member-sync helpers only
# ------------------------------------------------------------
# IMPORTANT:
# This module intentionally does NOT own Discord @bot.event handlers.
# The primary runtime event owner is stoney_verify/events.py.
#
# Keeping event handlers out of this file avoids import-order collisions
# where a newer helper module silently overrides the richer legacy event
# flow that also feeds modlog, raid detection, verification assignment,
# and dashboard sync behavior.
# ============================================================


def _member_id(member: Any) -> str:
    try:
        return str(getattr(member, "id", "unknown"))
    except Exception:
        return "unknown"


def _guild_id(guild: Any) -> str:
    try:
        return str(getattr(guild, "id", "unknown"))
    except Exception:
        return "unknown"


def _role_id(role: Any) -> str:
    try:
        return str(getattr(role, "id", "unknown"))
    except Exception:
        return "unknown"


async def sync_member_snapshot(
    member: discord.Member,
    *,
    active: bool = True,
    departed: bool = False,
):
    try:
        return await sync_member(member, active=active, departed=departed)
    except Exception as e:
        print(f"❌ sync_member_snapshot failed for {_member_id(member)}: {repr(e)}")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return None


async def sync_departed_member(
    member_or_user: Any,
    guild: discord.Guild,
):
    try:
        return await sync_member_remove(member_or_user, guild)
    except Exception as e:
        print(
            f"❌ sync_departed_member failed for {_member_id(member_or_user)} "
            f"in guild {_guild_id(guild)}: {repr(e)}"
        )
        try:
            traceback.print_exc()
        except Exception:
            pass
        return None


async def sync_role_snapshot(role: discord.Role):
    try:
        return await sync_role_members(role)
    except Exception as e:
        print(f"❌ sync_role_snapshot failed for role {_role_id(role)}: {repr(e)}")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return None


# ============================================================
# Public helper API expected by other modules
# ------------------------------------------------------------
# These names are kept stable because api_new/server.py and
# tasks_new/command_queue.py import them directly.
# ============================================================


async def run_full_member_sync_for_guild(guild: discord.Guild):
    try:
        return await sync_all_members(guild)
    except Exception as e:
        print(f"❌ run_full_member_sync_for_guild failed for guild {_guild_id(guild)}: {repr(e)}")
        raise


async def run_departed_reconciliation_for_guild(guild: discord.Guild):
    try:
        return await reconcile_departed_members(guild)
    except Exception as e:
        print(
            f"❌ run_departed_reconciliation_for_guild failed for guild {_guild_id(guild)}: {repr(e)}"
        )
        raise


async def run_role_member_sync(role: discord.Role):
    try:
        return await sync_role_members(role)
    except Exception as e:
        print(f"❌ run_role_member_sync failed for role {_role_id(role)}: {repr(e)}")
        raise


__all__ = [
    "sync_member_snapshot",
    "sync_departed_member",
    "sync_role_snapshot",
    "run_full_member_sync_for_guild",
    "run_departed_reconciliation_for_guild",
    "run_role_member_sync",
]