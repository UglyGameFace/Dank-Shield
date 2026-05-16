from __future__ import annotations

"""Startup loader for public member-update modlog coverage.

The main events.py listener already calls modlog.maybe_log_member_update_diff.
Registering the older public listener at the same time creates duplicate
Member Updated embeds for every role change.

Default behavior now keeps the extra listener off. It can still be enabled for
special deployments that intentionally do not load events.py by setting:

    DANK_ENABLE_EXTRA_MEMBER_UPDATE_MODLOG=true

This guard also trims routine CLEAR/LOW identity context from member-update
embeds so approvals do not spam the mod-log with repeated no-risk summaries.
"""

import os
from typing import Any, List, Tuple

_REGISTERED = False
_CONTEXT_PATCHED = False
_APPROVAL_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧍 member_update_modlog {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ member_update_modlog {message}")
    except Exception:
        pass


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _member_update_context_mode() -> str:
    mode = _env_str("DANK_MEMBER_UPDATE_CONTEXT_MODE", "risky").lower()
    if mode in {"off", "none", "false", "0"}:
        return "off"
    if mode in {"all", "full", "verbose", "true", "1"}:
        return "all"
    return "risky"


def _context_field_is_actionable(name: Any, value: Any) -> bool:
    field_name = str(name or "").strip().lower()
    text = str(value or "").strip().lower()
    if not text:
        return False

    if "identity" in field_name:
        return True

    if "alt summary" in field_name and "signals: no strong recent link evidence" in text:
        return False

    if "risk context" in field_name:
        clear_low_zero = (
            "clear" in text
            and "low / 0/100" in text
            and "flags:" not in text
            and "warns:" not in text
            and "shared fingerprint" not in text
            and "similar name count" not in text
            and "burst join count" not in text
        )
        if clear_low_zero:
            return False

    markers = (
        "confirmed duplicate",
        "likely same person",
        "proof_matches=",
        "manual_confirmed=",
        "manual_likely=",
        "not_linked=",
        "identity fingerprint",
        "critical /",
        "high /",
        "medium /",
        "warns:",
        "flags:",
        "shared fingerprint",
        "similar name count",
        "burst join count",
        "join burst:",
    )
    return any(marker in text for marker in markers)


def patch_member_update_context_noise() -> bool:
    global _CONTEXT_PATCHED
    if _CONTEXT_PATCHED:
        return True

    try:
        from stoney_verify import modlog
    except Exception as e:
        _warn(f"failed importing modlog for context patch: {e!r}")
        return False

    original = getattr(modlog, "_build_member_context_fields", None)
    if not callable(original):
        _warn("modlog._build_member_context_fields missing; context patch skipped")
        return False

    if getattr(original, "__dank_member_update_context_patch__", False):
        _CONTEXT_PATCHED = True
        return True

    async def filtered_build_member_context_fields(guild, member_or_user):  # type: ignore[no-untyped-def]
        fields = await original(guild, member_or_user)
        mode = _member_update_context_mode()
        if mode == "all":
            return fields
        if mode == "off":
            return []

        filtered: List[Tuple[str, str, bool]] = []
        for item in list(fields or []):
            try:
                name, value, inline = item
            except Exception:
                continue
            if _context_field_is_actionable(name, value):
                filtered.append((str(name), str(value), bool(inline)))
        return filtered

    filtered_build_member_context_fields.__dank_member_update_context_patch__ = True  # type: ignore[attr-defined]
    filtered_build_member_context_fields.__wrapped__ = original  # type: ignore[attr-defined]

    try:
        setattr(modlog, "_dank_original_build_member_context_fields", original)
        setattr(modlog, "_build_member_context_fields", filtered_build_member_context_fields)
        _CONTEXT_PATCHED = True
        _log("trimmed clear low-risk context from routine member-update logs")
        return True
    except Exception as e:
        _warn(f"failed installing context patch: {e!r}")
        return False


def _approval_role_churn_enabled() -> bool:
    return _env_bool("DANK_SILENCE_APPROVAL_ROLE_CHURN", True)


def _looks_like_bot_approval_role_churn(modlog: Any, role_added: List[str], role_removed: List[str], audit_reason: str) -> bool:
    if not role_added and not role_removed:
        return False

    reason = str(audit_reason or "").strip().lower()
    if not reason:
        return False

    if "approved by" not in reason:
        return False

    joined = "\n".join([*role_added, *role_removed]).lower()
    approval_role_terms = (
        "verified",
        "resident",
        "unverified",
        "stoner",
        "drunken",
    )
    return any(term in joined for term in approval_role_terms)


def patch_approval_role_churn_noise() -> bool:
    global _APPROVAL_PATCHED
    if _APPROVAL_PATCHED:
        return True

    try:
        from stoney_verify import modlog
    except Exception as e:
        _warn(f"failed importing modlog for approval patch: {e!r}")
        return False

    original = getattr(modlog, "maybe_log_member_update_diff", None)
    if not callable(original):
        _warn("modlog.maybe_log_member_update_diff missing; approval patch skipped")
        return False

    if getattr(original, "__dank_approval_role_churn_patch__", False):
        _APPROVAL_PATCHED = True
        return True

    async def filtered_maybe_log_member_update_diff(guild, before, after):  # type: ignore[no-untyped-def]
        if not _approval_role_churn_enabled():
            return await original(guild, before, after)

        try:
            role_added, role_removed = modlog._roles_diff_lines(before, after)
            timeout_lines = modlog._timeout_change_lines(before, after)
            nickname_changed = (before.nick or "") != (after.nick or "")

            if role_added or role_removed:
                if not timeout_lines and not nickname_changed:
                    entry = await modlog._audit_find_best_member_update_match(guild, int(after.id))
                    _actor, audit_reason = modlog._format_actor_from_audit(entry)
                    if _looks_like_bot_approval_role_churn(modlog, list(role_added), list(role_removed), audit_reason):
                        # The approval command/ticket flow is the meaningful event.
                        # Hide the mechanical role add/remove noise and report handled
                        # so events.py does not emit its fallback role-update embed.
                        return True
        except Exception:
            pass

        return await original(guild, before, after)

    filtered_maybe_log_member_update_diff.__dank_approval_role_churn_patch__ = True  # type: ignore[attr-defined]
    filtered_maybe_log_member_update_diff.__wrapped__ = original  # type: ignore[attr-defined]

    try:
        setattr(modlog, "_dank_original_maybe_log_member_update_diff", original)
        setattr(modlog, "maybe_log_member_update_diff", filtered_maybe_log_member_update_diff)
        _APPROVAL_PATCHED = True
        _log("silencing routine approval role add/remove churn")
        return True
    except Exception as e:
        _warn(f"failed installing approval role churn patch: {e!r}")
        return False


def register_member_update_modlog() -> bool:
    global _REGISTERED
    if _REGISTERED:
        return True

    if not _env_bool("DANK_ENABLE_EXTRA_MEMBER_UPDATE_MODLOG", False):
        _REGISTERED = True
        _log("extra public listener disabled; core events.py member-update logger is active")
        return True

    try:
        from stoney_verify.globals import bot
        from stoney_verify.commands_ext.public_member_update_modlog import register_public_member_update_modlog

        register_public_member_update_modlog(bot, getattr(bot, "tree", None))
        _REGISTERED = True
        _log("registered public role/nickname/timeout update listener")
        return True
    except Exception as e:
        _warn(f"failed registering public member update listener: {e!r}")
        return False


patch_member_update_context_noise()
patch_approval_role_churn_noise()
register_member_update_modlog()


__all__ = [
    "register_member_update_modlog",
    "patch_member_update_context_noise",
    "patch_approval_role_churn_noise",
]
