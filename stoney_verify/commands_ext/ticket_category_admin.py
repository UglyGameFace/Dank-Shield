from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

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
# Source-of-truth helpers for ticket menu/routing categories.
#
# public_setup_solid._seed_recommended_categories() calls these helpers.
# Keep this file schema-tolerant so setup does not fail just because older
# Supabase deployments used slightly different column names.
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

_TABLE = "ticket_categories"
_MAX_NAME_LEN = 120
_MAX_DESC_LEN = 500
_MAX_SLUG_LEN = 80
_MAX_KEYWORD_LEN = 80
_MAX_KEYWORDS = 25
_MIN_SORT_ORDER = -9999
_MAX_SORT_ORDER = 9999
_LAST_CATEGORY_DB_ERROR = ""
_PROBE_COLUMNS = (
    "id",
    "guild_id",
    "slug",
    "category_slug",
    "name",
    "category_name",
    "label",
    "description",
    "intake_type",
    "type",
    "match_keywords",
    "keywords",
    "is_default",
    "default",
    "sort_order",
    "position",
    "created_at",
    "updated_at",
)


def _remember_error(error: Any) -> None:
    global _LAST_CATEGORY_DB_ERROR
    try:
        _LAST_CATEGORY_DB_ERROR = f"{type(error).__name__}: {str(error)[:500]}"
        print(f"⚠️ ticket_category_admin database operation failed: {_LAST_CATEGORY_DB_ERROR}")
    except Exception:
        _LAST_CATEGORY_DB_ERROR = "unknown database error"


def _last_category_db_error() -> str:
    return _LAST_CATEGORY_DB_ERROR


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


def _first(row: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row.get(name) is not None:
            return row.get(name)
    return default


def _normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    sort_order = _first(row, "sort_order", "position", default=None)
    try:
        sort_order = int(sort_order) if sort_order is not None else None
    except Exception:
        sort_order = None

    slug = _slugify(_safe_str(_first(row, "slug", "category_slug", default="")))
    name = _safe_str(_first(row, "name", "category_name", "label", default=slug), slug)
    intake_type = _safe_str(_first(row, "intake_type", "type", default="general"), "general").lower()

    return {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id")),
        "slug": slug,
        "name": name,
        "description": _safe_str(row.get("description")),
        "intake_type": intake_type,
        "match_keywords": _normalize_keywords(_first(row, "match_keywords", "keywords", default=[])),
        "is_default": bool(_first(row, "is_default", "default", default=False)),
        "sort_order": sort_order,
    }


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _column_supported_sync(column: str) -> bool:
    sb = _sb()
    if not sb:
        return False
    try:
        sb.table(_TABLE).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def _supported_columns_sync() -> set[str]:
    sb = _sb()
    if not sb:
        return set()
    try:
        sb.table(_TABLE).select("*").limit(1).execute()
    except Exception as e:
        _remember_error(e)
        return set()
    supported: set[str] = set()
    for col in _PROBE_COLUMNS:
        if _column_supported_sync(col):
            supported.add(col)
    return supported


def _schema_columns(columns: set[str]) -> tuple[str, str, str, str, str, str, str]:
    guild_col = "guild_id" if "guild_id" in columns else ""
    slug_col = "slug" if "slug" in columns else "category_slug" if "category_slug" in columns else ""
    name_col = "name" if "name" in columns else "category_name" if "category_name" in columns else "label" if "label" in columns else ""
    type_col = "intake_type" if "intake_type" in columns else "type" if "type" in columns else ""
    keywords_col = "match_keywords" if "match_keywords" in columns else "keywords" if "keywords" in columns else ""
    default_col = "is_default" if "is_default" in columns else "default" if "default" in columns else ""
    sort_col = "sort_order" if "sort_order" in columns else "position" if "position" in columns else ""
    return guild_col, slug_col, name_col, type_col, keywords_col, default_col, sort_col


def _query_guild_rows_sync(guild_id: int) -> tuple[list[dict[str, Any]], set[str]]:
    sb = _sb()
    columns = _supported_columns_sync()
    if not sb or not columns:
        return [], columns
    guild_col, *_ = _schema_columns(columns)
    if not guild_col:
        return [], columns
    try:
        res = sb.table(_TABLE).select("*").eq(guild_col, str(int(guild_id))).execute()
        rows = getattr(res, "data", None) or []
        return [dict(x) for x in rows if isinstance(x, Mapping)], columns
    except Exception as e:
        _remember_error(e)
        return [], columns


def _fetch_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    rows, _columns = _query_guild_rows_sync(guild_id)
    out = [_normalize_category_row(x) for x in rows]
    out.sort(
        key=lambda r: (
            r.get("sort_order") is None,
            r.get("sort_order") if r.get("sort_order") is not None else 10_000,
            r.get("name", "").lower(),
            r.get("slug", "").lower(),
        )
    )
    return out


async def _fetch_categories(guild_id: int) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_categories_sync, guild_id)


