from __future__ import annotations

"""Migration adapter for legacy setup-health callers."""

import discord

_DONE = False


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_group as group
        from stoney_verify.setup_engine import build_legacy_health_lists

        def _build_setup_health(guild: discord.Guild, cfg):
            return build_legacy_health_lists(guild, cfg)

        setattr(_build_setup_health, "_canonical_setup_engine", True)
        group._build_setup_health = _build_setup_health  # type: ignore[attr-defined]
        _DONE = True
        print("🧭 setup_engine adapter active; legacy setup health now uses canonical setup_engine v1")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_engine adapter failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
