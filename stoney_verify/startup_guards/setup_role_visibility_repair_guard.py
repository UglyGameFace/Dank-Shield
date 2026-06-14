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


def _target_exists(targets: list[Any], channel: Any) -> bool:
    try:
        cid = int(getattr(channel, "id", 0) or 0)
        return any(int(getattr(getattr(item, "channel", None), "id", 0) or 0) == cid for item in targets)
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


def _add_public_readonly_targets(guild: discord.Guild, cfg: Any, targets: list[Any], seen: set[int], repair: Any, waiting_role: discord.Role, notes: list[str]) -> None:
    added = 0
    for cid in sorted(_public_ids(cfg)):
        channel = _channel(guild, cid)
        if channel is None or _target_exists(targets, channel):
            continue
        if _can_talk(channel, waiting_role):
            repair._add_target(
                targets,
                seen,
                channel,
                "Onboarding read-only channel",
                {waiting_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)},
            )
            added += 1
    if added:
        notes.append(f"Added {added} read-only onboarding repair target(s) so Unverified can view setup channels without chatting there.")


def _add_safe_parent_visibility_targets(guild: discord.Guild, cfg: Any, targets: list[Any], seen: set[int], repair: Any, waiting_role: discord.Role, notes: list[str]) -> None:
    added = 0
    private_ids = _private_ids(cfg)
    for cid in sorted(_public_ids(cfg)):
        channel = _channel(guild, cid)
        parent = getattr(channel, "category", None)
        if channel is None or parent is None:
            continue
        parent_id = _int(getattr(parent, "id", 0))
        if parent_id <= 0 or parent_id in private_ids:
            continue
        if _can_see(channel, waiting_role) and not _can_see(parent, waiting_role):
            repair._add_target(
                targets,
                seen,
                parent,
                "Onboarding parent category visibility",
                {waiting_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)},
            )
            added += 1
    if added:
        notes.append(f"Added {added} parent-category visibility repair target(s) so Discord shows onboarding channels in the right category.")


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

                seen = {int(getattr(getattr(item, "channel", None), "id", 0) or 0) for item in targets}
                _add_public_readonly_targets(guild, cfg, targets, seen, repair, waiting_role, notes)
                _add_safe_parent_visibility_targets(guild, cfg, targets, seen, repair, waiting_role, notes)

                items = visibility._unverified_leaks(guild, cfg, waiting_role)
                if not items:
                    return targets, notes
                ow = {waiting_role: discord.PermissionOverwrite(view_channel=False)}
                added = 0
                for channel in items[:150]:
                    if _target_exists(targets, channel):
                        continue
                    repair._add_target(targets, seen, channel, "Role visibility alignment", ow)
                    added += 1
                if added:
                    notes.append(f"Added {added} role visibility alignment target(s) for the saved Unverified role.")
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