def _fetch_category_by_slug_sync(guild_id: int, slug: str) -> Optional[Dict[str, Any]]:
    slug_clean = _slugify(slug)
    if not slug_clean:
        return None
    rows = _fetch_categories_sync(guild_id)
    for row in rows:
        if _safe_str(row.get("slug")).lower() == slug_clean:
            return row
    return None


async def _fetch_category_by_slug(guild_id: int, slug: str) -> Optional[Dict[str, Any]]:
    return await _run_blocking(_fetch_category_by_slug_sync, guild_id, slug)


def _payload_for_schema(payload: Dict[str, Any], columns: set[str], *, keywords_as_text: bool = False) -> tuple[dict[str, Any], str]:
    guild_col, slug_col, name_col, type_col, keywords_col, default_col, sort_col = _schema_columns(columns)
    missing = []
    if not guild_col:
        missing.append("guild_id")
    if not slug_col:
        missing.append("slug/category_slug")
    if not name_col:
        missing.append("name/category_name/label")
    if missing:
        return {}, f"ticket_categories is missing required column(s): {', '.join(missing)}"

    slug = _slugify(payload.get("slug") or payload.get("category_slug") or payload.get("name") or "support")
    out: dict[str, Any] = {
        guild_col: str(int(payload.get("guild_id") or 0)),
        slug_col: slug,
        name_col: _normalize_name(payload.get("name") or payload.get("category_name") or payload.get("label") or slug),
    }
    if "description" in columns:
        out["description"] = _normalize_description(payload.get("description"))
    if type_col:
        out[type_col] = _safe_str(payload.get("intake_type") or payload.get("type"), "general").lower()
    if keywords_col:
        keywords = _normalize_keywords(payload.get("match_keywords") if "match_keywords" in payload else payload.get("keywords"))
        out[keywords_col] = ", ".join(keywords) if keywords_as_text else keywords
    if default_col:
        out[default_col] = bool(payload.get("is_default", payload.get("default", False)))
    if sort_col:
        sort_order = payload.get("sort_order", payload.get("position"))
        try:
            out[sort_col] = int(sort_order) if sort_order is not None else None
        except Exception:
            out[sort_col] = None
    return out, ""


def _insert_category_sync(payload: Dict[str, Any]) -> bool:
    sb = _sb()
    if not sb:
        return False
    columns = _supported_columns_sync()
    if not columns:
        return False

    guild_id = _safe_int(payload.get("guild_id"), 0)
    slug = _slugify(payload.get("slug") or payload.get("category_slug") or payload.get("name"))
    if guild_id and slug and _fetch_category_by_slug_sync(guild_id, slug):
        return True

    errors: list[str] = []
    for keywords_as_text in (False, True):
        db_payload, schema_error = _payload_for_schema(payload, columns, keywords_as_text=keywords_as_text)
        if schema_error:
            _remember_error(RuntimeError(schema_error))
            return False
        try:
            sb.table(_TABLE).insert(db_payload).execute()
            return True
        except Exception as e:
            errors.append(f"{type(e).__name__}: {str(e)[:240]}")
            _remember_error(e)
            if guild_id and slug and _fetch_category_by_slug_sync(guild_id, slug):
                return True
            continue
    if errors:
        print(f"⚠️ ticket_category_admin insert attempts failed for `{slug}`: {' | '.join(errors)}")
    return False


async def _insert_category(payload: Dict[str, Any]) -> bool:
    return await _run_blocking(_insert_category_sync, payload)


