from __future__ import annotations

"""Make setup pickers save safely instead of surfacing raw permission errors.

Two production polish fixes:
- Channel/category pickers should not fail with raw AttributeError. Save the selected
  ID and let Setup Safety Check report any real permission work.
- Channel-font scoped repair should not spam a giant failed list when Discord
  denies overwrite edits. Collapse repeated denials into one plain-language item.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🛡️ setup_picker_permission_error_guard {message}")
    except Exception:
        pass


def _bot_member(guild: discord.Guild) -> discord.Member | None:
    try:
        me = getattr(guild, "me", None)
        return me if isinstance(me, discord.Member) else None
    except Exception:
        return None


def _resolve_channel(guild: discord.Guild, channel: Any) -> Any:
    try:
        cid = int(getattr(channel, "id", channel) or 0)
    except Exception:
        cid = 0
    if cid > 0:
        try:
            resolved = guild.get_channel(cid)
            if resolved is not None:
                return resolved
        except Exception:
            pass
    return channel


def _safe_bool(obj: Any, name: str) -> bool:
    try:
        return bool(getattr(obj, name, False))
    except Exception:
        return False


def _patch_full_customization() -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_full_customization as custom
    except Exception:
        return False

    current = getattr(custom, "_channel_warnings", None)
    if not callable(current):
        return False
    if getattr(current, "_permission_error_guard_wrapped", False):
        return True

    def _channel_warnings(guild: discord.Guild, channel: Any, *, need_files: bool = False) -> tuple[bool, list[str]]:
        channel = _resolve_channel(guild, channel)
        me = _bot_member(guild)
        if me is None:
            return False, ["Bot member could not be resolved for channel permission checks."]

        warnings: list[str] = []
        try:
            perms = channel.permissions_for(me)
        except Exception:
            # Do not hard-fail setup saving because Discord/discord.py gave us a
            # partial channel object. Persist the ID; the health check can inspect
            # the live channel again after cache catches up.
            return True, []

        if not _safe_bool(perms, "view_channel"):
            warnings.append("Bot may be missing View Channel here; Safety Check will confirm.")
        if isinstance(channel, discord.TextChannel):
            if not _safe_bool(perms, "send_messages"):
                warnings.append("Bot may be missing Send Messages here.")
            if not _safe_bool(perms, "embed_links"):
                warnings.append("Bot may be missing Embed Links here.")
            if need_files and not _safe_bool(perms, "attach_files"):
                warnings.append("Bot may be missing Attach Files here.")
        if isinstance(channel, discord.CategoryChannel):
            if not _safe_bool(perms, "manage_channels"):
                warnings.append("Bot may need Manage Channels on this category for ticket/channel actions.")
        if isinstance(channel, discord.VoiceChannel):
            if not _safe_bool(perms, "connect"):
                warnings.append("Bot may be missing Connect on this voice channel.")
            if not _safe_bool(perms, "manage_channels"):
                warnings.append("Bot may need Manage Channels on this voice channel.")

        # Saving a setup selection should be allowed. Permission details belong in
        # health/safety repair, not a raw red picker failure.
        return True, warnings[:4]

    setattr(_channel_warnings, "_permission_error_guard_wrapped", True)
    custom._channel_warnings = _channel_warnings  # type: ignore[attr-defined]
    return True


def _label(channel: Any) -> str:
    try:
        return str(getattr(channel, "mention", None) or f"`{getattr(channel, 'name', 'unknown')}`")
    except Exception:
        return "`unknown`"


def _patch_font_repair() -> bool:
    try:
        from stoney_verify.startup_guards import channel_font_access_repair_guard as repair
    except Exception:
        return False

    current = getattr(repair, "_repair", None)
    if not callable(current):
        return False
    if getattr(current, "_permission_error_guard_wrapped", False):
        return True

    async def _repair(guild: discord.Guild, actor_id: int, blocked: list[dict[str, Any]]) -> dict[str, list[str]]:
        me = _bot_member(guild)
        changed: list[str] = []
        unchanged: list[str] = []
        failed: list[str] = []
        denied = 0
        missing = 0
        if me is None:
            return {"changed": [], "unchanged": [], "failed": ["Dank Shield bot member could not be resolved."]}

        server_perms = getattr(me, "guild_permissions", None)
        can_edit = bool(server_perms and (_safe_bool(server_perms, "administrator") or (_safe_bool(server_perms, "manage_roles") and _safe_bool(server_perms, "manage_channels"))))
        if not can_edit:
            return {
                "changed": [],
                "unchanged": [],
                "failed": ["Auto-repair needs server-level Manage Roles + Manage Channels. Move Dank Shield higher and grant those permissions, then rerun repair."],
            }

        seen: set[int] = set()
        for row in blocked[:50]:
            try:
                cid = int(str(row.get("channel_id") or "0") or 0)
            except Exception:
                cid = 0
            if cid <= 0 or cid in seen:
                continue
            seen.add(cid)
            channel = guild.get_channel(cid)
            if channel is None or not callable(getattr(channel, "set_permissions", None)):
                missing += 1
                continue
            try:
                perms = channel.permissions_for(me)
                if _safe_bool(perms, "view_channel") and _safe_bool(perms, "manage_channels"):
                    unchanged.append(_label(channel))
                    continue
                current_overwrite = channel.overwrites_for(me)
                expected = discord.PermissionOverwrite.from_pair(*current_overwrite.pair())
                expected.view_channel = True
                expected.manage_channels = True
                expected.read_message_history = True
                await channel.set_permissions(me, overwrite=expected, reason=f"Dank Shield scoped font rename access repair by {actor_id}")
                changed.append(_label(channel))
            except discord.Forbidden:
                denied += 1
            except Exception as exc:
                failed.append(f"{_label(channel)} — {type(exc).__name__}")

        if denied:
            failed.insert(0, f"Discord blocked auto-repair for {denied} channel/category permission edit(s). Manually grant Dank Shield View Channel + Manage Channels on the parent category, or move its role higher, then rerun preview.")
        if missing:
            failed.append(f"{missing} preview target(s) no longer exist or are not cached.")
        return {"changed": changed, "unchanged": unchanged, "failed": failed[:3]}

    setattr(_repair, "_permission_error_guard_wrapped", True)
    repair._repair = _repair  # type: ignore[attr-defined]
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    ok_custom = _patch_full_customization()
    ok_font = _patch_font_repair()
    _PATCHED = bool(ok_custom or ok_font)
    if _PATCHED:
        _log(f"active custom={ok_custom} font_repair={ok_font}")
    return _PATCHED


apply()

__all__ = ["apply"]
