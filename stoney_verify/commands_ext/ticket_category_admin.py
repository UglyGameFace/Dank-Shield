from __future__ import annotations

from typing import Any, Dict, List, Optional

import asyncio
import re

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from .common import _staff_check, reply_once


# ============================================================
# ticket_category_admin.py
# ------------------------------------------------------------
# Hardening goals:
# - keep category CRUD governance-safe
# - preserve one valid default at all times
# - allow the intake types the project actually uses
# - prevent destructive edits that leave routing in a broken state
# - make warnings obvious to staff before category drift becomes a bug
# ============================================================

_ALLOWED_INTAKE_TYPES = {
    "general",
    "support",
    "verification",
    "appeal",
    "report",
    "partnership",
    "question",
    "ghost",
    "account",
    "purchase",
    "bug",
    "custom",
}

_MAX_NAME_LEN = 120
_MAX_DESC_LEN = 500
_MAX_SLUG_LEN = 80
_MAX_KEYWORD_LEN = 80
_MAX_KEYWORDS = 25
_MIN_SORT_ORDER = -9999
_MAX_SORT_ORDER = 9999


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
        return text.strip("-")[:_MAX_SLUG_LEN]
    except Exception:
        return ""


def _normalize_name(value: Any) -> str:
    try:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:_MAX_NAME_LEN]
    except Exception:
        return ""


def _normalize_description(value: Any) -> str:
    try:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:_MAX_DESC_LEN]
    except Exception:
        return ""


def _normalize_keywords(value: Any) -> List[str]:
    out: List[str] = []

    try:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = str(value or "").split(",")

        for raw in raw_items:
            item = str(raw or "").strip().lower()
            item = re.sub(r"\s+", " ", item)
            if not item:
                continue
            item = item[:_MAX_KEYWORD_LEN]
            if item not in out:
                out.append(item)
    except Exception:
        pass

    return out[:_MAX_KEYWORDS]


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    sort_order = row.get("sort_order")
    try:
        sort_order = int(sort_order) if sort_order is not None else None
    except Exception:
        sort_order = None

    return {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id")),
        "slug": _safe_str(row.get("slug")).lower(),
        "name": _safe_str(row.get("name")),
        "description": _safe_str(row.get("description")),
        "intake_type": _safe_str(row.get("intake_type"), "general").lower(),
        "match_keywords": _normalize_keywords(row.get("match_keywords")),
        "is_default": bool(row.get("is_default", False)),
        "sort_order": sort_order,
    }


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _fetch_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    sb = _sb()
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
        out.sort(
            key=lambda r: (
                r.get("sort_order") is None,
                r.get("sort_order") if r.get("sort_order") is not None else 10_000,
                r.get("name", "").lower(),
                r.get("slug", "").lower(),
            )
        )
        return out
    except Exception:
        return []


async def _fetch_categories(guild_id: int) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_categories_sync, guild_id)


def _fetch_category_by_slug_sync(guild_id: int, slug: str) -> Optional[Dict[str, Any]]:
    sb = _sb()
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
    sb = _sb()
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
    sb = _sb()
    if not sb:
        return False
    try:
        (
            sb.table("ticket_categories")
            .update(patch)
            .eq("guild_id", str(int(guild_id)))
            .eq("slug", str(slug).lower())
            .execute()
        )
        return True
    except Exception:
        return False


async def _update_category(guild_id: int, slug: str, patch: Dict[str, Any]) -> bool:
    return await _run_blocking(_update_category_sync, guild_id, slug, patch)


def _delete_category_sync(guild_id: int, slug: str) -> bool:
    sb = _sb()
    if not sb:
        return False
    try:
        (
            sb.table("ticket_categories")
            .delete()
            .eq("guild_id", str(int(guild_id)))
            .eq("slug", str(slug).lower())
            .execute()
        )
        return True
    except Exception:
        return False


async def _delete_category(guild_id: int, slug: str) -> bool:
    return await _run_blocking(_delete_category_sync, guild_id, slug)


def _clear_default_except_sync(guild_id: int, keep_slug: Optional[str] = None) -> bool:
    sb = _sb()
    if not sb:
        return False

    try:
        query = (
            sb.table("ticket_categories")
            .update({"is_default": False})
            .eq("guild_id", str(int(guild_id)))
            .eq("is_default", True)
        )
        if keep_slug:
            query = query.neq("slug", str(keep_slug).lower())
        query.execute()
        return True
    except Exception:
        return False


async def _clear_default_except(guild_id: int, keep_slug: Optional[str] = None) -> bool:
    return await _run_blocking(_clear_default_except_sync, guild_id, keep_slug)


def _set_default_sync(guild_id: int, slug: str) -> bool:
    sb = _sb()
    if not sb:
        return False

    try:
        (
            sb.table("ticket_categories")
            .update({"is_default": True})
            .eq("guild_id", str(int(guild_id)))
            .eq("slug", str(slug).lower())
            .execute()
        )
        _clear_default_except_sync(guild_id, keep_slug=slug)
        return True
    except Exception:
        return False


async def _set_default(guild_id: int, slug: str) -> bool:
    return await _run_blocking(_set_default_sync, guild_id, slug)


