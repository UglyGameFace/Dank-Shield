from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Sequence

import discord

from ..globals import get_supabase
from ..guild_config import discover_runtime_guild_config
from .panel_repository import (
    DEFAULT_PANEL_RULES,
    build_panel_runtime_config,
    panel_creation_guard_scope,
)

try:
    from .guardrails import evaluate_ticket_creation_guardrails
except Exception:
    async def evaluate_ticket_creation_guardrails(*, guild_id: int, user_id: int) -> Dict[str, Any]:  # type: ignore
        return {
            "ok": True,
            "reason": "",
            "source": "allow",
            "settings": {},
            "blacklist": None,
        }


# ============================================================
# tickets_new/panel_rules.py
# ------------------------------------------------------------
# Runtime rules layer for panel creation/access decisions.
#
# Portability design:
# - DB guild_config is preferred.
# - Runtime Discord discovery is allowed when configured.
# - .env values are fallback only through guild_config.
# - Missing server-specific config never crashes the bot.
#
# Legal / privacy posture:
# - no hidden cross-guild config sharing
# - no role/channel guessing writes to DB from this file
# - no extra user profiling here beyond server role-state checks
# - server owners remain responsible for informing users about
#   ticket logs/transcripts according to their server rules
# ============================================================


DEFAULT_RULE_OVERRIDES: Dict[str, Any] = {
    # Public-server friendly default:
    # if a server has not configured roles yet, do not hard-break ticket intake.
    "allow_unknown_members": True,
}


# ============================================================
# Small helpers
# ============================================================

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _slugify(value: Any, limit: int = 120) -> str:
    raw = _safe_str(value).lower()
    out: list[str] = []
    prev_dash = False

    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True

    return "".join(out).strip("-")[:limit]


def _normalized_category_list(values: Sequence[Any]) -> list[str]:
    out: list[str] = []

    for value in values or []:
        slug = _slugify(value)
        if slug and slug not in out:
            out.append(slug)

    return out


def _member_has_role_id(member: discord.Member, role_id: Any) -> bool:
    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return False

    try:
        return any(int(getattr(role, "id", 0) or 0) == rid for role in (member.roles or []))
    except Exception:
        return False


# ============================================================
# DB helpers
# ============================================================

