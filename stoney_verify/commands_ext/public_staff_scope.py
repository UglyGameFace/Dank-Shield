from __future__ import annotations

"""
Per-guild staff permission isolation for public mode.

The legacy helpers in globals.py were built for one beta guild and used the
single STAFF_ROLE_ID from env. That is not safe enough for public/multi-server
use because another guild should never inherit the beta server's staff role.

This module patches the shared staff-check path before public command modules
import it. The result is intentionally simple:

- Administrator always counts as staff.
- Configured staff role from guild_configs counts as staff for that guild only.
- VC staff role also counts when configured.
- Unconfigured guilds do NOT use beta env role IDs; only admins can run setup.

No hardcoded guild IDs or role IDs live here. The resolver decides whether env
fallback is allowed for a guild.
"""

from typing import Any

import discord

_PATCHED = False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _member_role_ids(member: discord.Member) -> set[int]:
    ids: set[int] = set()
    try:
        for role in getattr(member, "roles", []) or []:
            rid = _safe_int(getattr(role, "id", 0), 0)
            if rid > 0:
                ids.add(rid)
    except Exception:
        pass
    return ids


def _configured_staff_role_ids(member: discord.Member) -> set[int]:
    try:
        from ..guild_config import get_cached_guild_config

        guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
        cfg = get_cached_guild_config(guild_id)
        ids = {
            _safe_int(getattr(cfg, "staff_role_id", 0), 0),
            _safe_int(getattr(cfg, "vc_staff_role_id", 0), 0),
            _safe_int(getattr(cfg, "effective_vc_staff_role_id", 0), 0),
        }
        return {rid for rid in ids if rid > 0}
    except Exception:
        return set()


def scoped_is_staff(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False

    try:
        if bool(getattr(member.guild_permissions, "administrator", False)):
            return True
    except Exception:
        pass

    staff_role_ids = _configured_staff_role_ids(member)
    if not staff_role_ids:
        return False

    return bool(_member_role_ids(member).intersection(staff_role_ids))


def _patch_staff_helpers() -> None:
    global _PATCHED
    if _PATCHED:
        return

    patched_any = False

    try:
        from .. import globals as g

        g.is_staff = scoped_is_staff  # type: ignore[assignment]
        patched_any = True
    except Exception as e:
        try:
            print(f"⚠️ public_staff_scope could not patch globals.is_staff: {repr(e)}")
        except Exception:
            pass

    try:
        from . import common

        common._staff_check = lambda interaction: scoped_is_staff(getattr(interaction, "user", None))  # type: ignore[assignment]
        patched_any = True
    except Exception as e:
        try:
            print(f"⚠️ public_staff_scope could not patch common._staff_check: {repr(e)}")
        except Exception:
            pass

    _PATCHED = bool(patched_any)
    if _PATCHED:
        try:
            print("✅ public_staff_scope: per-guild staff permission isolation active")
        except Exception:
            pass


def register_public_staff_scope(bot, tree) -> None:
    _ = bot, tree
    _patch_staff_helpers()


__all__ = ["register_public_staff_scope", "scoped_is_staff"]
