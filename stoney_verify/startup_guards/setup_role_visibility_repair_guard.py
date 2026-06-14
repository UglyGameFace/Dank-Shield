from __future__ import annotations

"""Extend setup permission repair with role visibility alignment targets."""

from typing import Any

import discord

_DONE = False


def _int(value: Any) -> int:
    try:
        return int(str(value or "0").strip() or "0")
    except Exception:
        return 0


def _first(cfg: Any, *names: str) -> int:
    for name in names:
        try:
            raw = cfg.get(name) if hasattr(cfg, "get") else getattr(cfg, name, 0)
        except Exception:
            raw = 0
        value = _int(raw)
        if value > 0:
            return value
    return 0


def _role_from_config(guild: discord.Guild, cfg: Any, name: str) -> discord.Role | None:
    role = guild.get_role(_first(cfg, name))
    return role if isinstance(role, discord.Role) else None


def _target_id(channel: Any) -> int:
    try:
        return int(getattr(channel, "id", 0) or 0)
    except Exception:
        return 0


def _target_exists(targets: list[Any], channel: Any) -> bool:
    cid = _target_id(channel)
    if cid <= 0:
        return False
    try:
        return any(_target_id(getattr(item, "channel", None)) == cid for item in targets)
    except Exception:
        return False


def _channel(guild: discord.Guild, channel_id: int) -> Any:
    try:
        return guild.get_channel(int(channel_id)) if int(channel_id or 0) > 0 else None
    except Exception:
        return None


def _can_see(obj: Any, role: discord.Role | None) -> bool:
    try:
        return bool(role and obj and obj.permissions_for(role).view_channel)
    except Exception:
        return False


def _can_talk(obj: Any, role: discord.Role | None) -> bool:
    try:
        return bool(role and obj and getattr(obj.permissions_for(role), "send_messages", False))
    except Exception:
        return False


def _label(obj: Any) -> str:
    try:
        return str(getattr(obj, "mention", None) or f"`{getattr(obj, 'name', 'unknown')}`")
    except Exception:
        return "`unknown`"


def _public_ids(cfg: Any) -> set[int]:
    return {
        value
        for value in {
            _first(cfg, "start_category_id", "welcome_category_id"),
            _first(cfg, "welcome_channel_id"),
            _first(cfg, "rules_channel_id"),
            _first(cfg, "announcements_channel_id", "announcement_channel_id"),
            _first(cfg, "verify_channel_id", "verification_channel_id"),
            _first(cfg, "ticket_panel_channel_id", "support_channel_id", "panel_channel_id"),
            _first(cfg, "vc_verify_channel_id", "voice_verify_channel_id"),
        }
        if value > 0
    }


def _private_ids(cfg: Any) -> set[int]:
    return {
        value
        for value in {
            _first(cfg, "ticket_category_id", "active_ticket_category_id", "open_ticket_category_id"),
            _first(cfg, "ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id"),
            _first(cfg, "management_category_id", "staff_tools_category_id"),
            _first(cfg, "transcripts_channel_id", "transcript_channel_id"),
            _first(cfg, "modlog_channel_id", "mod_log_channel_id"),
            _first(cfg, "raidlog_channel_id", "raid_log_channel_id", "security_log_channel_id"),
            _first(cfg, "join_log_channel_id", "join_leave_log_channel_id", "joinlog_channel_id"),
            _first(cfg, "force_verify_log_channel_id", "forced_verify_log_channel_id"),
            _first(cfg, "status_channel_id", "bot_status_channel_id"),
            _first(cfg, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id"),
        }
        if value > 0
    }


def _remember_fix(notes: list[str], message: str) -> None:
    text = str(message or "").strip()
    if text and text not in notes:
        notes.append(text)


def _add_target_if_needed(targets: list[Any], seen: set[int], repair: Any, channel: Any, label: str, overwrites: dict[Any, discord.PermissionOverwrite]) -> bool:
    before = len(targets)
    repair._add_target(targets, seen, channel, label, overwrites)
    return len(targets) > before


def _add_public_readonly_targets(guild: discord.Guild, cfg: Any, targets: list[Any], seen: set[int], repair: Any, waiting_role: discord.Role, notes: list[str]) -> None:
    channels_to_fix: list[Any] = []
    overwrites = {waiting_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)}
    for cid in sorted(_public_ids(cfg)):
        channel = _channel(guild, cid)
        if channel is None:
            continue
        if _can_talk(channel, waiting_role):
            channels_to_fix.append(channel)
            _add_target_if_needed(targets, seen, repair, channel, "Onboarding read-only channel", overwrites)

    if channels_to_fix:
        preview = ", ".join(_label(ch) for ch in channels_to_fix[:4])
        extra = f" and {len(channels_to_fix) - 4} more" if len(channels_to_fix) > 4 else ""
        _remember_fix(
            notes,
            f"Will fix Setup Health warning: Unverified can send messages in onboarding channel(s). Action: keep visible, remove Send Messages for {len(channels_to_fix)} saved onboarding target(s): {preview}{extra}.",
        )


