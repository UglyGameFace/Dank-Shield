from __future__ import annotations

from typing import Any

import discord

_DONE = False


def _i(v: Any) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return 0


def _cv(cfg: Any, name: str) -> int:
    try:
        if hasattr(cfg, "get"):
            return _i(cfg.get(name))
        return _i(getattr(cfg, name, 0))
    except Exception:
        return 0


def _first(cfg: Any, names: tuple[str, ...]) -> int:
    for name in names:
        value = _cv(cfg, name)
        if value > 0:
            return value
    return 0


def _roles(guild: discord.Guild, cfg: Any, groups: tuple[tuple[str, ...], ...]) -> list[discord.Role]:
    out: list[discord.Role] = []
    for names in groups:
        role = guild.get_role(_first(cfg, names))
        if isinstance(role, discord.Role) and role not in out:
            out.append(role)
    return out


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
    return str(getattr(obj, "mention", None) or getattr(obj, "name", "unknown"))


def _target(guild: discord.Guild, cid: int) -> Any:
    try:
        return guild.get_channel(_i(cid)) if _i(cid) > 0 else None
    except Exception:
        return None


def _role_label(role: discord.Role | None) -> str:
    if role is None:
        return "role"
    try:
        return str(getattr(role, "mention", None) or f"@{role.name}")
    except Exception:
        return "role"


def _check_parent(target: Any, role: discord.Role | None) -> bool:
    try:
        parent = getattr(target, "category", None)
        return bool(parent is not None and _can_see(parent, role))
    except Exception:
        return False


def _check(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    unverified = guild.get_role(_first(cfg, ("unverified_role_id",)))
    verified_roles = _roles(
        guild,
        cfg,
        (
            ("verified_role_id",),
            ("resident_role_id", "member_role_id"),
        ),
    )
    if unverified is None:
        warnings.append("Unverified visibility check skipped because the role is not saved.")

    public = {
        _first(cfg, ("start_category_id", "welcome_category_id")),
        _first(cfg, ("welcome_channel_id",)),
        _first(cfg, ("verify_channel_id", "verification_channel_id")),
        _first(cfg, ("ticket_panel_channel_id", "support_channel_id", "panel_channel_id")),
        _first(cfg, ("vc_verify_channel_id", "voice_verify_channel_id")),
    }
    private = {
        _first(cfg, ("ticket_category_id", "active_ticket_category_id", "open_ticket_category_id")),
        _first(cfg, ("ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id")),
        _first(cfg, ("management_category_id", "staff_tools_category_id")),
        _first(cfg, ("transcripts_channel_id", "transcript_channel_id")),
        _first(cfg, ("modlog_channel_id", "mod_log_channel_id")),
        _first(cfg, ("raidlog_channel_id", "raid_log_channel_id", "security_log_channel_id")),
        _first(cfg, ("join_log_channel_id", "join_leave_log_channel_id", "joinlog_channel_id")),
        _first(cfg, ("force_verify_log_channel_id", "forced_verify_log_channel_id")),
        _first(cfg, ("status_channel_id", "bot_status_channel_id")),
        _first(cfg, ("vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id")),
    }
    member_public = {
        _first(cfg, ("general_channel_id", "member_chat_channel_id", "lounge_channel_id")),
        _first(cfg, ("rules_channel_id",)),
        _first(cfg, ("announcements_channel_id", "announcement_channel_id")),
    }

    public_checked = 0
    private_checked = 0
    member_checked = 0

    for cid in {x for x in public if x > 0}:
        ch = _target(guild, cid)
        if ch is None:
            continue
        public_checked += 1
        if unverified is not None:
            if not _can_see(ch, unverified):
                warnings.append(f"{_label(ch)} should be visible to Unverified for onboarding.")
            if _can_talk(ch, unverified):
                warnings.append(f"{_label(ch)} lets Unverified send messages; setup default expects read-only.")

    for cid in {x for x in private if x > 0}:
        ch = _target(guild, cid)
        if ch is None:
            continue
        private_checked += 1
        if unverified is not None:
            if _can_see(ch, unverified):
                blockers.append(f"{_label(ch)} is visible to Unverified but should be private/staff controlled.")
            if _check_parent(ch, unverified):
                blockers.append(f"{_label(getattr(ch, 'category', None))} category is visible to Unverified.")
        for role in verified_roles:
            if _can_see(ch, role):
                blockers.append(f"{_label(ch)} is visible to {_role_label(role)} but should stay staff/private controlled.")
            if _check_parent(ch, role):
                blockers.append(f"{_label(getattr(ch, 'category', None))} category is visible to {_role_label(role)} but should stay private.")

    for cid in {x for x in member_public if x > 0}:
        ch = _target(guild, cid)
        if ch is None:
            continue
        member_checked += 1
        for role in verified_roles:
            if not _can_see(ch, role):
                warnings.append(f"{_label(ch)} should be visible to {_role_label(role)} for normal member access.")
        if unverified is not None and _can_see(ch, unverified):
            warnings.append(f"{_label(ch)} is visible to Unverified; if this is member-only, run Safety & Repair.")

    ok.append(f"Role visibility checked: onboarding={public_checked}, private={private_checked}, member={member_checked}.")
    if verified_roles:
        ok.append("Verified/member role privacy checks are active for saved private setup targets.")
    else:
        warnings.append("Verified/member role privacy check skipped because no Verified/Resident role is saved.")


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_group as group
        original = getattr(group, "_build_setup_health", None)
        if not callable(original) or getattr(original, "_visibility_wrapped", False):
            return False
        def wrapped(guild: discord.Guild, cfg: Any):
            blockers, warnings, ok = original(guild, cfg)
            try:
                _check(guild, cfg, blockers, warnings, ok)
            except Exception as exc:
                warnings.append(f"Role visibility check failed: {type(exc).__name__}.")
            return blockers, warnings, ok
        setattr(wrapped, "_visibility_wrapped", True)
        group._build_setup_health = wrapped
        _DONE = True
        print("🛡️ setup_visibility_health_guard active; setup health checks role visibility privacy")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_visibility_health_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
