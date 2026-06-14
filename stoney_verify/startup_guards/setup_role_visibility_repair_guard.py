from __future__ import annotations

"""Extend setup permission repair with role visibility alignment targets."""

from typing import Any

import discord

_DONE = False


def _role_from_config(guild: discord.Guild, cfg: Any, name: str) -> discord.Role | None:
    try:
        raw = cfg.get(name) if hasattr(cfg, "get") else getattr(cfg, name, 0)
        role = guild.get_role(int(str(raw or "0").strip() or "0"))
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


def _target_exists(targets: list[Any], channel: Any) -> bool:
    try:
        cid = int(getattr(channel, "id", 0) or 0)
        return any(int(getattr(getattr(item, "channel", None), "id", 0) or 0) == cid for item in targets)
    except Exception:
        return False


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
                items = visibility._unverified_leaks(guild, cfg, waiting_role)
                if not items:
                    return targets, notes
                ow = {waiting_role: discord.PermissionOverwrite(view_channel=False)}
                added = 0
                seen = {int(getattr(getattr(item, "channel", None), "id", 0) or 0) for item in targets}
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
