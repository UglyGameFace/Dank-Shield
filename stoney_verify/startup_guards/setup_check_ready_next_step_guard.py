from __future__ import annotations

"""Make the green /dank setup safety check stop showing fix instructions.

The existing setup check correctly detects an all-clear state, but it still adds
an unconditional "How to fix this" field. That reads like a contradiction. This
small display-layer guard keeps the same health logic and only changes the final
field when blockers and warnings are both clear.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_BUILD: Any = None


def _field_value(embed: discord.Embed, name: str) -> str:
    wanted = str(name or "").strip().lower()
    for field in list(getattr(embed, "fields", []) or []):
        try:
            if str(field.name or "").strip().lower() == wanted:
                return str(field.value or "")
        except Exception:
            continue
    return ""


def _is_all_clear(embed: discord.Embed) -> bool:
    try:
        desc = str(embed.description or "")
        needs = _field_value(embed, "Needs Fixing")
        warnings = _field_value(embed, "Warnings / Review")
        return (
            "Ready to test" in desc
            and "Nothing required is missing" in needs
            and "No warnings" in warnings
        )
    except Exception:
        return False


def _replace_clear_next_step(embed: discord.Embed) -> discord.Embed:
    try:
        fields = list(embed.fields)
        embed.clear_fields()
        for field in fields:
            name = str(field.name or "")
            if name.strip().lower() in {"how to fix this", "how to fix"}:
                continue
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
        embed.add_field(
            name="Next step",
            value=(
                "✅ No fixes needed right now. Post or refresh the member-facing panel for this setup, "
                "then test it with a non-staff account. If something changes later, rerun Setup Check."
            ),
            inline=False,
        )
    except Exception:
        pass
    return embed


async def _patched_build_plain_setup_health_embed(recommend: Any, guild: discord.Guild) -> discord.Embed:
    embed = await _ORIGINAL_BUILD(recommend, guild)
    if _is_all_clear(embed):
        return _replace_clear_next_step(embed)
    return embed


def apply() -> bool:
    global _PATCHED, _ORIGINAL_BUILD
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_check_existing_server_inference_guard as existing
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        _ORIGINAL_BUILD = existing._build_plain_setup_health_embed
        existing._build_plain_setup_health_embed = _patched_build_plain_setup_health_embed
        recommend._build_plain_setup_health_embed = lambda guild: _patched_build_plain_setup_health_embed(recommend, guild)
        _PATCHED = True
        print("✅ setup_check_ready_next_step_guard active; green setup checks show next step instead of fix instructions")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_check_ready_next_step_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
