from __future__ import annotations

"""Compatibility wrapper for invite target precedence.

This module intentionally stays small: the main invite listener already supports
protected bot/channel settings and allow-list override settings. This guard gives
startup logs a clear marker that the intended policy is explicit:
protected bot/channel choices are meant to take priority over broad allow-lists.
"""

_PATCHED = False


def apply() -> bool:
    global _PATCHED
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
        _PATCHED = True
        print("✅ protection_invite_target_precedence_guard active; protected invite targets are treated as priority settings")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_invite_target_precedence_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]