def _add_safe_parent_visibility_targets(guild: discord.Guild, cfg: Any, targets: list[Any], seen: set[int], repair: Any, waiting_role: discord.Role, notes: list[str]) -> None:
    private_ids = _private_ids(cfg)
    parent_targets: dict[int, Any] = {}
    child_count = 0
    overwrites = {waiting_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)}

    for cid in sorted(_public_ids(cfg)):
        channel = _channel(guild, cid)
        if channel is None:
            continue
        parent = getattr(channel, "category", None)
        if parent is None:
            continue
        parent_id = _target_id(parent)
        if parent_id <= 0 or parent_id in private_ids:
            continue
        if _can_see(channel, waiting_role) and not _can_see(parent, waiting_role):
            child_count += 1
            parent_targets[parent_id] = parent

    for parent in parent_targets.values():
        _add_target_if_needed(targets, seen, repair, parent, "Onboarding parent category visibility", overwrites)

    if parent_targets:
        preview = ", ".join(_label(parent) for parent in list(parent_targets.values())[:3])
        extra = f" and {len(parent_targets) - 3} more" if len(parent_targets) > 3 else ""
        _remember_fix(
            notes,
            f"Will fix Setup Health warning: onboarding channel is visible but parent category is hidden. Action: reveal only {len(parent_targets)} safe parent category header(s) for Unverified ({preview}{extra}) covering {child_count} child channel(s).",
        )


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.startup_guards import setup_permission_repair_guard as repair
        from stoney_verify.startup_guards import setup_visibility_health_guard as visibility

        original = getattr(repair, "_build_targets", None)
        if not callable(original) or getattr(original, "_role_visibility_repair_wrapped", False):
            return False

        async def wrapped(guild: discord.Guild):
            targets, notes = await original(guild)
            try:
                cfg = await get_guild_config(guild.id, refresh=True)
                waiting_role = _role_from_config(guild, cfg, "unverified_role_id")
                if waiting_role is None:
                    return targets, notes

                seen = {_target_id(getattr(item, "channel", None)) for item in targets}
                _add_public_readonly_targets(guild, cfg, targets, seen, repair, waiting_role, notes)
                _add_safe_parent_visibility_targets(guild, cfg, targets, seen, repair, waiting_role, notes)

                items = visibility._unverified_leaks(guild, cfg, waiting_role)
                if not items:
                    return targets, notes
                ow = {waiting_role: discord.PermissionOverwrite(view_channel=False)}
                added_targets = 0
                for channel in items[:150]:
                    if _target_exists(targets, channel):
                        continue
                    if _add_target_if_needed(targets, seen, repair, channel, "Role visibility alignment", ow):
                        added_targets += 1
                if added_targets:
                    _remember_fix(
                        notes,
                        f"Will fix Setup Health warning: Unverified visibility leak. Action: hide {added_targets} private/staff/member-only target(s) from the saved Unverified role.",
                    )
            except Exception as exc:
                notes.append(f"Role visibility alignment scan failed: {type(exc).__name__}.")
            return targets, notes

        setattr(wrapped, "_role_visibility_repair_wrapped", True)
        repair._build_targets = wrapped
        _DONE = True
        print("🛡️ setup_role_visibility_repair_guard active; repair includes saved-role visibility alignment targets")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_role_visibility_repair_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
