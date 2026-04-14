from __future__ import annotations

from typing import Any, Dict, List, Optional

import asyncio
import re

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from .common import _staff_check, reply_once


_ALLOWED_INTAKE_TYPES = {
    "general",
    "support",
    "verification",
    "appeal",
    "report",
    "partnership",
    "question",
    "ghost",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _truncate(text: Any, limit: int = 250) -> str:
    raw = _safe_str(text)
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _slugify(value: str) -> str:
    try:
        text = str(value or "").strip().lower()
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9\s\-_]+", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-{2,}", "-", text)
        return text.strip("-")[:80]
    except Exception:
        return ""


def _normalize_keywords(value: str) -> List[str]:
    out: List[str] = []
    try:
        for raw in str(value or "").split(","):
            item = str(raw or "").strip().lower()
            if item and item not in out:
                out.append(item[:80])
    except Exception:
        pass
    return out[:25]


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id")),
        "slug": _safe_str(row.get("slug")).lower(),
        "name": _safe_str(row.get("name")),
        "description": _safe_str(row.get("description")),
        "intake_type": _safe_str(row.get("intake_type"), "general").lower(),
        "match_keywords": list(row.get("match_keywords") or []) if isinstance(row.get("match_keywords"), list) else [],
        "is_default": bool(row.get("is_default", False)),
        "sort_order": row.get("sort_order"),
    }


def _fetch_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return []

    try:
        res = (
            sb.table("ticket_categories")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        out = [_normalize_category_row(x) for x in rows if isinstance(x, dict)]
        out.sort(key=lambda r: (r.get("sort_order") is None, r.get("sort_order") if r.get("sort_order") is not None else 10_000, r.get("name", "").lower()))
        return out
    except Exception:
        return []


async def _fetch_categories(guild_id: int) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_categories_sync, guild_id)


