from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import asyncio
import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from .common import _staff_check, reply_once, safe_defer, mark_ticket_activity

try:
    from ..tickets_new.repository import (
        get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
    )
except Exception:
    async def repo_get_ticket_by_any_channel_id(channel_id: int | str):  # type: ignore
        return None

try:
    from ..tickets_new.macros_service import (
        list_ticket_macros,
        get_ticket_macro,
        send_ticket_macro,
        preview_ticket_macro,
    )
except Exception:
    async def list_ticket_macros(*args, **kwargs):  # type: ignore
        return []

    async def get_ticket_macro(*args, **kwargs):  # type: ignore
        return None

    async def send_ticket_macro(*args, **kwargs):  # type: ignore
        return {"ok": False, "message": "Macro service unavailable."}

    async def preview_ticket_macro(*args, **kwargs):  # type: ignore
        return {"ok": False, "message": "Macro service unavailable."}


_VALID_BOOL_TRUE = {"1", "true", "yes", "y", "on"}
_VALID_BOOL_FALSE = {"0", "false", "no", "n", "off"}
_VALID_TICKET_STATUSES = {"open", "claimed", "closed", "deleted"}
_MAX_MACRO_BODY = 4000
_MAX_MACRO_SLUG = 80
_MAX_MACRO_NAME = 120
_MAX_MACRO_CATEGORY = 80
_MAX_MACRO_LIST = 40


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = _safe_str(value).lower()
        if raw in _VALID_BOOL_TRUE:
            return True
        if raw in _VALID_BOOL_FALSE:
            return False
        return default
    except Exception:
        return default


def _truncate(text: Any, limit: int = 300) -> str:
    raw = _safe_str(text)
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _normalize_slug(value: Any) -> str:
    text = _safe_str(value).strip().lower()
    text = text.replace("&", " and ")
    out: List[str] = []
    prev_dash = False

    for ch in text:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True

    slug = "".join(out).strip("-")
    return slug[:_MAX_MACRO_SLUG]


def _clean_body(value: Any, limit: int = _MAX_MACRO_BODY) -> str:
    try:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return text[:limit]
    except Exception:
        return ""


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("status"), "unknown").lower()
        if raw in _VALID_TICKET_STATUSES:
            return raw
        if raw in {"active", "reopened"}:
            return "open"
    except Exception:
        pass
    return "unknown"


