from __future__ import annotations

"""Robust recommended ticket-menu seeding for /stoney setup.

The Discord auto-build can succeed while the recommended ticket menu fails if
`ticket_categories` differs slightly between deployments. This module patches
public_setup_solid so the seed path probes the table columns first and inserts
using the schema that actually exists.

Supported column aliases:
- slug or category_slug
- name, category_name, or label
- intake_type or type
- match_keywords or keywords
- is_default or default
- sort_order or position
"""

import asyncio
from typing import Any, Mapping, Optional

import discord

from ..globals import get_supabase
from . import public_setup_solid as solid

_PATCHED = False
_TABLE = "ticket_categories"
_PROBE_COLUMNS = (
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


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _slugify(value: Any) -> str:
    try:
        from . import ticket_category_admin as category_admin

        slug = category_admin._slugify(str(value or ""))
        return slug or "support"
    except Exception:
        text = _safe_str(value, "support").lower()
        out = []
        dash = False
        for ch in text:
            if ch.isalnum():
                out.append(ch)
                dash = False
            elif not dash:
                out.append("-")
                dash = True
        return ("".join(out).strip("-") or "support")[:80]


def _normalize_keywords(value: Any) -> list[str]:
    try:
        if isinstance(value, list):
            raw = value
        else:
            raw = str(value or "").split(",")
        out: list[str] = []
        for item in raw:
            text = str(item or "").strip().lower()
            if text and text not in out:
                out.append(text[:80])
        return out[:25]
    except Exception:
        return []


def _sort_value(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _first(row: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row.get(name) is not None:
            return row.get(name)
    return default


def _column_supported_sync(column: str) -> bool:
    sb = get_supabase()
    if sb is None:
        return False
    try:
        sb.table(_TABLE).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def _probe_columns_sync() -> tuple[set[str], str]:
    sb = get_supabase()
    if sb is None:
        return set(), "Supabase is not available."

    # First confirm the table exists at all.
    try:
        sb.table(_TABLE).select("*").limit(1).execute()
    except Exception as e:
        return set(), f"Could not read `{_TABLE}`: {type(e).__name__}: {str(e)[:300]}"

    supported: set[str] = set()
    for column in _PROBE_COLUMNS:
        if _column_supported_sync(column):
            supported.add(column)
    return supported, ""


def _select_columns(columns: set[str]) -> tuple[str, str, str, str, str, str, str]:
    guild_col = "guild_id" if "guild_id" in columns else ""
    slug_col = "slug" if "slug" in columns else "category_slug" if "category_slug" in columns else ""
    name_col = "name" if "name" in columns else "category_name" if "category_name" in columns else "label" if "label" in columns else ""
    type_col = "intake_type" if "intake_type" in columns else "type" if "type" in columns else ""
    keywords_col = "match_keywords" if "match_keywords" in columns else "keywords" if "keywords" in columns else ""
    default_col = "is_default" if "is_default" in columns else "default" if "default" in columns else ""
    sort_col = "sort_order" if "sort_order" in columns else "position" if "position" in columns else ""
    return guild_col, slug_col, name_col, type_col, keywords_col, default_col, sort_col


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    slug = _slugify(_first(row, "slug", "category_slug", default=""))
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
        "sort_order": _sort_value(_first(row, "sort_order", "position", default=None)),
    }


def _fetch_rows_sync(guild_id: int) -> tuple[list[dict[str, Any]], str, set[str]]:
    sb = get_supabase()
    if sb is None:
        return [], "Supabase is not available.", set()
    columns, error = _probe_columns_sync()
    if error:
        return [], error, columns
    guild_col, _slug_col, _name_col, _type_col, _kw_col, _default_col, _sort_col = _select_columns(columns)
    if not guild_col:
        return [], "`ticket_categories` is missing required `guild_id` column.", columns
    try:
        res = sb.table(_TABLE).select("*").eq(guild_col, str(int(guild_id))).execute()
        rows = getattr(res, "data", None) or []
        out = [_normalize_row(row) for row in rows if isinstance(row, Mapping)]
        out.sort(
            key=lambda r: (
                r.get("sort_order") is None,
                r.get("sort_order") if r.get("sort_order") is not None else 10_000,
                str(r.get("name") or "").lower(),
                str(r.get("slug") or "").lower(),
            )
        )
        return out, "", columns
    except Exception as e:
        return [], f"Could not read `{_TABLE}` rows: {type(e).__name__}: {str(e)[:300]}", columns


def _payload_for_schema(guild_id: int, item: Mapping[str, Any], columns: set[str], *, keywords_as_text: bool = False) -> tuple[dict[str, Any], str]:
    guild_col, slug_col, name_col, type_col, keywords_col, default_col, sort_col = _select_columns(columns)
    if not guild_col or not slug_col or not name_col:
        missing = []
        if not guild_col:
            missing.append("guild_id")
        if not slug_col:
            missing.append("slug/category_slug")
        if not name_col:
            missing.append("name/category_name/label")
        return {}, f"`ticket_categories` is missing required columns: {', '.join(missing)}."

    slug = _slugify(item.get("slug"))
    keywords = _normalize_keywords(item.get("match_keywords"))
    payload: dict[str, Any] = {
        guild_col: str(int(guild_id)),
        slug_col: slug,
        name_col: _safe_str(item.get("name"), slug)[:120],
    }
    if "description" in columns:
        payload["description"] = _safe_str(item.get("description"))[:500]
    if type_col:
        payload[type_col] = _safe_str(item.get("intake_type"), "general").lower()[:80]
    if keywords_col:
        payload[keywords_col] = ", ".join(keywords) if keywords_as_text else keywords
    if default_col:
        payload[default_col] = bool(item.get("is_default"))
    if sort_col:
        payload[sort_col] = _sort_value(item.get("sort_order"))
    return payload, ""


def _insert_sync(guild_id: int, item: Mapping[str, Any], columns: set[str]) -> tuple[bool, str]:
    sb = get_supabase()
    if sb is None:
        return False, "Supabase is not available."

    errors: list[str] = []
    for keywords_as_text in (False, True):
        payload, error = _payload_for_schema(guild_id, item, columns, keywords_as_text=keywords_as_text)
        if error:
            return False, error
        try:
            sb.table(_TABLE).insert(payload).execute()
            return True, ""
        except Exception as e:
            errors.append(f"{type(e).__name__}: {str(e)[:260]}")

    return False, " | ".join(errors[-2:]) or "insert failed"


def _set_default_sync(guild_id: int, slug: str, columns: set[str]) -> None:
    sb = get_supabase()
    if sb is None:
        return
    guild_col, slug_col, _name_col, _type_col, _kw_col, default_col, _sort_col = _select_columns(columns)
    if not guild_col or not slug_col or not default_col:
        return
    try:
        sb.table(_TABLE).update({default_col: False}).eq(guild_col, str(int(guild_id))).eq(default_col, True).execute()
    except Exception:
        pass
    try:
        sb.table(_TABLE).update({default_col: True}).eq(guild_col, str(int(guild_id))).eq(slug_col, slug).execute()
    except Exception:
        pass


async def _patched_category_load(guild: discord.Guild) -> solid.CategoryLoad:
    rows, error, _columns = await asyncio.to_thread(_fetch_rows_sync, int(guild.id))
    return solid.CategoryLoad(rows, error)


async def _patched_seed_recommended_categories(guild: discord.Guild) -> tuple[list[str], list[str], str]:
    rows, error, columns = await asyncio.to_thread(_fetch_rows_sync, int(guild.id))
    if error:
        return [], [], error

    existing = {_slugify(row.get("slug")) for row in rows if _slugify(row.get("slug"))}
    has_default = any(bool(row.get("is_default")) for row in rows)
    created: list[str] = []
    skipped: list[str] = []

    for item in solid.RECOMMENDED_CATEGORIES:
        slug = _slugify(item.get("slug"))
        if slug in existing:
            skipped.append(slug)
            continue

        payload_item = dict(item)
        if slug == "support" and not has_default:
            payload_item["is_default"] = True

        ok, insert_error = await asyncio.to_thread(_insert_sync, int(guild.id), payload_item, columns)
        if not ok:
            detail = insert_error or "unknown insert error"
            return created, skipped, f"Database insert failed while creating `{slug}`. {detail}"

        created.append(slug)
        existing.add(slug)
        if bool(payload_item.get("is_default")):
            has_default = True

    if not has_default:
        # Make support default if the schema supports a default column.
        await asyncio.to_thread(_set_default_sync, int(guild.id), "support", columns)

    return created, skipped, ""


def _patch() -> None:
    global _PATCHED
    solid._category_load = _patched_category_load
    solid._seed_recommended_categories = _patched_seed_recommended_categories
    _PATCHED = True


_patch()


def register_public_setup_category_seed_guard(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_category_seed_guard: robust recommended ticket menu seeding active")


__all__ = ["register_public_setup_category_seed_guard"]
