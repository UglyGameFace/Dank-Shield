from __future__ import annotations

"""Make permission repair previews explain which health warnings they fix."""

from typing import Any

import discord

_PATCHED = False


def _lines(lines: list[str], *, empty: str = "None", limit: int = 950) -> str:
    clean = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if not clean:
        return empty
    out: list[str] = []
    total = 0
    for line in clean:
        text = line if line.startswith(("•", "✅", "⚠️", "🚫")) else f"• {line}"
        if total + len(text) + 1 > limit:
            out.append(f"…and {len(clean) - len(out)} more")
            break
        out.append(text)
        total += len(text) + 1
    return "\n".join(out)[:limit] or empty


def _patch_repair_embed() -> bool:
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as repair
    except Exception:
        return False

    current = getattr(repair, "_result_embed", None)
    if not callable(current):
        return False
    if getattr(current, "_preview_clarity_wrapped", False):
        return True

    def _result_embed(result: dict[str, Any]) -> discord.Embed:
        applied = bool(result.get("applied"))
        ok = bool(result.get("ok"))
        notes = list(result.get("notes") or [])
        changed = list(result.get("changed") or [])
        unchanged = list(result.get("unchanged") or [])
        failed = list(result.get("failed") or [])

        embed = discord.Embed(
            title=("🛠️ Permission Repair Applied" if applied else "🛠️ Permission Repair Preview"),
            description=(
                "This compares the latest **Setup Safety Check** expectations to saved `/dank setup` IDs, "
                "then previews exactly which overwrites will change. It preserves per-user ticket overwrites."
            ),
            color=discord.Color.green() if ok else discord.Color.orange(),
        )
        if result.get("error"):
            embed.add_field(name="Issue", value=str(result.get("error"))[:1024], inline=False)
        embed.add_field(name="Targets Checked", value=f"`{int(result.get('target_count') or 0)}` setup channels/categories", inline=True)
        embed.add_field(
            name="Setup Health Warnings This Fixes",
            value=_lines(notes, empty="No specific Setup Health warning matched. These are baseline saved-setup permission checks."),
            inline=False,
        )
        embed.add_field(
            name=("Changed" if applied else "Will Change Now"),
            value=_lines(changed, empty="Nothing needs to change right now."),
            inline=False,
        )
        embed.add_field(name="Already Safe", value=_lines(unchanged, empty="None reported."), inline=False)
        if failed:
            embed.add_field(name="Manual Fix Needed", value=_lines(failed, empty="None"), inline=False)
        embed.add_field(
            name="After This",
            value="Run **Setup Safety Check** again. If a warning remains, it needs a different action than this scoped permission repair.",
            inline=False,
        )
        embed.set_footer(text="Safe scope: saved setup items, ticket/archive/staff-tool children, bot/staff/control/public baselines.")
        return embed

    setattr(_result_embed, "_preview_clarity_wrapped", True)
    repair._result_embed = _result_embed  # type: ignore[attr-defined]
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    _PATCHED = _patch_repair_embed()
    if _PATCHED:
        try:
            print("🛠️ setup_permission_repair_preview_clarity_guard active; repair previews explain warning coverage")
        except Exception:
            pass
    return _PATCHED


apply()

__all__ = ["apply"]
