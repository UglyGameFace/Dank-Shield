from __future__ import annotations

"""Suppress mod-log spam from setup permission repair.

Permission repair legitimately touches many channel overwrites. Discord emits a
channel update for each touched channel, which used to flood the configured
mod-log with dozens of near-identical cards after one Apply Fixes click.

This guard patches the public modlog channel-update listener before listener
registration. It suppresses only channel updates whose audit-log reason is the
known setup permission repair reason. Normal channel edits still log.
"""

from typing import Any

_PATCHED = False
_ORIGINAL_CHANNEL_UPDATE: Any = None
_REPAIR_REASON = "dank shield setup permission repair"


def _log(message: str) -> None:
    try:
        print(f"🛠️ setup_permission_repair_modlog_silence_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_permission_repair_modlog_silence_guard {message}")
    except Exception:
        pass


def _is_repair_reason(reason: Any) -> bool:
    try:
        return _REPAIR_REASON in str(reason or "").strip().lower()
    except Exception:
        return False


def apply() -> bool:
    global _PATCHED, _ORIGINAL_CHANNEL_UPDATE
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_modlog_coverage as modlog

        original = getattr(modlog, "_on_guild_channel_update", None)
        if not callable(original) or getattr(original, "_permission_repair_silenced", False):
            return False

        async def wrapped(before: Any, after: Any) -> None:
            try:
                guild = getattr(after, "guild", None)
                if guild is not None:
                    _actor, reason = await modlog._find_audit_actor(
                        guild,
                        "channel_update",
                        target_id=getattr(after, "id", None),
                    )
                    if _is_repair_reason(reason):
                        return
            except Exception:
                pass
            return await original(before, after)

        setattr(wrapped, "_permission_repair_silenced", True)
        setattr(wrapped, "_permission_repair_original", original)
        _ORIGINAL_CHANNEL_UPDATE = original
        modlog._on_guild_channel_update = wrapped
        _PATCHED = True
        _log("active; setup permission repair channel-update modlog spam is suppressed")
        return True
    except Exception as exc:
        _warn(f"failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