def _update_category_sync(guild_id: int, slug: str, patch: Dict[str, Any]) -> bool:
    sb = _sb()
    if not sb:
        return False
    columns = _supported_columns_sync()
    guild_col, slug_col, _name_col, _type_col, _kw_col, _default_col, _sort_col = _schema_columns(columns)
    if not guild_col or not slug_col:
        return False
    db_patch, schema_error = _payload_for_schema({"guild_id": guild_id, "slug": slug, **patch, "name": patch.get("name", slug)}, columns)
    if schema_error:
        return False
    db_patch.pop(guild_col, None)
    db_patch.pop(slug_col, None)
    try:
        sb.table(_TABLE).update(db_patch).eq(guild_col, str(int(guild_id))).eq(slug_col, _slugify(slug)).execute()
        return True
    except Exception as e:
        _remember_error(e)
        return False


async def _update_category(guild_id: int, slug: str, patch: Dict[str, Any]) -> bool:
    return await _run_blocking(_update_category_sync, guild_id, slug, patch)


def _delete_category_sync(guild_id: int, slug: str) -> bool:
    sb = _sb()
    if not sb:
        return False
    columns = _supported_columns_sync()
    guild_col, slug_col, *_rest = _schema_columns(columns)
    if not guild_col or not slug_col:
        return False
    try:
        sb.table(_TABLE).delete().eq(guild_col, str(int(guild_id))).eq(slug_col, _slugify(slug)).execute()
        return True
    except Exception as e:
        _remember_error(e)
        return False


async def _delete_category(guild_id: int, slug: str) -> bool:
    return await _run_blocking(_delete_category_sync, guild_id, slug)


def _clear_default_except_sync(guild_id: int, keep_slug: Optional[str] = None) -> bool:
    sb = _sb()
    if not sb:
        return False
    columns = _supported_columns_sync()
    guild_col, slug_col, _name_col, _type_col, _kw_col, default_col, _sort_col = _schema_columns(columns)
    if not guild_col or not default_col:
        return False
    try:
        query = sb.table(_TABLE).update({default_col: False}).eq(guild_col, str(int(guild_id))).eq(default_col, True)
        if keep_slug and slug_col:
            query = query.neq(slug_col, _slugify(keep_slug))
        query.execute()
        return True
    except Exception as e:
        _remember_error(e)
        return False


async def _clear_default_except(guild_id: int, keep_slug: Optional[str] = None) -> bool:
    return await _run_blocking(_clear_default_except_sync, guild_id, keep_slug)


def _set_default_sync(guild_id: int, slug: str) -> bool:
    sb = _sb()
    if not sb:
        return False
    columns = _supported_columns_sync()
    guild_col, slug_col, _name_col, _type_col, _kw_col, default_col, _sort_col = _schema_columns(columns)
    if not guild_col or not slug_col or not default_col:
        return False
    try:
        sb.table(_TABLE).update({default_col: True}).eq(guild_col, str(int(guild_id))).eq(slug_col, _slugify(slug)).execute()
        _clear_default_except_sync(guild_id, keep_slug=slug)
        return True
    except Exception as e:
        _remember_error(e)
        return False


async def _set_default(guild_id: int, slug: str) -> bool:
    return await _run_blocking(_set_default_sync, guild_id, slug)


def _duplicate_slugs(rows: List[Dict[str, Any]]) -> List[str]:
    counts: Dict[str, int] = {}
    for row in rows:
        slug = _safe_str(row.get("slug")).lower()
        if slug:
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
        if intake_type == "verification" or slug in {"verification", "verification-issue", "verification_issue"} or "verification" in name:
            out.append(row)
    return out


def _choose_replacement_default(rows: List[Dict[str, Any]], deleted_slug: str) -> Optional[Dict[str, Any]]:
    candidates = [row for row in rows if _safe_str(row.get("slug")).lower() != deleted_slug.lower()]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r.get("sort_order") is None, r.get("sort_order") if r.get("sort_order") is not None else 10_000, r.get("name", "").lower(), r.get("slug", "").lower()))
    return candidates[0]


