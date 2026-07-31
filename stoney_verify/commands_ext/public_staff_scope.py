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
- Ticket panel and transcript controls use the same per-guild staff truth.
- Ticket channel permission synchronization targets configured guild roles.
- Legacy persistent controls, top-level commands, dashboard API mutations, and
  dashboard command queues receive central claim-first wrappers.
- Startup fails closed if any critical ticket security patch is missing.

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


def _config_value(config: Any, key: str) -> Any:
    try:
        if isinstance(config, dict):
            return config.get(key)
        getter = getattr(config, "get", None)
        if callable(getter):
            value = getter(key)
            if value is not None:
                return value
        return getattr(config, key, None)
    except Exception:
        return None


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


def configured_ticket_staff_role_ids(guild_id: int) -> list[int]:
    """Return only staff roles configured for this Discord guild."""
    try:
        from ..guild_config import get_cached_guild_config

        config = get_cached_guild_config(_safe_int(guild_id, 0))
    except Exception:
        config = None

    ids: list[int] = []
    for key in (
        "staff_role_id",
        "vc_staff_role_id",
        "effective_vc_staff_role_id",
    ):
        role_id = _safe_int(_config_value(config, key), 0)
        if role_id > 0 and role_id not in ids:
            ids.append(role_id)
    return ids


def _configured_staff_role_ids(member: discord.Member) -> set[int]:
    guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
    return set(configured_ticket_staff_role_ids(guild_id))


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

    installed = {
        "globals": False,
        "common": False,
        "ticket_panel": False,
        "ticket_transcripts": False,
        "ticket_permissions": False,
        "ticket_claim_runtime": False,
        "ticket_admin_claim_runtime": False,
        "ticket_api_claim_runtime": False,
        "ticket_tasks_queue_claim_runtime": False,
        "ticket_bot_worker_claim_runtime": False,
    }

    try:
        from .. import globals as g

        g.is_staff = scoped_is_staff  # type: ignore[assignment]
        installed["globals"] = True
    except Exception as e:
        print(f"❌ public_staff_scope could not patch globals.is_staff: {repr(e)}")

    try:
        from . import common

        common._staff_check = lambda interaction: scoped_is_staff(getattr(interaction, "user", None))  # type: ignore[assignment]
        installed["common"] = True
    except Exception as e:
        print(f"❌ public_staff_scope could not patch common._staff_check: {repr(e)}")

    try:
        from ..tickets_new import panel as ticket_panel

        ticket_panel._is_staff_member = scoped_is_staff  # type: ignore[assignment]
        installed["ticket_panel"] = True
    except Exception as e:
        print(f"❌ public_staff_scope could not patch ticket panel staff scope: {repr(e)}")

    ticket_transcripts = None
    try:
        from .. import transcripts as loaded_ticket_transcripts

        ticket_transcripts = loaded_ticket_transcripts
        ticket_transcripts._is_staff_member = scoped_is_staff  # type: ignore[assignment]
        installed["ticket_transcripts"] = True
    except Exception as e:
        print(f"❌ public_staff_scope could not patch transcript staff scope: {repr(e)}")

    if ticket_transcripts is not None:
        try:
            from ..tickets_new.claim_runtime_guard import install_transcript_claim_runtime_guards

            install_transcript_claim_runtime_guards(ticket_transcripts)
            installed["ticket_claim_runtime"] = True
        except Exception as e:
            print(f"❌ public_staff_scope could not install claim runtime guards: {repr(e)}")

        try:
            from . import ticket_admin
            from ..tickets_new.ticket_admin_claim_guard import install_ticket_admin_claim_guard

            install_ticket_admin_claim_guard(
                ticket_admin,
                ticket_transcripts=ticket_transcripts,
            )
            installed["ticket_admin_claim_runtime"] = True
        except Exception as e:
            print(f"❌ public_staff_scope could not install legacy ticket command guards: {repr(e)}")

    try:
        from ..api_new import server as ticket_api_server
        from ..tickets_new.api_claim_runtime_guard import install_api_claim_runtime_guards

        install_api_claim_runtime_guards(ticket_api_server)
        installed["ticket_api_claim_runtime"] = True
    except Exception as e:
        print(f"❌ public_staff_scope could not install dashboard ticket claim guards: {repr(e)}")

    try:
        from ..tasks_new import command_queue as ticket_tasks_queue
        from ..workers import bot_command_worker as ticket_bot_worker
        from ..tickets_new.command_queue_claim_guard import (
            install_bot_command_worker_claim_guard,
            install_tasks_command_queue_claim_guard,
        )

        install_tasks_command_queue_claim_guard(ticket_tasks_queue)
        installed["ticket_tasks_queue_claim_runtime"] = True

        install_bot_command_worker_claim_guard(ticket_bot_worker)
        installed["ticket_bot_worker_claim_runtime"] = True
    except Exception as e:
        print(f"❌ public_staff_scope could not install dashboard command queue guards: {repr(e)}")

    try:
        from ..tickets_new import service as ticket_service

        ticket_service._default_staff_role_ids = configured_ticket_staff_role_ids  # type: ignore[assignment]
        installed["ticket_permissions"] = True
    except Exception as e:
        print(f"❌ public_staff_scope could not patch ticket permission staff scope: {repr(e)}")

    missing = sorted(name for name, ok in installed.items() if not ok)
    _PATCHED = not missing
    if missing:
        raise RuntimeError(
            "Per-guild ticket security scope failed closed; missing patches: "
            + ", ".join(missing)
        )

    print("✅ public_staff_scope: per-guild ticket security scope active")


def register_public_staff_scope(bot, tree) -> None:
    _ = bot, tree
    _patch_staff_helpers()
    if not _PATCHED:
        raise RuntimeError("Per-guild ticket security scope is not active.")


__all__ = [
    "configured_ticket_staff_role_ids",
    "register_public_staff_scope",
    "scoped_is_staff",
]