def _ticket_archive_category_id() -> int:
    for key in (
        "TICKET_ARCHIVE_CATEGORY_ID",
        "TICKET_ARCHIVED_CATEGORY_ID",
        "ARCHIVED_TICKET_CATEGORY_ID",
        "ARCHIVE_TICKET_CATEGORY_ID",
    ):
        try:
            value = int(globals().get(key, 0) or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _looks_like_archive_category_name(name: str) -> bool:
    text = _safe_str(name).lower()
    if not text:
        return False

    markers = (
        "archive",
        "archived",
        "ticket archive",
        "tickets archive",
        "archived tickets",
        "closed tickets",
    )
    return any(marker in text for marker in markers)


def _resolve_category_by_id(
    guild: discord.Guild,
    category_id: int,
) -> Optional[discord.CategoryChannel]:
    try:
        if category_id <= 0:
            return None
        channel = guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
    except Exception:
        pass
    return None


def _resolve_archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_archive_category_id()
    if explicit_id > 0:
        explicit = _resolve_category_by_id(guild, explicit_id)
        if explicit is not None:
            return explicit

    try:
        for category in guild.categories:
            if _looks_like_archive_category_name(category.name):
                return category
    except Exception:
        pass

    return None


def _channel_is_in_category(
    channel: discord.TextChannel,
    category: Optional[discord.CategoryChannel],
) -> bool:
    try:
        if category is None:
            return False
        return int(getattr(channel.category, "id", 0) or 0) == int(category.id)
    except Exception:
        return False


def _channel_is_in_archive_category(channel: discord.TextChannel) -> bool:
    archive_category = _resolve_archive_category(channel.guild)
    if archive_category and _channel_is_in_category(channel, archive_category):
        return True
    try:
        if channel.category and _looks_like_archive_category_name(channel.category.name):
            return True
    except Exception:
        pass
    return False


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("closed-")
    except Exception:
        return False


def _ticket_effectively_closed(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> bool:
    status = _ticket_status(row)
    if status in {"closed", "deleted"}:
        return True
    if _channel_looks_closed(channel):
        return True
    if _channel_is_in_archive_category(channel):
        return True
    return False


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    try:
        row = await repo_get_ticket_by_any_channel_id(int(channel.id))
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        return None


async def _ensure_ticket_context(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await reply_once(interaction, {"content": "❌ Must be used in a ticket text channel.", "ephemeral": True})
        return None, None

    row = await _ticket_row_for_channel(ch)
    if not isinstance(row, dict):
        await reply_once(
            interaction,
            {"content": f"❌ `{ch.name}` is not recognized as a ticket channel.", "ephemeral": True},
        )
        return None, None

    return ch, row


def _macros_table():
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase is not configured.")
    return sb.table("ticket_macros")


def _fetch_db_macro_by_slug_sync(guild_id: int, slug: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            _macros_table()
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("slug", str(slug).strip().lower())
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception:
        return None
    return None


async def _fetch_db_macro_by_slug(guild_id: int, slug: str) -> Optional[Dict[str, Any]]:
    return await _run_blocking(_fetch_db_macro_by_slug_sync, guild_id, slug)


def _upsert_macro_sync(
    guild_id: int,
    *,
    slug: str,
    name: str,
    body: str,
    category: Optional[str],
    sort_order: int,
    send_as_note: bool,
    is_active: bool,
    actor_id: Optional[int],
) -> bool:
    existing = _fetch_db_macro_by_slug_sync(guild_id, slug)
    now_iso = now_utc().isoformat()

    base_payload = {
        "guild_id": str(int(guild_id)),
        "slug": str(slug).strip().lower(),
        "name": str(name).strip(),
        "body": str(body).strip(),
        "category": str(category).strip().lower() if category else None,
        "is_active": bool(is_active),
        "sort_order": int(sort_order),
        "send_as_note": bool(send_as_note),
        "updated_at": now_iso,
        "updated_by": str(actor_id) if actor_id else None,
    }

    try:
        if existing and existing.get("id") is not None:
            payload = dict(base_payload)
            (
                _macros_table()
                .update(payload)
                .eq("id", str(existing["id"]))
                .execute()
            )
            return True

        payload = dict(base_payload)
        payload["created_at"] = now_iso
        payload["created_by"] = str(actor_id) if actor_id else None
        (
            _macros_table()
            .insert(payload)
            .execute()
        )
        return True
    except Exception:
        return False


async def _upsert_macro(
    guild_id: int,
    *,
    slug: str,
    name: str,
    body: str,
    category: Optional[str],
    sort_order: int,
    send_as_note: bool,
    is_active: bool,
    actor_id: Optional[int],
) -> bool:
    return await _run_blocking(
        _upsert_macro_sync,
        guild_id,
        slug=slug,
        name=name,
        body=body,
        category=category,
        sort_order=sort_order,
        send_as_note=send_as_note,
        is_active=is_active,
        actor_id=actor_id,
    )


def _delete_macro_sync(guild_id: int, slug: str) -> bool:
    try:
        existing = _fetch_db_macro_by_slug_sync(guild_id, slug)
        if not existing:
            return False
        (
            _macros_table()
            .delete()
            .eq("id", str(existing["id"]))
            .execute()
        )
        return True
    except Exception:
        return False


async def _delete_macro(guild_id: int, slug: str) -> bool:
    return await _run_blocking(_delete_macro_sync, guild_id, slug)


def _macro_source_value(row: Dict[str, Any]) -> str:
    return _safe_str(row.get("_source"), "db")


def _macro_is_active(row: Dict[str, Any]) -> bool:
    return _safe_bool(row.get("active", row.get("is_active", True)), True)


def _macro_embed(row: Dict[str, Any], *, title: str = "🔎 Ticket Macro") -> discord.Embed:
    embed = discord.Embed(
        title=title,
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )

    embed.add_field(name="Slug", value=f"`{_safe_str(row.get('slug'), 'unknown')}`", inline=True)
    embed.add_field(name="Name", value=_truncate(row.get("name"), 200) or "Unnamed", inline=True)
    embed.add_field(
        name="Category",
        value=f"`{_safe_str(row.get('category'), 'all') or 'all'}`",
        inline=True,
    )
    embed.add_field(
        name="Source",
        value=f"`{_macro_source_value(row)}`",
        inline=True,
    )
    embed.add_field(
        name="Sort Order",
        value=f"`{_safe_int(row.get('sort_order'), 0)}`",
        inline=True,
    )
    embed.add_field(
        name="Send As Note",
        value="Yes" if _safe_bool(row.get("send_as_note"), False) else "No",
        inline=True,
    )
    embed.add_field(
        name="Active",
        value="Yes" if _macro_is_active(row) else "No",
        inline=True,
    )

    aliases = row.get("aliases") or []
    if aliases:
        alias_values = ", ".join([f"`{_safe_str(x)}`" for x in aliases[:10]])
        embed.add_field(
            name="Aliases",
            value=alias_values[:1024],
            inline=False,
        )

    body = _clean_body(row.get("body") or row.get("content"))
    embed.add_field(name="Body", value=_truncate(body, 4000) or "—", inline=False)
    return embed


def _macro_list_line(row: Dict[str, Any]) -> str:
    row_slug = _safe_str(row.get("slug"), "unknown")
    row_name = _truncate(row.get("name"), 60) or row_slug
    row_category = _safe_str(row.get("category"), "all") or "all"
    row_source = _macro_source_value(row)

    note_flag = " • note" if _safe_bool(row.get("send_as_note"), False) else ""
    inactive_flag = " • inactive" if not _macro_is_active(row) else ""

    return f"• `{row_slug}` — {row_name} • `{row_category}` • `{row_source}`{note_flag}{inactive_flag}"


def _chunk_lines(lines: Sequence[str], limit: int = 3500) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for line in lines:
        add_len = len(line) + 1
        if current and (current_len + add_len) > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

        current.append(line)
        current_len += add_len

    if current:
        chunks.append("\n".join(current))

    return chunks or []


async def _send_paginated_macro_embeds(
    interaction: discord.Interaction,
    *,
    title: str,
    description: Optional[str],
    color: discord.Color,
    chunks: List[str],
    footer_prefix: str,
) -> None:
    if not chunks:
        embed = discord.Embed(
            title=title,
            description=description or "No results found.",
            color=color,
            timestamp=now_utc(),
        )
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    embeds: List[discord.Embed] = []
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        embed = discord.Embed(
            title=title if index == 1 else f"{title} (cont.)",
            description=chunk if not description or index != 1 else f"{description}\n\n{chunk}",
            color=color,
            timestamp=now_utc(),
        )
        embed.set_footer(text=f"{footer_prefix} • Page {index}/{total}")
        embeds.append(embed)

    await reply_once(interaction, {"embed": embeds[0], "ephemeral": True})
    for embed in embeds[1:]:
        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            break


def _category_hint_from_ticket_row(row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(row, dict):
        return None

    for key in ("category", "matched_category_slug", "matched_category_name"):
        value = _normalize_slug(row.get(key))
        if value:
            return value

    return None


def register_ticket_macro_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_macro_add",
        description="Create or update a ticket macro using the unified macro schema.",
    )
    @app_commands.describe(
        slug="Stable macro slug like welcome or verification-reminder",
        body="Macro body. Supports placeholders like {user_mention}, {staff_mention}, {ticket_number}",
        name="Optional display name",
        category="Optional category slug like verification_issue or ghost",
        send_as_note="Save as internal note instead of sending to the ticket",
        sort_order="Lower appears earlier",
        active="Whether this macro is active",
    )
    async def ticket_macro_add(
        interaction: discord.Interaction,
        slug: str,
        body: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
        send_as_note: Optional[bool] = False,
        sort_order: Optional[int] = 100,
        active: Optional[bool] = True,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        clean_slug = _normalize_slug(slug)
        clean_body = _clean_body(body)
        clean_name = (_safe_str(name) or clean_slug.replace("-", " ").title())[:_MAX_MACRO_NAME]
        clean_category = _normalize_slug(category)[:_MAX_MACRO_CATEGORY] if category else None
        clean_sort = max(0, _safe_int(sort_order, 100))
        clean_active = bool(active)
        clean_send_as_note = bool(send_as_note)

        if not clean_slug:
            return await reply_once(interaction, {"content": "❌ Macro slug cannot be empty.", "ephemeral": True})
        if not clean_body:
            return await reply_once(interaction, {"content": "❌ Macro body cannot be empty.", "ephemeral": True})

        ok = await _upsert_macro(
            guild.id,
            slug=clean_slug,
            name=clean_name,
            body=clean_body,
            category=clean_category,
            sort_order=clean_sort,
            send_as_note=clean_send_as_note,
            is_active=clean_active,
            actor_id=_safe_int(getattr(interaction.user, "id", 0), 0) or None,
        )
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to save macro.", "ephemeral": True})

        row = await get_ticket_macro(
            guild_id=guild.id,
            slug=clean_slug,
            category=clean_category,
        ) or {
            "slug": clean_slug,
            "name": clean_name,
            "body": clean_body,
            "category": clean_category,
            "sort_order": clean_sort,
            "send_as_note": clean_send_as_note,
            "active": clean_active,
            "_source": "db",
        }

        embed = _macro_embed(row, title="💬 Ticket Macro Saved")
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_macro_delete",
        description="Delete a DB-backed ticket macro by slug.",
    )
    @app_commands.describe(slug="Macro slug to delete")
    async def ticket_macro_delete(
        interaction: discord.Interaction,
        slug: str,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        clean_slug = _normalize_slug(slug)
        if not clean_slug:
            return await reply_once(interaction, {"content": "❌ Macro slug cannot be empty.", "ephemeral": True})

        db_row = await _fetch_db_macro_by_slug(guild.id, clean_slug)
        if not db_row:
            return await reply_once(
                interaction,
                {"content": f"❌ DB macro `{clean_slug}` was not found.", "ephemeral": True},
            )

        ok = await _delete_macro(guild.id, clean_slug)
        if not ok:
            return await reply_once(interaction, {"content": "❌ Failed to delete macro.", "ephemeral": True})

        await reply_once(
            interaction,
            {"content": f"✅ Deleted DB macro `{clean_slug}`.", "ephemeral": True},
        )

    @tree.command(
        name="ticket_macro_list",
        description="List available ticket macros for this guild.",
    )
    @app_commands.describe(category="Optional category filter like verification_issue")
    async def ticket_macro_list(
        interaction: discord.Interaction,
        category: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        clean_category = _normalize_slug(category) if category else None
        rows = await list_ticket_macros(
            guild_id=guild.id,
            category=clean_category,
            include_defaults=True,
        )

        filtered_rows = [row for row in rows if isinstance(row, dict)]
        filtered_rows = filtered_rows[:_MAX_MACRO_LIST]

        lines = [_macro_list_line(row) for row in filtered_rows]
        chunks = _chunk_lines(lines)

        description = f"Category filter: `{clean_category}`" if clean_category else None
        await _send_paginated_macro_embeds(
            interaction,
            title="📚 Ticket Macros",
            description=description,
            color=discord.Color.blurple(),
            chunks=chunks,
            footer_prefix=f"Showing {len(filtered_rows)} macro(s)",
        )

    @tree.command(
        name="ticket_macro_show",
        description="Show a single ticket macro and preview its template body.",
    )
    @app_commands.describe(
        slug="Macro slug to inspect",
        category="Optional category hint for category-specific macros",
    )
    async def ticket_macro_show(
        interaction: discord.Interaction,
        slug: str,
        category: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        clean_slug = _normalize_slug(slug)
        clean_category = _normalize_slug(category) if category else None
        if not clean_slug:
            return await reply_once(interaction, {"content": "❌ Macro slug cannot be empty.", "ephemeral": True})

        row = await get_ticket_macro(
            guild_id=guild.id,
            slug=clean_slug,
            category=clean_category,
        )
        if not row:
            return await reply_once(
                interaction,
                {"content": f"❌ Macro `{clean_slug}` was not found.", "ephemeral": True},
            )

        embed = _macro_embed(row)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_macro_preview",
        description="Preview how a ticket macro will render in the current ticket.",
    )
    @app_commands.describe(
        slug="Macro slug to preview",
        channel="Ticket channel to preview in (leave empty for current channel)",
    )
    async def ticket_macro_preview_cmd(
        interaction: discord.Interaction,
        slug: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        clean_slug = _normalize_slug(slug)
        if not clean_slug:
            return await reply_once(interaction, {"content": "❌ Macro slug cannot be empty.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        preview = await preview_ticket_macro(
            channel=ch,
            slug=clean_slug,
            actor=interaction.user,
        )
        if not preview.get("ok"):
            return await reply_once(
                interaction,
                {"content": f"❌ {preview.get('message') or 'Preview failed.'}", "ephemeral": True},
            )

        macro = preview.get("macro") or {}
        rendered = _clean_body(preview.get("content"), limit=3500)
        category_hint = _category_hint_from_ticket_row(row)

        embed = discord.Embed(
            title="👀 Ticket Macro Preview",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Slug", value=f"`{_safe_str(macro.get('slug'), clean_slug)}`", inline=True)
        embed.add_field(name="Channel", value=ch.mention, inline=True)
        embed.add_field(
            name="Ticket Category",
            value=f"`{category_hint or 'unknown'}`",
            inline=True,
        )
        embed.add_field(
            name="Ticket State",
            value="Closed / archived" if _ticket_effectively_closed(channel=ch, row=row) else "Active",
            inline=True,
        )
        embed.add_field(
            name="Rendered Output",
            value=rendered or "—",
            inline=False,
        )
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_macro_send",
        description="Send a ticket macro into the current active ticket.",
    )
    @app_commands.describe(
        slug="Macro slug to send",
        channel="Ticket channel to send into (leave empty to use current channel)",
    )
    async def ticket_macro_send_cmd(
        interaction: discord.Interaction,
        slug: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        clean_slug = _normalize_slug(slug)
        if not clean_slug:
            return await reply_once(interaction, {"content": "❌ Macro slug cannot be empty.", "ephemeral": True})

        await safe_defer(interaction, ephemeral=True)

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status == "deleted":
            return await interaction.followup.send("❌ Deleted tickets cannot send macros.", ephemeral=True)

        if _ticket_effectively_closed(channel=ch, row=row):
            location_hint = "This ticket is closed/archived."
            if _channel_is_in_archive_category(ch):
                location_hint += f" It is currently in **{ch.category.name}**."
            return await interaction.followup.send(
                f"❌ Closed tickets cannot send macros. Reopen the ticket first.\n{location_hint}",
                ephemeral=True,
            )

        result = await send_ticket_macro(
            channel=ch,
            slug=clean_slug,
            actor=interaction.user,
        )
        if not result.get("ok"):
            return await interaction.followup.send(
                f"❌ {result.get('message') or 'Failed to send macro.'}",
                ephemeral=True,
            )

        try:
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        macro = result.get("macro") or {}
        macro_name = _safe_str(macro.get("name"), clean_slug)

        if _safe_bool(result.get("send_as_note"), False):
            msg = f"✅ Saved macro **{macro_name}** as an internal note in {ch.mention}."
        else:
            msg = f"✅ Sent macro **{macro_name}** in {ch.mention}."

        await interaction.followup.send(msg, ephemeral=True)