def _fetch_category_by_slug_sync(guild_id: int, slug: str) -> Optional[Dict[str, Any]]:
    sb = get_supabase()
    if not sb:
        return None

    try:
        res = (
            sb.table("ticket_categories")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("slug", str(slug).lower())
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return _normalize_category_row(rows[0])
    except Exception:
        pass
    return None


async def _fetch_category_by_slug(guild_id: int, slug: str) -> Optional[Dict[str, Any]]:
    return await _run_blocking(_fetch_category_by_slug_sync, guild_id, slug)


def _insert_category_sync(payload: Dict[str, Any]) -> bool:
    sb = get_supabase()
    if not sb:
        return False
    try:
        sb.table("ticket_categories").insert(payload).execute()
        return True
    except Exception:
        return False


async def _insert_category(payload: Dict[str, Any]) -> bool:
    return await _run_blocking(_insert_category_sync, payload)


def _update_category_sync(guild_id: int, slug: str, patch: Dict[str, Any]) -> bool:
    sb = get_supabase()
    if not sb:
        return False
    try:
        sb.table("ticket_categories").update(patch).eq("guild_id", str(int(guild_id))).eq("slug", str(slug).lower()).execute()
        return True
    except Exception:
        return False


async def _update_category(guild_id: int, slug: str, patch: Dict[str, Any]) -> bool:
    return await _run_blocking(_update_category_sync, guild_id, slug, patch)


def _delete_category_sync(guild_id: int, slug: str) -> bool:
    sb = get_supabase()
    if not sb:
        return False
    try:
        sb.table("ticket_categories").delete().eq("guild_id", str(int(guild_id))).eq("slug", str(slug).lower()).execute()
        return True
    except Exception:
        return False


async def _delete_category(guild_id: int, slug: str) -> bool:
    return await _run_blocking(_delete_category_sync, guild_id, slug)


def _clear_default_sync(guild_id: int) -> bool:
    sb = get_supabase()
    if not sb:
        return False
    try:
        sb.table("ticket_categories").update({"is_default": False}).eq("guild_id", str(int(guild_id))).eq("is_default", True).execute()
        return True
    except Exception:
        return False


async def _clear_default(guild_id: int) -> bool:
    return await _run_blocking(_clear_default_sync, guild_id)


def _category_embed(title: str, row: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Name", value=_safe_str(row.get("name"), "unknown"), inline=True)
    embed.add_field(name="Slug", value=f"`{_safe_str(row.get('slug'), 'unknown')}`", inline=True)
    embed.add_field(name="Type", value=f"`{_safe_str(row.get('intake_type'), 'general')}`", inline=True)
    embed.add_field(name="Default", value="Yes" if bool(row.get("is_default")) else "No", inline=True)
    embed.add_field(name="Sort Order", value=f"`{row.get('sort_order')}`", inline=True)
    embed.add_field(name="Keywords", value=(", ".join([f"`{_truncate(x, 40)}`" for x in (row.get("match_keywords") or [])]) or "—")[:1024], inline=False)
    embed.add_field(name="Description", value=_truncate(row.get("description") or "No description.", 1024), inline=False)
    return embed


def register_ticket_category_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_category_create",
        description="Create a new dashboard ticket category.",
    )
    @app_commands.describe(
        name="Display name for the category",
        slug="URL-style slug, like support or verification-issue",
        intake_type="Type: general, support, verification, appeal, report, partnership, question, ghost",
        description="Optional category description",
        keywords="Comma-separated match keywords",
        is_default="Whether this should be the default category",
        sort_order="Optional sort order",
    )
    async def ticket_category_create(
        interaction: discord.Interaction,
        name: str,
        slug: str,
        intake_type: str,
        description: Optional[str] = None,
        keywords: Optional[str] = None,
        is_default: Optional[bool] = False,
        sort_order: Optional[int] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        slug_clean = _slugify(slug)
        if not slug_clean:
            return await reply_once(interaction, {"content": "❌ Invalid slug.", "ephemeral": True})

        type_clean = _safe_str(intake_type, "general").lower()
        if type_clean not in _ALLOWED_INTAKE_TYPES:
            return await reply_once(interaction, {"content": f"❌ Invalid intake type. Use one of: {', '.join(sorted(_ALLOWED_INTAKE_TYPES))}", "ephemeral": True})

        existing = await _fetch_category_by_slug(guild.id, slug_clean)
        if existing:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` already exists.", "ephemeral": True})

        if bool(is_default):
            await _clear_default(guild.id)

        payload = {
            "guild_id": str(guild.id),
            "slug": slug_clean,
            "name": _safe_str(name)[:120],
            "description": _safe_str(description)[:500],
            "intake_type": type_clean,
            "match_keywords": _normalize_keywords(_safe_str(keywords)),
            "is_default": bool(is_default),
            "sort_order": int(sort_order) if sort_order is not None else None,
        }

        ok = await _insert_category(payload)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to create ticket category.", "ephemeral": True})

        embed = _category_embed("✅ Ticket Category Created", payload)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_category_edit",
        description="Edit an existing dashboard ticket category.",
    )
    @app_commands.describe(
        slug="Existing category slug to edit",
        name="New display name",
        intake_type="New intake type",
        description="New description",
        keywords="New comma-separated keywords",
        is_default="Set or unset as default",
        sort_order="New sort order",
    )
    async def ticket_category_edit(
        interaction: discord.Interaction,
        slug: str,
        name: Optional[str] = None,
        intake_type: Optional[str] = None,
        description: Optional[str] = None,
        keywords: Optional[str] = None,
        is_default: Optional[bool] = None,
        sort_order: Optional[int] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        slug_clean = _slugify(slug)
        row = await _fetch_category_by_slug(guild.id, slug_clean)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})

        patch: Dict[str, Any] = {}
        if name is not None:
            patch["name"] = _safe_str(name)[:120]
        if intake_type is not None:
            type_clean = _safe_str(intake_type, "general").lower()
            if type_clean not in _ALLOWED_INTAKE_TYPES:
                return await reply_once(interaction, {"content": f"❌ Invalid intake type. Use one of: {', '.join(sorted(_ALLOWED_INTAKE_TYPES))}", "ephemeral": True})
            patch["intake_type"] = type_clean
        if description is not None:
            patch["description"] = _safe_str(description)[:500]
        if keywords is not None:
            patch["match_keywords"] = _normalize_keywords(_safe_str(keywords))
        if sort_order is not None:
            patch["sort_order"] = int(sort_order)
        if is_default is not None:
            if bool(is_default):
                await _clear_default(guild.id)
            patch["is_default"] = bool(is_default)

        if not patch:
            return await reply_once(interaction, {"content": "ℹ️ Nothing to update.", "ephemeral": True})

        ok = await _update_category(guild.id, slug_clean, patch)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to update ticket category.", "ephemeral": True})

        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, **patch}
        embed = _category_embed("✅ Ticket Category Updated", updated)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_category_delete",
        description="Delete a dashboard ticket category.",
    )
    @app_commands.describe(slug="Category slug to delete")
    async def ticket_category_delete(interaction: discord.Interaction, slug: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        slug_clean = _slugify(slug)
        row = await _fetch_category_by_slug(guild.id, slug_clean)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})

        ok = await _delete_category(guild.id, slug_clean)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to delete ticket category.", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Deleted ticket category `{slug_clean}`.", "ephemeral": True})

    @tree.command(
        name="ticket_category_set_default",
        description="Set the default dashboard ticket category.",
    )
    @app_commands.describe(slug="Category slug to make default")
    async def ticket_category_set_default(interaction: discord.Interaction, slug: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        slug_clean = _slugify(slug)
        row = await _fetch_category_by_slug(guild.id, slug_clean)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})

        await _clear_default(guild.id)
        ok = await _update_category(guild.id, slug_clean, {"is_default": True})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to set default category.", "ephemeral": True})

        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "is_default": True}
        embed = _category_embed("⭐ Default Ticket Category Updated", updated)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_category_reorder",
        description="Set the sort order for a dashboard ticket category.",
    )
    @app_commands.describe(
        slug="Category slug to reorder",
        sort_order="Lower numbers appear first",
    )
    async def ticket_category_reorder(
        interaction: discord.Interaction,
        slug: str,
        sort_order: int,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        slug_clean = _slugify(slug)
        row = await _fetch_category_by_slug(guild.id, slug_clean)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})

        ok = await _update_category(guild.id, slug_clean, {"sort_order": int(sort_order)})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to reorder ticket category.", "ephemeral": True})

        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "sort_order": int(sort_order)}
        embed = _category_embed("↕️ Ticket Category Reordered", updated)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_category_keywords",
        description="Replace the keyword list for a dashboard ticket category.",
    )
    @app_commands.describe(
        slug="Category slug to update",
        keywords="Comma-separated keywords to use for routing",
    )
    async def ticket_category_keywords(
        interaction: discord.Interaction,
        slug: str,
        keywords: str,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        slug_clean = _slugify(slug)
        row = await _fetch_category_by_slug(guild.id, slug_clean)
        if not row:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})

        kw = _normalize_keywords(keywords)
        ok = await _update_category(guild.id, slug_clean, {"match_keywords": kw})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to update category keywords.", "ephemeral": True})

        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "match_keywords": kw}
        embed = _category_embed("🏷️ Ticket Category Keywords Updated", updated)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
