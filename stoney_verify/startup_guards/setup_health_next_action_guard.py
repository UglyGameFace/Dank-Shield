from __future__ import annotations

"""Make setup health actionable instead of just informational."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_BUILD_HEALTH_EMBED: Any = None


def _field_value(embed: discord.Embed, name: str) -> str:
    try:
        for field in list(getattr(embed, "fields", []) or []):
            if str(getattr(field, "name", "") or "") == name:
                return str(getattr(field, "value", "") or "")
    except Exception:
        pass
    return ""


def _has_real_items(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and "✅ None" not in text and text.lower() not in {"none", "no passing checks reported."})


def _replace_next_action(embed: discord.Embed, *, blockers: str, warnings: str) -> discord.Embed:
    try:
        remaining: list[tuple[str, str, bool]] = []
        for field in list(getattr(embed, "fields", []) or []):
            name = str(getattr(field, "name", "") or "")
            if name in {"What To Press Next", "Next Action", "Recommended Next Click"}:
                continue
            remaining.append((name, str(getattr(field, "value", "") or ""), bool(getattr(field, "inline", False))))
        embed.clear_fields()
        for name, value, inline in remaining:
            embed.add_field(name=name[:256], value=(value or "—")[:1024], inline=inline)

        has_blockers = _has_real_items(blockers)
        has_warnings = _has_real_items(warnings)
        if has_blockers:
            next_text = (
                "1. Press **🧩 Use Existing Roles/Channels** if the missing item already exists.\n"
                "2. Press **🚀 Start Setup / Fix Missing** if you want Dank Shield to create missing defaults.\n"
                "3. Run **🩺 Health Check** again after saving."
            )
            embed.color = discord.Color.red()
            embed.description = "🚫 **Not ready yet.** Fix the blockers below, then run Health Check again."
        elif has_warnings:
            next_text = (
                "1. You can test now, but review the warnings first.\n"
                "2. Press **🧩 Use Existing Roles/Channels** to map anything optional.\n"
                "3. Test opening a ticket and test verification."
            )
            embed.color = discord.Color.orange()
            embed.description = "🟡 **Almost ready.** No blockers, but a few optional items need review."
        else:
            next_text = (
                "1. Test opening a ticket.\n"
                "2. Test verification with a spare/member account.\n"
                "3. Use **Advanced Ticket Routing** only if you want custom ticket menu options."
            )
            embed.color = discord.Color.green()
            embed.description = "✅ **Ready to test.** Main setup checks are passing."
        embed.add_field(name="Recommended Next Click", value=next_text[:1024], inline=False)
        embed.set_footer(text="Setup is safe to rerun. It saves choices and repairs missing items without deleting server history.")
    except Exception:
        pass
    return embed


def apply() -> bool:
    global _PATCHED, _ORIGINAL_BUILD_HEALTH_EMBED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        original = getattr(solid, "_build_health_embed", None)
        if not callable(original) or getattr(original, "_health_next_action_wrapped", False):
            return False
        _ORIGINAL_BUILD_HEALTH_EMBED = original

        async def wrapped_build_health_embed(*args: Any, **kwargs: Any) -> discord.Embed:
            embed = await _ORIGINAL_BUILD_HEALTH_EMBED(*args, **kwargs)
            blockers = _field_value(embed, "Blockers")
            warnings = _field_value(embed, "Warnings")
            return _replace_next_action(embed, blockers=blockers, warnings=warnings)

        setattr(wrapped_build_health_embed, "_health_next_action_wrapped", True)
        solid._build_health_embed = wrapped_build_health_embed
        _PATCHED = True
        print("🧭 setup_health_next_action_guard active; health checks now recommend exact next clicks")
        return True
    except Exception as exc:
        print(f"⚠️ setup_health_next_action_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
