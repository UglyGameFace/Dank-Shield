from __future__ import annotations

from typing import Any

import discord

from ..globals import now_utc
from . import public_setup_solid as solid

_PATCHED = False


def _short(value: Any, limit: int = 88) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _installed_slugs(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("slug") or "").strip().lower() for row in rows if str(row.get("slug") or "").strip()}


def _recommended_line(item: dict[str, Any], installed: set[str]) -> str:
    slug = str(item.get("slug") or "").strip().lower()
    marker = "✅" if slug in installed else "➕"
    default = " ⭐ default" if bool(item.get("is_default")) else ""
    purpose = item.get("why") or item.get("description") or "Recommended routing option."
    return f"{marker} **{item.get('name', slug)}** — `{slug}`{default}\n  ↳ {_short(purpose)}"


def _recommended_text(rows: list[dict[str, Any]]) -> str:
    installed = _installed_slugs(rows)
    return "\n".join(_recommended_line(item, installed) for item in solid.RECOMMENDED_CATEGORIES)[:1024]


def _missing_text(rows: list[dict[str, Any]]) -> str:
    installed = _installed_slugs(rows)
    missing = [item for item in solid.RECOMMENDED_CATEGORIES if str(item.get("slug") or "").lower() not in installed]
    if not missing:
        return "✅ All recommended categories already exist. You can still edit, rename, reorder, or delete them."
    return "\n".join(_recommended_line(item, installed) for item in missing)[:1024]


async def _better_category_manager_payload(guild: discord.Guild, *, title: str = "🗂️ Manage Ticket Categories"):
    load = await solid._category_load(guild)
    embed = discord.Embed(
        title=title,
        description=(
            "These are the options users pick from when opening a ticket.\n"
            "They control routing/menu labels — they are **not** Discord channel categories."
        ),
        color=discord.Color.blurple() if not load.error else discord.Color.red(),
        timestamp=now_utc(),
    )

    if load.error:
        embed.add_field(name="Database Problem", value=load.error[:1024], inline=False)
        embed.add_field(
            name="Fix",
            value="Make sure the `ticket_categories` table exists and Supabase is reachable, then press Refresh.",
            inline=False,
        )
        return embed, solid.CategoryManagerView(rows=load.rows, db_error=load.error)

    embed.add_field(name="Current Categories", value=solid._category_list_text(load.rows), inline=False)
    embed.add_field(name="Stoney's Recommended Layout", value=_recommended_text(load.rows), inline=False)
    embed.add_field(name="Missing Recommended Categories", value=_missing_text(load.rows), inline=False)
    embed.add_field(
        name="What Create Recommended Does",
        value=(
            "Creates only missing recommended menu/routing categories. It does **not** create Discord channels, "
            "does **not** delete old tickets, and does **not** lock you in. You can edit everything after."
        ),
        inline=False,
    )
    embed.add_field(name="Safety", value=solid._category_governance_text(load.rows), inline=False)
    embed.set_footer(text="Tip: use Ticket Basics for the actual Discord open/archive categories.")
    return embed, solid.CategoryManagerView(rows=load.rows, db_error=load.error)


def _patch() -> None:
    global _PATCHED
    solid._build_category_manager_payload = _better_category_manager_payload
    _PATCHED = True


_patch()


def register_public_setup_recommend_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_recommend: setup category recommendations preview active")


__all__ = ["register_public_setup_recommend_commands"]
