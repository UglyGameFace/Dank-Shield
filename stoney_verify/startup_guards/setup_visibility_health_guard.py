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


def _public_ids(cfg: Any) -> set[int]:
    return {
        x
        for x in {
            _first(cfg, ("start_category_id", "welcome_category_id")),
            _first(cfg, ("welcome_channel_id",)),
            _first(cfg, ("rules_channel_id",)),
            _first(cfg, ("announcements_channel_id", "announcement_channel_id")),
            _first(cfg, ("verify_channel_id", "verification_channel_id")),
            _first(cfg, ("ticket_panel_channel_id", "support_channel_id", "panel_channel_id")),
            _first(cfg, ("vc_verify_channel_id", "voice_verify_channel_id")),
        }
        if x > 0
    }


def _private_ids(cfg: Any) -> set[int]:
    return {
        x
        for x in {
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
        if x > 0
    }


def _member_ids(cfg: Any) -> set[int]:
    return {
        x
        for x in {
            _first(cfg, ("general_channel_id", "member_chat_channel_id", "lounge_channel_id")),
        }
        if x > 0
    }


def _all_channel_targets(guild: discord.Guild) -> list[Any]:
    targets: list[Any] = []
    targets.extend(list(getattr(guild, "categories", []) or []))
    targets.extend(list(getattr(guild, "channels", []) or []))
    seen: set[int] = set()
    out: list[Any] = []
    for item in targets:
        try:
            cid = int(getattr(item, "id", 0) or 0)
        except Exception:
            cid = 0
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        out.append(item)
    return out


def _is_onboarding_allowed(target: Any, allowed_ids: set[int]) -> bool:
    try:
        cid = int(getattr(target, "id", 0) or 0)
        return cid in allowed_ids
    except Exception:
        return False


def _unverified_leaks(guild: discord.Guild, cfg: Any, unverified: discord.Role | None) -> list[Any]:
    if unverified is None:
        return []
    allowed = _public_ids(cfg)
    leaks: list[Any] = []
    for target in _all_channel_targets(guild):
        if _is_onboarding_allowed(target, allowed):
            continue
        if _can_see(target, unverified):
            leaks.append(target)
    return leaks


def _vc_category_notice(guild: discord.Guild, cfg: Any, unverified: discord.Role | None, warnings: list[str]) -> None:
    vc = _target(guild, _first(cfg, ("vc_verify_channel_id", "voice_verify_channel_id")))
    if vc is None:
        return
    parent = getattr(vc, "category", None)
    if parent is None:
        return
    management = _first(cfg, ("management_category_id", "staff_tools_category_id"))
    private = _private_ids(cfg)
    parent_id = _i(getattr(parent, "id", 0))
    if parent_id == management or parent_id in private:
        warnings.append(
            f"{_label(vc)} is onboarding-visible VC verification but it is inside {_label(parent)}. "
            "Discord will show that category to Unverified users because they can see the VC child. "
            "Move VC verification to a public verification/onboarding category, or run the VC setup/fix flow to place it correctly."
        )
    elif unverified is not None and _can_see(vc, unverified) and not _can_see(parent, unverified):
        warnings.append(
            f"{_label(vc)} is visible to Unverified but its parent category {_label(parent)} is hidden. "
            "Discord may show confusing category behavior; prefer placing VC verification in the onboarding category."
        )


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

    public = _public_ids(cfg)
    private = _private_ids(cfg)
    member_public = _member_ids(cfg)

    public_checked = 0
    private_checked = 0
    member_checked = 0

    for cid in public:
        ch = _target(guild, cid)
        if ch is None:
            continue
        public_checked += 1
        if unverified is not None:
            if not _can_see(ch, unverified):
                warnings.append(f"{_label(ch)} should be visible to Unverified for onboarding.")
            if _can_talk(ch, unverified):
                warnings.append(f"{_label(ch)} lets Unverified send messages; setup default expects read-only.")

    for cid in private:
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

    for cid in member_public:
        ch = _target(guild, cid)
        if ch is None:
            continue
        member_checked += 1
        for role in verified_roles:
            if not _can_see(ch, role):
                warnings.append(f"{_label(ch)} should be visible to {_role_label(role)} for normal member access.")
        if unverified is not None and _can_see(ch, unverified):
            blockers.append(f"{_label(ch)} is visible to Unverified; member-only areas must be hidden until verification.")

    _vc_category_notice(guild, cfg, unverified, warnings)

    leaks = _unverified_leaks(guild, cfg, unverified)
    if leaks:
        shown = ", ".join(_label(item) for item in leaks[:12])
        extra = f" and {len(leaks) - 12} more" if len(leaks) > 12 else ""
        blockers.append(f"Unverified visibility leak: {len(leaks)} non-onboarding channels/categories visible ({shown}{extra}).")

    ok.append(f"Role visibility checked: onboarding={public_checked}, private={private_checked}, member={member_checked}, full_unverified_scan={len(_all_channel_targets(guild))}.")
    if verified_roles:
        ok.append("Verified/member role privacy checks are active for saved private setup targets.")
    else:
        warnings.append("Verified/member role privacy check skipped because no Verified/Resident role is saved.")


def _load_repair_alignment() -> None:
    try:
        from stoney_verify.startup_guards import setup_role_visibility_repair_guard
        setup_role_visibility_repair_guard.apply()
    except Exception as exc:
        try:
            print(f"⚠️ setup_visibility_health_guard repair alignment failed: {exc!r}")
        except Exception:
            pass


def apply() -> bool:
    global _DONE
    _load_repair_alignment()
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
        print("🛡️ setup_visibility_health_guard active; setup health scans all channels for Unverified leaks and VC category placement")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_visibility_health_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply", "_unverified_leaks", "_public_ids"]
