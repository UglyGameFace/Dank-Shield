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


def _saved_public_ids(cfg: Any) -> set[int]:
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


def _name(obj: Any) -> str:
    try:
        return str(getattr(obj, "name", "") or "").lower().replace("_", "-").replace(" ", "-")
    except Exception:
        return ""


def _looks_like_onboarding_public(target: Any) -> bool:
    name = _name(target)
    if not name:
        return False
    public_tokens = (
        "welcome",
        "rule",
        "verify",
        "verification",
        "support",
        "ticket",
        "start",
        "onboard",
        "onboarding",
        "central-command",
        "command",
    )
    private_tokens = (
        "staff",
        "mod",
        "admin",
        "transcript",
        "log",
        "archive",
        "ticket-0",
        "active-ticket",
    )
    return any(token in name for token in public_tokens) and not any(token in name for token in private_tokens)


def _public_ids(cfg: Any, guild: discord.Guild | None = None) -> set[int]:
    allowed = set(_saved_public_ids(cfg))
    if guild is None:
        return allowed
    for cid in list(allowed):
        channel = _target(guild, cid)
        parent = getattr(channel, "category", None)
        parent_id = _i(getattr(parent, "id", 0)) if parent is not None else 0
        if parent_id > 0:
            allowed.add(parent_id)
    private = _private_ids(cfg)
    for target in _all_channel_targets(guild):
        tid = _i(getattr(target, "id", 0))
        if tid <= 0 or tid in private:
            continue
        parent = getattr(target, "category", None)
        parent_id = _i(getattr(parent, "id", 0)) if parent is not None else 0
        if parent_id in private:
            continue
        if _looks_like_onboarding_public(target):
            allowed.add(tid)
            if parent_id > 0:
                allowed.add(parent_id)
    return allowed


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
    allowed = _public_ids(cfg, guild)
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
            f"{_label(vc)} is an onboarding VC verification channel, but it is inside private/staff category {_label(parent)}. "
            "Do not let automatic repair expose that category. Smart fix: use **Use My Existing Server → Discord Categories** to pick/create a public onboarding/start category, then move or reselect the VC verification channel there."
        )
    elif unverified is not None and _can_see(vc, unverified) and not _can_see(parent, unverified):
        warnings.append(
            f"{_label(vc)} is visible to Unverified but its parent category {_label(parent)} is hidden. "
            "Smart fix: **Safety & Repair → Fix Permissions** can safely reveal only that parent category header to Unverified so Discord displays the VC under the right category."
        )


def _check(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    # Deprecated compatibility hook. Canonical setup_engine now owns new setup
    # health reports; keep this callable for older imports that still use
    # _unverified_leaks / _public_ids while the cleanup continues.
    return None


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
        if not callable(original):
            return False
        if getattr(original, "_canonical_setup_engine", False):
            _DONE = True
            print("🛡️ setup_visibility_health_guard deprecated; canonical setup_engine owns Setup Health")
            return True
        if getattr(original, "_visibility_wrapped", False):
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
        print("🛡️ setup_visibility_health_guard compatibility active; canonical engine may override this adapter")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_visibility_health_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply", "_unverified_leaks", "_public_ids"]