def _duplicate_slugs(rows: List[Dict[str, Any]]) -> List[str]:
    counts: Dict[str, int] = {}
    for row in rows:
        slug = _safe_str(row.get("slug")).lower()
        if not slug:
            continue
        counts[slug] = counts.get(slug, 0) + 1
    return sorted([slug for slug, count in counts.items() if count > 1])


def _default_categories(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in rows if bool(row.get("is_default"))]


def _verification_like_categories(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        slug = _safe_str(row.get("slug")).lower()
        name = _safe_str(row.get("name")).lower()
        intake_type = _safe_str(row.get("intake_type")).lower()
        if intake_type == "verification":
            out.append(row)
            continue
        if slug in {"verification", "verification-issue", "verification_issue"}:
            out.append(row)
            continue
        if "verification" in name:
            out.append(row)
            continue
    return out


def _choose_replacement_default(rows: List[Dict[str, Any]], deleted_slug: str) -> Optional[Dict[str, Any]]:
    candidates = [row for row in rows if _safe_str(row.get("slug")).lower() != deleted_slug.lower()]
    if not candidates:
        return None

    candidates.sort(
        key=lambda r: (
            r.get("sort_order") is None,
            r.get("sort_order") if r.get("sort_order") is not None else 10_000,
            r.get("name", "").lower(),
            r.get("slug", "").lower(),
        )
    )
    return candidates[0]


def _category_embed(title: str, row: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Name", value=_safe_str(row.get("name"), "unknown"), inline=True)
    embed.add_field(name="Slug", value=f"`{_safe_str(row.get('slug'), 'unknown')}`", inline=True)
    embed.add_field(name="Type", value=f"`{_safe_str(row.get('intake_type'), 'general')}`", inline=True)
    embed.add_field(name="Default", value="Yes" if bool(row.get("is_default")) else "No", inline=True)
    embed.add_field(name="Sort Order", value=f"`{row.get('sort_order')}`", inline=True)
    embed.add_field(
        name="Keywords",
        value=((", ".join([f"`{_truncate(x, 40)}`" for x in (row.get("match_keywords") or [])])) or "—")[:1024],
        inline=False,
    )
    embed.add_field(
        name="Description",
        value=_truncate(row.get("description") or "No description.", 1024),
        inline=False,
    )
    embed.add_field(
        name="Staff Note",
        value=(
            "Keyword routing should be specific enough that staff can predict why this category wins.\n"
            "Keep names, descriptions, and keywords clear."
        )[:1024],
        inline=False,
    )
    return embed


def _governance_warnings(rows: List[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []

    duplicates = _duplicate_slugs(rows)
    if duplicates:
        warnings.append(f"Duplicate slugs detected: {', '.join([f'`{x}`' for x in duplicates[:6]])}")

    defaults = _default_categories(rows)
    if len(defaults) == 0:
        warnings.append("No default category is set.")
    elif len(defaults) > 1:
        warnings.append(f"Multiple default categories detected: `{len(defaults)}`")

    verification_rows = _verification_like_categories(rows)
    if len(verification_rows) == 0:
        warnings.append("No verification-like category detected.")
    elif len(verification_rows) > 1:
        warnings.append(f"Multiple verification-like categories detected: `{len(verification_rows)}`")

    return warnings


def _validated_sort_order(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        num = int(value)
    except Exception:
        return None
    if num < _MIN_SORT_ORDER or num > _MAX_SORT_ORDER:
        return None
    return num


def _human_intake_types() -> str:
    return ", ".join(sorted(_ALLOWED_INTAKE_TYPES))


def register_ticket_category_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_category_create",
        description="Create a new dashboard ticket category.",
    )
    @app_commands.describe(
        name="Display name for the category",
        slug="URL-style slug, like support or verification-issue",
        intake_type="Type used by routing logic",
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

        name_clean = _normalize_name(name)
        if not name_clean:
            return await reply_once(interaction, {"content": "❌ Category name cannot be empty.", "ephemeral": True})

        type_clean = _safe_str(intake_type, "general").lower()
        if type_clean not in _ALLOWED_INTAKE_TYPES:
            return await reply_once(
                interaction,
                {"content": f"❌ Invalid intake type. Use one of: {_human_intake_types()}", "ephemeral": True},
            )

        sort_clean = _validated_sort_order(sort_order)
        if sort_order is not None and sort_clean is None:
            return await reply_once(
                interaction,
                {"content": f"❌ Sort order must be between `{_MIN_SORT_ORDER}` and `{_MAX_SORT_ORDER}`.", "ephemeral": True},
            )

        existing = await _fetch_category_by_slug(guild.id, slug_clean)
        if existing:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` already exists.", "ephemeral": True})

        payload = {
            "guild_id": str(guild.id),
            "slug": slug_clean,
            "name": name_clean,
            "description": _normalize_description(description),
            "intake_type": type_clean,
            "match_keywords": _normalize_keywords(_safe_str(keywords)),
            "is_default": bool(is_default),
            "sort_order": sort_clean,
        }

        ok = await _insert_category(payload)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to create ticket category.", "ephemeral": True})

        if bool(is_default):
            await _set_default(guild.id, slug_clean)

        created = await _fetch_category_by_slug(guild.id, slug_clean) or payload
        embed = _category_embed("✅ Ticket Category Created", created)

        rows = await _fetch_categories(guild.id)
        warnings = _governance_warnings(rows)
        if warnings:
            embed.add_field(
                name="Governance Warnings",
                value="\n".join([f"• {w}" for w in warnings])[:1024],
                inline=False,
            )

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
            name_clean = _normalize_name(name)
            if not name_clean:
                return await reply_once(interaction, {"content": "❌ Category name cannot be empty.", "ephemeral": True})
            patch["name"] = name_clean

        if intake_type is not None:
            type_clean = _safe_str(intake_type, "general").lower()
            if type_clean not in _ALLOWED_INTAKE_TYPES:
                return await reply_once(
                    interaction,
                    {"content": f"❌ Invalid intake type. Use one of: {_human_intake_types()}", "ephemeral": True},
                )
            patch["intake_type"] = type_clean

        if description is not None:
            patch["description"] = _normalize_description(description)

        if keywords is not None:
            patch["match_keywords"] = _normalize_keywords(_safe_str(keywords))

        if sort_order is not None:
            sort_clean = _validated_sort_order(sort_order)
            if sort_clean is None:
                return await reply_once(
                    interaction,
                    {"content": f"❌ Sort order must be between `{_MIN_SORT_ORDER}` and `{_MAX_SORT_ORDER}`.", "ephemeral": True},
                )
            patch["sort_order"] = sort_clean

        if is_default is not None:
            patch["is_default"] = bool(is_default)

        if not patch:
            return await reply_once(interaction, {"content": "ℹ️ Nothing to update.", "ephemeral": True})

        ok = await _update_category(guild.id, slug_clean, patch)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to update ticket category.", "ephemeral": True})

        if is_default is True:
            await _set_default(guild.id, slug_clean)
        elif is_default is False and bool(row.get("is_default")):
            rows_after = await _fetch_categories(guild.id)
            replacement = _choose_replacement_default(rows_after, slug_clean)
            if replacement is not None:
                await _set_default(guild.id, _safe_str(replacement.get("slug")))
            else:
                await _set_default(guild.id, slug_clean)

        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, **patch}
        embed = _category_embed("✅ Ticket Category Updated", updated)

        rows = await _fetch_categories(guild.id)
        warnings = _governance_warnings(rows)
        if warnings:
            embed.add_field(
                name="Governance Warnings",
                value="\n".join([f"• {w}" for w in warnings])[:1024],
                inline=False,
            )

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

        rows_before = await _fetch_categories(guild.id)
        if len(rows_before) <= 1:
            return await reply_once(
                interaction,
                {
                    "content": (
                        "❌ You cannot delete the only remaining ticket category.\n"
                        "Create another category first so routing always has at least one valid target."
                    ),
                    "ephemeral": True,
                },
            )

        verification_rows = _verification_like_categories(rows_before)
        deleting_verification_like = any(
            _safe_str(x.get("slug")).lower() == slug_clean for x in verification_rows
        )
        if deleting_verification_like and len(verification_rows) <= 1:
            return await reply_once(
                interaction,
                {
                    "content": (
                        "❌ You cannot delete the only verification-like category.\n"
                        "Create another verification category first so unverified users still have a clear routing target."
                    ),
                    "ephemeral": True,
                },
            )

        replacement_default = None
        if bool(row.get("is_default")):
            replacement_default = _choose_replacement_default(rows_before, slug_clean)

        ok = await _delete_category(guild.id, slug_clean)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to delete ticket category.", "ephemeral": True})

        auto_msg = ""
        if replacement_default is not None:
            replacement_slug = _safe_str(replacement_default.get("slug"))
            if replacement_slug:
                await _set_default(guild.id, replacement_slug)
                auto_msg = f"\n⭐ Auto-promoted `{replacement_slug}` as the new default category."

        await reply_once(
            interaction,
            {"content": f"✅ Deleted ticket category `{slug_clean}`.{auto_msg}", "ephemeral": True},
        )

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

        ok = await _set_default(guild.id, slug_clean)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to set default category.", "ephemeral": True})

        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "is_default": True}
        embed = _category_embed("⭐ Default Ticket Category Updated", updated)

        rows = await _fetch_categories(guild.id)
        warnings = _governance_warnings(rows)
        if warnings:
            embed.add_field(
                name="Governance Warnings",
                value="\n".join([f"• {w}" for w in warnings])[:1024],
                inline=False,
            )

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

        sort_clean = _validated_sort_order(sort_order)
        if sort_clean is None:
            return await reply_once(
                interaction,
                {"content": f"❌ Sort order must be between `{_MIN_SORT_ORDER}` and `{_MAX_SORT_ORDER}`.", "ephemeral": True},
            )

        ok = await _update_category(guild.id, slug_clean, {"sort_order": sort_clean})
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to reorder ticket category.", "ephemeral": True})

        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "sort_order": sort_clean}
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
