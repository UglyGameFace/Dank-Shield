from __future__ import annotations

"""Compatibility coverage for Discord audit events newer than discord.py enums."""

import re
from typing import Any, Mapping

import discord

from . import anti_nuke_guardian_runtime as guardian
from . import anti_nuke_self_action_runtime as self_action

_INSTALL_FLAG = "_dank_antinuke_audit_compat_installed"
_ACTION_FLAG = "_dank_antinuke_audit_compat_action_patched"
_ROUTE_FLAG = "_dank_antinuke_audit_compat_route_patched"

# Discord documents action_type 192/193, while discord.py 2.7.1 does not yet
# expose named AuditLogAction enum members for them.
_RAW_ACTION_NAMES = {
    "192": "voice_channel_status_create",
    "193": "voice_channel_status_delete",
}


def _patch_action_names() -> bool:
    if bool(getattr(guardian, _ACTION_FLAG, False)):
        return False
    guardian_original = guardian._action_name  # noqa: SLF001
    self_original = self_action._action_name  # noqa: SLF001

    def guardian_name(entry: Any) -> str:
        name = guardian_original(entry)
        return _RAW_ACTION_NAMES.get(name, name)

    def self_name(entry: Any) -> str:
        name = self_original(entry)
        return _RAW_ACTION_NAMES.get(name, name)

    guardian._action_name = guardian_name  # noqa: SLF001
    self_action._action_name = self_name  # noqa: SLF001
    guardian._ACTIONS.update(  # noqa: SLF001
        {
            "voice_channel_status_create": (
                "Voice-channel status creation",
                "antinuke_channel_delete_threshold",
                "voice_status",
                1,
            ),
            "voice_channel_status_delete": (
                "Voice-channel status deletion",
                "antinuke_channel_delete_threshold",
                "voice_status",
                1,
            ),
        }
    )
    guardian._PANIC_WEIGHTS.update(  # noqa: SLF001
        {"voice_channel_status_create": 1, "voice_channel_status_delete": 1}
    )
    guardian._PANIC_ACTIONS = frozenset(guardian._PANIC_WEIGHTS)  # noqa: SLF001
    self_action._PROTECTED_ACTIONS = frozenset(  # noqa: SLF001
        set(self_action._PROTECTED_ACTIONS)  # noqa: SLF001
        | {"voice_channel_status_create", "voice_channel_status_delete"}
    )
    setattr(guardian, _ACTION_FLAG, True)
    return True


def _patch_self_action_route() -> bool:
    if bool(getattr(self_action, _ROUTE_FLAG, False)):
        return False
    original = self_action._request_spec  # noqa: SLF001

    def wrapped(bot: discord.Client, route: Any, kwargs: Mapping[str, Any]):
        spec = original(bot, route, kwargs)
        if spec is not None:
            return spec
        method = str(getattr(route, "method", "") or "").strip().upper()
        path = self_action._route_path(route)  # noqa: SLF001
        match = re.fullmatch(r"/channels/(\d+)/voice-status", path)
        if match and method == "PUT":
            channel_id = int(match.group(1))
            payload = kwargs.get("json")
            status = payload.get("status") if isinstance(payload, Mapping) else None
            action = (
                "voice_channel_status_delete"
                if status is None
                else "voice_channel_status_create"
            )
            return self_action._spec(  # noqa: SLF001
                (action,),
                self_action._guild_for_channel(bot, channel_id),  # noqa: SLF001
                self_action._id_key(channel_id),  # noqa: SLF001
            )
        return None

    self_action._request_spec = wrapped  # noqa: SLF001
    setattr(self_action, _ROUTE_FLAG, True)
    return True


def install_anti_nuke_audit_compat_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    action_names = _patch_action_names()
    routes = _patch_self_action_route()
    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🧩 AntiNuke audit compatibility active: raw Discord 192/193 voice-status "
        f"events={'mapped' if action_names else 'ready'}; "
        f"local proof route={'covered' if routes else 'ready'}"
    )
    return True


__all__ = ["install_anti_nuke_audit_compat_runtime"]