def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _guild_member_row_sync(guild_id: int, user_id: int) -> Dict[str, Any]:
    sb = _sb()
    if sb is None:
        return {}

    try:
        res = (
            sb.table("guild_members")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return dict(rows[0])
    except Exception:
        return {}

    return {}


async def _guild_member_row(guild_id: int, user_id: int) -> Dict[str, Any]:
    return await asyncio.to_thread(_guild_member_row_sync, guild_id, user_id)


# ============================================================
# Role state resolution
# ============================================================

def _is_staff_by_permissions(member: discord.Member) -> bool:
    try:
        perms = member.guild_permissions
        return bool(
            perms.administrator
            or perms.manage_guild
            or perms.manage_roles
            or perms.manage_channels
            or perms.kick_members
            or perms.ban_members
            or perms.moderate_members
        )
    except Exception:
        return False


def _role_names(member: discord.Member) -> list[str]:
    out: list[str] = []

    try:
        for role in member.roles or []:
            name = _safe_str(getattr(role, "name", "")).lower()
            if name:
                out.append(name)
    except Exception:
        return out

    return out


def _role_state_from_names(member: discord.Member) -> str:
    names = _role_names(member)

    staff_markers = {
        "staff",
        "mod",
        "moderator",
        "admin",
        "administrator",
        "helper",
        "support",
        "ticket staff",
    }
    resident_markers = {"resident"}
    verified_markers = {"verified"}
    unverified_markers = {"unverified", "un-verified", "not verified", "pending"}

    for name in names:
        if any(marker in name for marker in staff_markers):
            return "staff"

    for name in names:
        if any(marker in name for marker in resident_markers):
            return "resident"

    for name in names:
        if any(marker in name for marker in verified_markers):
            return "verified"

    for name in names:
        if any(marker in name for marker in unverified_markers):
            return "unverified"

    return "unknown"


async def _role_state_from_guild_config(member: discord.Member) -> str:
    """
    Prefer per-server guild_config.

    guild_config may itself use .env as fallback if the server owner/deployer
    allows it, but this file does not directly rely on .env role IDs.
    """
    try:
        config = await discover_runtime_guild_config(member.guild)

        staff_role_id = config.get("staff_role_id")
        resident_role_id = config.get("resident_role_id")
        verified_role_id = config.get("verified_role_id")
        unverified_role_id = config.get("unverified_role_id")

        if _member_has_role_id(member, staff_role_id):
            return "staff"
        if _member_has_role_id(member, resident_role_id):
            return "resident"
        if _member_has_role_id(member, verified_role_id):
            return "verified"
        if _member_has_role_id(member, unverified_role_id):
            return "unverified"
    except Exception:
        pass

    return "unknown"


async def resolve_member_role_state(member: discord.Member) -> str:
    """
    Resolve member role-state without requiring per-server .env setup.

    Order:
    1. Discord permissions for staff safety.
    2. DB guild_members.role_state if your sync pipeline maintains it.
    3. DB/runtime guild_config role IDs, with .env fallback only if enabled there.
    4. Role-name fallback for new public servers.
    5. unknown.
    """
    if _is_staff_by_permissions(member):
        return "staff"

    try:
        row = await _guild_member_row(int(member.guild.id), int(member.id))
        db_state = _safe_str(row.get("role_state")).lower()
        if db_state in {"staff", "resident", "verified", "unverified"}:
            return db_state
    except Exception:
        pass

    config_state = await _role_state_from_guild_config(member)
    if config_state in {"staff", "resident", "verified", "unverified"}:
        return config_state

    fallback_state = _role_state_from_names(member)
    if fallback_state in {"staff", "resident", "verified", "unverified"}:
        return fallback_state

    return "unknown"


# ============================================================
# Rule merging / interpretation
# ============================================================

def _merge_rules(panel_rules: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(DEFAULT_PANEL_RULES)
    merged.update(DEFAULT_RULE_OVERRIDES)
    merged.update(dict(panel_rules or {}))

    merged["cooldown_seconds"] = max(
        0,
        _safe_int(merged.get("cooldown_seconds"), DEFAULT_PANEL_RULES["cooldown_seconds"]),
    )
    merged["max_tickets_per_window"] = max(
        0,
        _safe_int(merged.get("max_tickets_per_window"), DEFAULT_PANEL_RULES["max_tickets_per_window"]),
    )
    merged["window_minutes"] = max(
        0,
        _safe_int(merged.get("window_minutes"), DEFAULT_PANEL_RULES["window_minutes"]),
    )
    merged["auto_close_enabled"] = _safe_bool(
        merged.get("auto_close_enabled"),
        DEFAULT_PANEL_RULES["auto_close_enabled"],
    )
    merged["auto_close_minutes"] = max(
        5,
        _safe_int(merged.get("auto_close_minutes"), DEFAULT_PANEL_RULES["auto_close_minutes"]),
    )
    merged["inactivity_reminders_enabled"] = _safe_bool(
        merged.get("inactivity_reminders_enabled"),
        DEFAULT_PANEL_RULES["inactivity_reminders_enabled"],
    )
    merged["inactivity_reminder_minutes"] = max(
        1,
        _safe_int(
            merged.get("inactivity_reminder_minutes"),
            DEFAULT_PANEL_RULES["inactivity_reminder_minutes"],
        ),
    )
    merged["staff_alert_channel_id"] = _safe_str(merged.get("staff_alert_channel_id")) or None

    merged["allow_unverified"] = _safe_bool(
        merged.get("allow_unverified"),
        DEFAULT_PANEL_RULES["allow_unverified"],
    )
    merged["allow_verified"] = _safe_bool(
        merged.get("allow_verified"),
        DEFAULT_PANEL_RULES["allow_verified"],
    )
    merged["allow_resident"] = _safe_bool(
        merged.get("allow_resident"),
        DEFAULT_PANEL_RULES["allow_resident"],
    )
    merged["allow_staff"] = _safe_bool(
        merged.get("allow_staff"),
        DEFAULT_PANEL_RULES["allow_staff"],
    )
    merged["allow_unknown_members"] = _safe_bool(
        merged.get("allow_unknown_members"),
        DEFAULT_RULE_OVERRIDES["allow_unknown_members"],
    )

    merged["ghost_allowed"] = _safe_bool(
        merged.get("ghost_allowed"),
        DEFAULT_PANEL_RULES["ghost_allowed"],
    )
    merged["close_confirmation_required"] = _safe_bool(
        merged.get("close_confirmation_required"),
        DEFAULT_PANEL_RULES["close_confirmation_required"],
    )
    merged["per_owner_open_limit"] = max(
        1,
        _safe_int(
            merged.get("per_owner_open_limit"),
            DEFAULT_PANEL_RULES["per_owner_open_limit"],
        ),
    )
    merged["transcript_mode"] = _safe_str(
        merged.get("transcript_mode"),
        _safe_str(DEFAULT_PANEL_RULES["transcript_mode"], "on_close"),
    ).lower()

    return merged


def _panel_category_allowed(panel_bundle: Dict[str, Any], category_slug: Optional[str]) -> bool:
    categories = _normalized_category_list(panel_bundle.get("categories") or [])
    if not categories:
        return True

    slug = _slugify(category_slug)
    if not slug:
        return False

    return slug in categories


def _member_allowed_by_rules(role_state: str, rules: Dict[str, Any]) -> tuple[bool, str]:
    state = _safe_str(role_state).lower()

    if state == "staff":
        return (
            _safe_bool(rules.get("allow_staff"), True),
            "Staff are not allowed to use this panel.",
        )

    if state == "resident":
        return (
            _safe_bool(rules.get("allow_resident"), True),
            "Residents are not allowed to use this panel.",
        )

    if state == "verified":
        return (
            _safe_bool(rules.get("allow_verified"), True),
            "Verified members are not allowed to use this panel.",
        )

    if state == "unverified":
        return (
            _safe_bool(rules.get("allow_unverified"), True),
            "Unverified members are not allowed to use this panel.",
        )

    return (
        _safe_bool(rules.get("allow_unknown_members"), True),
        "Your current role state is not allowed to use this panel.",
    )


def _ghost_allowed(rules: Dict[str, Any], is_ghost: bool) -> tuple[bool, str]:
    if not is_ghost:
        return (True, "")

    if _safe_bool(rules.get("ghost_allowed"), False):
        return (True, "")

    return (False, "Ghost ticket creation is not allowed for this panel.")


def _panel_runtime_summary(panel_bundle: Dict[str, Any]) -> Dict[str, Any]:
    panel = dict(panel_bundle.get("panel") or {})
    rules = _merge_rules(panel_bundle.get("rules") or {})
    categories = _normalized_category_list(panel_bundle.get("categories") or [])
    preset = dict(panel_bundle.get("preset") or {})

    return {
        "panel_key": _safe_str(panel.get("panel_key")),
        "panel_name": _safe_str(panel.get("panel_name")),
        "panel_style": _safe_str(panel.get("panel_style")),
        "panel_channel_id": _safe_str(panel.get("panel_channel_id")),
        "panel_message_id": _safe_str(panel.get("panel_message_id")),
        "is_enabled": _safe_bool(panel.get("is_enabled"), True),
        "categories": categories,
        "rules": rules,
        "preset_key": _safe_str(panel.get("preset_key") or preset.get("preset_key")),
        "prompt_title": _safe_str(panel.get("prompt_title") or preset.get("default_prompt_title")),
        "prompt_description": _safe_str(
            panel.get("prompt_description")
            or preset.get("default_prompt_description")
        ),
    }


# ============================================================
# Public runtime helpers
# ============================================================

async def get_effective_panel_runtime(
    *,
    guild_id: Any,
    panel_key: Any,
) -> Optional[Dict[str, Any]]:
    bundle = await build_panel_runtime_config(guild_id, panel_key)
    if bundle is None:
        return None
    return _panel_runtime_summary(bundle)


async def get_panel_access_snapshot(
    *,
    member: discord.Member,
    panel_key: Any,
    category_slug: Optional[str] = None,
    is_ghost: bool = False,
) -> Dict[str, Any]:
    runtime = await get_effective_panel_runtime(
        guild_id=member.guild.id,
        panel_key=panel_key,
    )

    if runtime is None:
        return {
            "ok": False,
            "reason": "This ticket panel no longer exists.",
            "source": "panel_missing",
            "panel": None,
            "rules": dict(_merge_rules({})),
            "role_state": "unknown",
            "category_allowed": False,
            "member_allowed": False,
            "ghost_allowed": False,
        }

    if not _safe_bool(runtime.get("is_enabled"), True):
        return {
            "ok": False,
            "reason": "This ticket panel is currently disabled.",
            "source": "panel_disabled",
            "panel": runtime,
            "rules": dict(runtime.get("rules") or {}),
            "role_state": "unknown",
            "category_allowed": False,
            "member_allowed": False,
            "ghost_allowed": False,
        }

    rules = _merge_rules(runtime.get("rules") or {})
    role_state = await resolve_member_role_state(member)
    category_allowed = _panel_category_allowed(runtime, category_slug)
    member_allowed, member_reason = _member_allowed_by_rules(role_state, rules)
    ghost_allowed, ghost_reason = _ghost_allowed(rules, is_ghost)

    if not category_allowed:
        return {
            "ok": False,
            "reason": "That category is not available for this panel.",
            "source": "category_not_allowed",
            "panel": runtime,
            "rules": rules,
            "role_state": role_state,
            "category_allowed": False,
            "member_allowed": member_allowed,
            "ghost_allowed": ghost_allowed,
        }

    if not member_allowed:
        return {
            "ok": False,
            "reason": member_reason,
            "source": "role_not_allowed",
            "panel": runtime,
            "rules": rules,
            "role_state": role_state,
            "category_allowed": True,
            "member_allowed": False,
            "ghost_allowed": ghost_allowed,
        }

    if not ghost_allowed:
        return {
            "ok": False,
            "reason": ghost_reason,
            "source": "ghost_not_allowed",
            "panel": runtime,
            "rules": rules,
            "role_state": role_state,
            "category_allowed": True,
            "member_allowed": True,
            "ghost_allowed": False,
        }

    return {
        "ok": True,
        "reason": "",
        "source": "allow",
        "panel": runtime,
        "rules": rules,
        "role_state": role_state,
        "category_allowed": True,
        "member_allowed": True,
        "ghost_allowed": True,
    }


async def evaluate_panel_creation_request(
    *,
    member: discord.Member,
    panel_key: Any,
    category_slug: Optional[str] = None,
    is_ghost: bool = False,
) -> Dict[str, Any]:
    access = await get_panel_access_snapshot(
        member=member,
        panel_key=panel_key,
        category_slug=category_slug,
        is_ghost=is_ghost,
    )

    if not access.get("ok"):
        return access

    global_guard = await evaluate_ticket_creation_guardrails(
        guild_id=int(member.guild.id),
        user_id=int(member.id),
    )

    if not _safe_bool(global_guard.get("ok"), True):
        return {
            "ok": False,
            "reason": _safe_str(
                global_guard.get("reason"),
                "You cannot create a ticket right now.",
            ),
            "source": _safe_str(global_guard.get("source"), "guardrails"),
            "panel": access.get("panel"),
            "rules": access.get("rules"),
            "role_state": access.get("role_state"),
            "global_guard": global_guard,
        }

    rules = dict(access.get("rules") or {})

    return {
        "ok": True,
        "reason": "",
        "source": "allow",
        "panel": access.get("panel"),
        "rules": rules,
        "role_state": access.get("role_state"),
        "global_guard": global_guard,
        "cooldown_seconds": _safe_int(rules.get("cooldown_seconds"), 0),
        "max_tickets_per_window": _safe_int(rules.get("max_tickets_per_window"), 0),
        "window_minutes": _safe_int(rules.get("window_minutes"), 0),
        "per_owner_open_limit": _safe_int(rules.get("per_owner_open_limit"), 1),
    }


@asynccontextmanager
async def panel_creation_guard(
    *,
    member: discord.Member,
    panel_key: Any,
    semaphore_limit: int = 8,
):
    sem, lock = await panel_creation_guard_scope(
        guild_id=member.guild.id,
        owner_id=member.id,
        panel_key=panel_key,
        semaphore_limit=semaphore_limit,
    )

    async with sem:
        async with lock:
            yield


def panel_rules_for_automation(runtime: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not runtime:
        return _merge_rules({})
    return _merge_rules(runtime.get("rules") or {})


def panel_transcript_mode(runtime: Optional[Dict[str, Any]]) -> str:
    rules = panel_rules_for_automation(runtime)
    return _safe_str(rules.get("transcript_mode"), "on_close").lower()


def panel_close_confirmation_required(runtime: Optional[Dict[str, Any]]) -> bool:
    rules = panel_rules_for_automation(runtime)
    return _safe_bool(rules.get("close_confirmation_required"), True)


def panel_staff_alert_channel_id(runtime: Optional[Dict[str, Any]]) -> Optional[str]:
    rules = panel_rules_for_automation(runtime)
    value = _safe_str(rules.get("staff_alert_channel_id"))
    return value or None


def panel_owner_open_limit(runtime: Optional[Dict[str, Any]]) -> int:
    rules = panel_rules_for_automation(runtime)
    return max(1, _safe_int(rules.get("per_owner_open_limit"), 1))


async def panel_runtime_from_message_binding(
    *,
    guild_id: Any,
    panel_key: Any = None,
) -> Optional[Dict[str, Any]]:
    if not panel_key:
        return None

    return await get_effective_panel_runtime(
        guild_id=guild_id,
        panel_key=panel_key,
    )


def panel_allows_role_state(runtime: Optional[Dict[str, Any]], role_state: str) -> bool:
    rules = panel_rules_for_automation(runtime)
    state = _safe_str(role_state).lower()

    if state == "staff":
        return _safe_bool(rules.get("allow_staff"), True)
    if state == "resident":
        return _safe_bool(rules.get("allow_resident"), True)
    if state == "verified":
        return _safe_bool(rules.get("allow_verified"), True)
    if state == "unverified":
        return _safe_bool(rules.get("allow_unverified"), True)

    return _safe_bool(rules.get("allow_unknown_members"), True)


async def panel_is_category_enabled(
    *,
    guild_id: Any,
    panel_key: Any,
    category_slug: Any,
) -> bool:
    runtime = await get_effective_panel_runtime(
        guild_id=guild_id,
        panel_key=panel_key,
    )

    if runtime is None:
        return False

    return _panel_category_allowed(runtime, _slugify(category_slug))