def _category_embed(title: str, row: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Name", value=_safe_str(row.get("name"), "unknown"), inline=True)
    embed.add_field(name="Slug", value=f"`{_safe_str(row.get('slug'), 'unknown')}`", inline=True)
    embed.add_field(name="Type", value=f"`{_safe_str(row.get('intake_type'), 'general')}`", inline=True)
    embed.add_field(name="Default", value="Yes" if bool(row.get("is_default")) else "No", inline=True)
    embed.add_field(name="Sort Order", value=f"`{row.get('sort_order')}`", inline=True)
    embed.add_field(name="Keywords", value=((", ".join([f"`{_truncate(x, 40)}`" for x in (row.get("match_keywords") or [])])) or "—")[:1024], inline=False)
    embed.add_field(name="Description", value=_truncate(row.get("description") or "No description.", 1024), inline=False)
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


def _payload_from_command(guild: discord.Guild, *, name: str, slug: str, intake_type: str, description: Optional[str], keywords: Optional[str], is_default: Optional[bool], sort_order: Optional[int]) -> tuple[Optional[dict[str, Any]], str]:
    slug_clean = _slugify(slug)
    if not slug_clean:
        return None, "Invalid slug."
    name_clean = _normalize_name(name)
    if not name_clean:
        return None, "Category name cannot be empty."
    type_clean = _safe_str(intake_type, "general").lower()
    if type_clean not in _ALLOWED_INTAKE_TYPES:
        return None, f"Invalid intake type. Use one of: {_human_intake_types()}"
    sort_clean = _validated_sort_order(sort_order)
    if sort_order is not None and sort_clean is None:
        return None, f"Sort order must be between `{_MIN_SORT_ORDER}` and `{_MAX_SORT_ORDER}`."
    return {
        "guild_id": str(guild.id),
        "slug": slug_clean,
        "name": name_clean,
        "description": _normalize_description(description),
        "intake_type": type_clean,
        "match_keywords": _normalize_keywords(_safe_str(keywords)),
        "is_default": bool(is_default),
        "sort_order": sort_clean,
    }, ""


def register_ticket_category_admin_commands(bot, tree) -> None:
    @tree.command(name="ticket_category_create", description="Create a new dashboard ticket category.")
    @app_commands.describe(name="Display name for the category", slug="URL-style slug, like support or verification-issue", intake_type="Type used by routing logic", description="Optional category description", keywords="Comma-separated match keywords", is_default="Whether this should be the default category", sort_order="Optional sort order")
    async def ticket_category_create(interaction: discord.Interaction, name: str, slug: str, intake_type: str, description: Optional[str] = None, keywords: Optional[str] = None, is_default: Optional[bool] = False, sort_order: Optional[int] = None):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        payload, error = _payload_from_command(guild, name=name, slug=slug, intake_type=intake_type, description=description, keywords=keywords, is_default=is_default, sort_order=sort_order)
        if error or payload is None:
            return await reply_once(interaction, {"content": f"❌ {error}", "ephemeral": True})
        slug_clean = _safe_str(payload.get("slug"))
        existing = await _fetch_category_by_slug(guild.id, slug_clean)
        if existing:
            return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` already exists.", "ephemeral": True})
        ok = await _insert_category(payload)
        if not ok:
            detail = _last_category_db_error() or "unknown database error"
            return await reply_once(interaction, {"content": f"❌ Failed to create ticket category. `{detail[:800]}`", "ephemeral": True})
        if bool(is_default):
            await _set_default(guild.id, slug_clean)
        created = await _fetch_category_by_slug(guild.id, slug_clean) or payload
        embed = _category_embed("✅ Ticket Category Created", created)
        warnings = _governance_warnings(await _fetch_categories(guild.id))
        if warnings:
            embed.add_field(name="Governance Warnings", value="\n".join([f"• {w}" for w in warnings])[:1024], inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(name="ticket_category_edit", description="Edit an existing dashboard ticket category.")
    @app_commands.describe(slug="Existing category slug to edit", name="New display name", intake_type="New intake type", description="New description", keywords="New comma-separated keywords", is_default="Set or unset as default", sort_order="New sort order")
    async def ticket_category_edit(interaction: discord.Interaction, slug: str, name: Optional[str] = None, intake_type: Optional[str] = None, description: Optional[str] = None, keywords: Optional[str] = None, is_default: Optional[bool] = None, sort_order: Optional[int] = None):
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
                return await reply_once(interaction, {"content": f"❌ Invalid intake type. Use one of: {_human_intake_types()}", "ephemeral": True})
            patch["intake_type"] = type_clean
        if description is not None:
            patch["description"] = _normalize_description(description)
        if keywords is not None:
            patch["match_keywords"] = _normalize_keywords(_safe_str(keywords))
        if sort_order is not None:
            sort_clean = _validated_sort_order(sort_order)
            if sort_clean is None:
                return await reply_once(interaction, {"content": f"❌ Sort order must be between `{_MIN_SORT_ORDER}` and `{_MAX_SORT_ORDER}`.", "ephemeral": True})
            patch["sort_order"] = sort_clean
        if is_default is not None:
            patch["is_default"] = bool(is_default)
        if not patch:
            return await reply_once(interaction, {"content": "ℹ️ Nothing to update.", "ephemeral": True})
        ok = await _update_category(guild.id, slug_clean, patch)
        if not ok:
            return await reply_once(interaction, {"content": f"❌ Failed to update ticket category. `{_last_category_db_error()[:800]}`", "ephemeral": True})
        if is_default is True:
            await _set_default(guild.id, slug_clean)
        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, **patch}
        embed = _category_embed("✅ Ticket Category Updated", updated)
        warnings = _governance_warnings(await _fetch_categories(guild.id))
        if warnings:
            embed.add_field(name="Governance Warnings", value="\n".join([f"• {w}" for w in warnings])[:1024], inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(name="ticket_category_delete", description="Delete a dashboard ticket category.")
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
            return await reply_once(interaction, {"content": "❌ You cannot delete the only remaining ticket category. Create another first.", "ephemeral": True})
        verification_rows = _verification_like_categories(rows_before)
        if any(_safe_str(x.get("slug")).lower() == slug_clean for x in verification_rows) and len(verification_rows) <= 1:
            return await reply_once(interaction, {"content": "❌ You cannot delete the only verification-like category.", "ephemeral": True})
        replacement_default = _choose_replacement_default(rows_before, slug_clean) if bool(row.get("is_default")) else None
        ok = await _delete_category(guild.id, slug_clean)
        if not ok:
            return await reply_once(interaction, {"content": f"❌ Failed to delete ticket category. `{_last_category_db_error()[:800]}`", "ephemeral": True})
        auto_msg = ""
        if replacement_default is not None:
            replacement_slug = _safe_str(replacement_default.get("slug"))
            if replacement_slug:
                await _set_default(guild.id, replacement_slug)
                auto_msg = f"\n⭐ Auto-promoted `{replacement_slug}` as the new default category."
        await reply_once(interaction, {"content": f"✅ Deleted ticket category `{slug_clean}`.{auto_msg}", "ephemeral": True})

    @tree.command(name="ticket_category_set_default", description="Set the default dashboard ticket category.")
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
            return await reply_once(interaction, {"content": f"❌ Failed to set default category. `{_last_category_db_error()[:800]}`", "ephemeral": True})
        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "is_default": True}
        await reply_once(interaction, {"embed": _category_embed("⭐ Default Ticket Category Updated", updated), "ephemeral": True})

    @tree.command(name="ticket_category_reorder", description="Set the sort order for a dashboard ticket category.")
    @app_commands.describe(slug="Category slug to reorder", sort_order="Lower numbers appear first")
    async def ticket_category_reorder(interaction: discord.Interaction, slug: str, sort_order: int):
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
            return await reply_once(interaction, {"content": f"❌ Sort order must be between `{_MIN_SORT_ORDER}` and `{_MAX_SORT_ORDER}`.", "ephemeral": True})
        ok = await _update_category(guild.id, slug_clean, {"sort_order": sort_clean})
        if not ok:
            return await reply_once(interaction, {"content": f"❌ Failed to reorder ticket category. `{_last_category_db_error()[:800]}`", "ephemeral": True})
        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "sort_order": sort_clean}
        await reply_once(interaction, {"embed": _category_embed("↕️ Ticket Category Reordered", updated), "ephemeral": True})

    @tree.command(name="ticket_category_keywords", description="Replace the keyword list for a dashboard ticket category.")
    @app_commands.describe(slug="Category slug to update", keywords="Comma-separated keywords to use for routing")
    async def ticket_category_keywords(interaction: discord.Interaction, slug: str, keywords: str):
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
            return await reply_once(interaction, {"content": f"❌ Failed to update category keywords. `{_last_category_db_error()[:800]}`", "ephemeral": True})
        updated = await _fetch_category_by_slug(guild.id, slug_clean) or {**row, "match_keywords": kw}
        await reply_once(interaction, {"embed": _category_embed("🏷️ Ticket Category Keywords Updated", updated), "ephemeral": True})
