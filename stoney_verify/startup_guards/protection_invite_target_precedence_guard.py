from __future__ import annotations

"""Keep invite protection choices authoritative.

When staff choose watched bots or watched channels, those choices should not be
silently cancelled by broad exception buckets. This keeps bump-bot setups simple:
let the other bot speak, while Dank Shield still handles Discord invite links.
"""

from typing import Any

_PATCHED = False
_ORIGINAL_OVERRIDE_ENABLED: Any = None

_PRIORITY_KEYS = {
    "invite_override_exempt_users_roles",
    "invite_override_allowed_roles",
    "invite_override_allowed_channels",
    "invite_override_allowed_codes",
    "invite_override_own_server_invites",
}


def _has_watched_scope(invite_guard: Any, settings: dict[str, Any]) -> bool:
    try:
        all_bots = invite_guard._safe_bool(
            invite_guard._first_setting(settings, "invite_hard_block_target_all_bots", "invite_target_all_bots"),
            False,
        )
        bot_ids = invite_guard._normalize_id_list(
            invite_guard._first_setting(settings, "invite_hard_block_target_bot_ids", "invite_target_bot_ids")
        )
        channel_ids = invite_guard._normalize_id_list(
            invite_guard._first_setting(settings, "invite_hard_block_target_channel_ids", "invite_target_channel_ids")
        )
        return bool(all_bots or bot_ids or channel_ids)
    except Exception:
        return False


def apply() -> bool:
    global _PATCHED, _ORIGINAL_OVERRIDE_ENABLED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import spam_guard_invite_hard_block as invite_guard
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy_guard

        try:
            invite_guard.install()
        except Exception:
            pass
        try:
            policy_guard.apply()
        except Exception:
            pass

        _ORIGINAL_OVERRIDE_ENABLED = getattr(invite_guard, "_override_enabled")

        def patched_override_enabled(settings: dict[str, Any], key: str) -> bool:
            if bool(_ORIGINAL_OVERRIDE_ENABLED(settings, key)):
                return True
            if key in _PRIORITY_KEYS and _has_watched_scope(invite_guard, settings):
                return True
            return False

        invite_guard._override_enabled = patched_override_enabled
        _PATCHED = True
        print("✅ protection_invite_target_precedence_guard active; watched invite bots/channels are authoritative")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_invite_target_precedence_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]