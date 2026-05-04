from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import asyncio
import re

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from .common import _staff_check, reply_once

try:
    from ..tickets_new.panel import TicketChannelActionsView
except Exception:
    TicketChannelActionsView = None  # type: ignore

try:
    from ..tickets_new.repository import (
        get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
    )
except Exception:
    async def repo_get_ticket_by_any_channel_id(channel_id: int | str):  # type: ignore
        return None


# ============================================================
# ticket_intake_admin.py
# ------------------------------------------------------------
# Hardening goals:
# - deterministic category listing / default selection
# - transparent category routing preview with reasons
# - duplicate slug / multiple-default detection
# - only post staff action panels in real, active ticket channels
# - archive-aware ticket lifecycle checks (not just DB status)
# - clearer staff messaging for real-world moderation use
# ============================================================


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


def _truncate(text: Any, limit: int = 300) -> str:
    raw = _safe_str(text)
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _normalize_text(text: str, *, limit: int = 600) -> str:
    try:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        return cleaned[:limit]
    except Exception:
        return ""


def _slugify(value: str) -> str:
    try:
        return (
            str(value or "")
            .strip()
            .lower()
            .replace("'", "")
            .replace('"', "")
            .replace("&", " and ")
        )
    except Exception:
        return ""


def _tokenize_text(text: str) -> List[str]:
    cleaned = _slugify(text)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return [part for part in cleaned.split() if part]


def _normalize_keywords(value: Any) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = str(value or "").split(",")

        for item in raw_items:
            text = _normalize_text(str(item or ""), limit=120).lower()
            if text and text not in out:
                out.append(text)
    except Exception:
        pass
    return out


def _normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    slug = _normalize_text(str(row.get("slug") or ""), limit=120).lower()
    name = _normalize_text(str(row.get("name") or ""), limit=200)
    description = _normalize_text(str(row.get("description") or ""), limit=500)
    intake_type = _normalize_text(str(row.get("intake_type") or ""), limit=80).lower()

    return {
        "id": row.get("id"),
        "guild_id": str(row.get("guild_id") or ""),
        "slug": slug,
        "name": name,
        "description": description,
        "intake_type": intake_type,
        "match_keywords": _normalize_keywords(row.get("match_keywords")),
        "is_default": bool(row.get("is_default", False)),
        "sort_order": row.get("sort_order"),
    }


def _fetch_ticket_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    try:
        sb = get_supabase()
    except Exception:
        sb = None

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
    except Exception:
        return []

    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(_normalize_category_row(row))

    try:
        normalized.sort(
            key=lambda c: (
                c.get("sort_order") is None,
                c.get("sort_order") if c.get("sort_order") is not None else 10_000,
                str(c.get("name") or "").lower(),
                str(c.get("slug") or "").lower(),
            )
        )
    except Exception:
        pass

    return normalized


async def _fetch_ticket_categories(guild_id: int) -> List[Dict[str, Any]]:
    return await _run_blocking(_fetch_ticket_categories_sync, guild_id)


def _duplicate_slugs(categories: List[Dict[str, Any]]) -> List[str]:
    seen: Dict[str, int] = {}
    for cat in categories:
        slug = _safe_str(cat.get("slug")).lower()
        if not slug:
            continue
        seen[slug] = seen.get(slug, 0) + 1
    return sorted([slug for slug, count in seen.items() if count > 1])


def _default_categories(categories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [cat for cat in categories if bool(cat.get("is_default"))]


def _find_default_category_slug(categories: List[Dict[str, Any]]) -> str:
    defaults = _default_categories(categories)
    if defaults:
        return str(defaults[0].get("slug") or "support")

    for cat in categories:
        slug = str(cat.get("slug") or "")
        if slug in {"support", "general-support", "general_support"}:
            return slug

    return "support"


def _find_verification_category_slug(categories: List[Dict[str, Any]]) -> str:
    for cat in categories:
        slug = str(cat.get("slug") or "")
        name = str(cat.get("name") or "").lower()
        intake_type = str(cat.get("intake_type") or "").lower()
        if intake_type == "verification":
            return slug or "verification_issue"
        if slug in {"verification", "verification-issue", "verification_issue"}:
            return slug
        if "verification" in name:
            return slug or "verification_issue"
    return "verification_issue"


def _find_category_by_slug(categories: List[Dict[str, Any]], slug: str) -> Optional[Dict[str, Any]]:
    slug_clean = _safe_str(slug).lower()
    for cat in categories:
        if _safe_str(cat.get("slug")).lower() == slug_clean:
            return cat
    return None


def _score_reason_against_category(reason: str, cat: Dict[str, Any]) -> Tuple[int, List[str]]:
    reason_norm = _normalize_text(reason, limit=600).lower()
    reason_tokens = set(_tokenize_text(reason_norm))

    slug = str(cat.get("slug") or "").lower()
    name = str(cat.get("name") or "").lower()
    desc = str(cat.get("description") or "").lower()
    keywords = [str(x).lower() for x in (cat.get("match_keywords") or [])]

    score = 0
    reasons: List[str] = []

    for kw in keywords:
        kw_clean = _normalize_text(kw, limit=120).lower()
        if not kw_clean:
            continue
        if kw_clean in reason_norm:
            score += 25
            reasons.append(f"keyword match: `{kw_clean}` (+25)")
            if len(kw_clean.split()) > 1:
                score += 10
                reasons.append(f"multi-word keyword bonus: `{kw_clean}` (+10)")

    slug_words = [w for w in re.split(r"[-_\s]+", slug) if w]
    name_words = [w for w in _tokenize_text(name)]
    desc_words = [w for w in _tokenize_text(desc)]

    for word in slug_words:
        if len(word) >= 3 and word in reason_tokens:
            score += 6
            reasons.append(f"slug token: `{word}` (+6)")

    for word in name_words:
        if len(word) >= 3 and word in reason_tokens:
            score += 5
            reasons.append(f"name token: `{word}` (+5)")

    for word in desc_words[:25]:
        if len(word) >= 4 and word in reason_tokens:
            score += 2
            reasons.append(f"description token: `{word}` (+2)")

    intake_type = str(cat.get("intake_type") or "").lower()
    if intake_type == "appeal" and any(x in reason_norm for x in ["appeal", "unban", "timeout", "ban", "muted", "banned"]):
        score += 6
        reasons.append("appeal intent bonus (+6)")
    elif intake_type == "report" and any(x in reason_norm for x in ["report", "scam", "abuse", "harassment", "threat"]):
        score += 6
        reasons.append("report intent bonus (+6)")
    elif intake_type == "partnership" and any(x in reason_norm for x in ["partner", "partnership", "collab", "promo", "sponsor"]):
        score += 6
        reasons.append("partnership intent bonus (+6)")
    elif intake_type == "question" and any(x in reason_norm for x in ["question", "help", "how do i", "how to"]):
        score += 4
        reasons.append("question intent bonus (+4)")
    elif intake_type == "verification" and any(x in reason_norm for x in ["verify", "verification", "id", "identity", "vc"]):
        score += 6
        reasons.append("verification intent bonus (+6)")

    return score, reasons


def _infer_category(categories: List[Dict[str, Any]], reason: str) -> Tuple[str, str, int, str, List[Tuple[Dict[str, Any], int]]]:
    if not categories:
        return "support", "Support", 0, "No dashboard categories found. Falling back to `support`.", []

    scored: List[Tuple[Dict[str, Any], int]] = []
    best: Optional[Dict[str, Any]] = None
    best_score = 0
    best_reasons: List[str] = []

    for cat in categories:
        score, reasons = _score_reason_against_category(reason, cat)
        scored.append((cat, score))
        if score > best_score:
            best = cat
            best_score = score
            best_reasons = reasons

    scored.sort(key=lambda item: item[1], reverse=True)

    if best is not None and best_score > 0:
        why = "; ".join(best_reasons[:6]) if best_reasons else "best score won"
        return (
            _safe_str(best.get("slug"), "support"),
            _safe_str(best.get("name"), "Support"),
            best_score,
            why,
            scored[:5],
        )

    default_slug = _find_default_category_slug(categories)
    default_cat = _find_category_by_slug(categories, default_slug)
    if default_cat is not None:
        return (
            _safe_str(default_cat.get("slug"), "support"),
            _safe_str(default_cat.get("name"), "Support"),
            0,
            f"No positive keyword/intent match. Falling back to default category `{default_slug}`.",
            scored[:5],
        )

    return "support", "Support", 0, "No positive match. Falling back to `support`.", scored[:5]


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("status"), "unknown").lower()
        if raw in {"open", "claimed", "closed", "deleted"}:
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


def _resolve_category_by_id(
    guild: discord.Guild,
    category_id: int,
) -> Optional[discord.CategoryChannel]:
    try:
        if category_id <= 0:
            return None
        maybe = guild.get_channel(int(category_id))
        if isinstance(maybe, discord.CategoryChannel):
            return maybe
    except Exception:
        pass
    return None


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


def _channel_is_in_archive_category(channel: discord.TextChannel) -> bool:
    try:
        category = channel.category
        if not isinstance(category, discord.CategoryChannel):
            return False

        archive = _resolve_archive_category(channel.guild)
        if archive is not None and int(category.id) == int(archive.id):
            return True

        return _looks_like_archive_category_name(category.name)
    except Exception:
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


def _ticket_lifecycle_label(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> str:
    if _ticket_effectively_closed(channel=channel, row=row):
        if _channel_is_in_archive_category(channel):
            return f"Closed / archived in **{channel.category.name if channel.category else 'archive'}**"
        return "Closed"
    return "Active"


async def _send_paginated_embeds(
    interaction: discord.Interaction,
    embeds: List[discord.Embed],
) -> None:
    if not embeds:
        return

    await reply_once(interaction, {"embed": embeds[0], "ephemeral": True})
    for embed in embeds[1:]:
        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            break


async def _ticket_context_for_actions(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> Tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await reply_once(interaction, {"content": "❌ Must be used in a ticket text channel.", "ephemeral": True})
        return None, None

    try:
        row = await repo_get_ticket_by_any_channel_id(int(ch.id))
        row = dict(row) if isinstance(row, dict) else None
    except Exception:
        row = None

    if row is None:
        await reply_once(
            interaction,
            {
                "content": (
                    f"❌ `{ch.name}` is not a tracked ticket channel.\n"
                    "Use this only inside a real ticket."
                ),
                "ephemeral": True,
            },
        )
        return None, None

    status = _ticket_status(row)
    if status == "deleted":
        await reply_once(
            interaction,
            {"content": "❌ You cannot post a staff actions panel into a deleted ticket.", "ephemeral": True},
        )
        return None, None

    if _ticket_effectively_closed(channel=ch, row=row):
        await reply_once(
            interaction,
            {
                "content": (
                    "❌ This ticket is closed or archived.\n"
                    "Do not post the active staff actions panel here.\n"
                    "Use the closed-ticket controls for closed tickets."
                ),
                "ephemeral": True,
            },
        )
        return None, None

    return ch, row


def register_ticket_intake_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_categories",
        description="Show the configured dashboard ticket categories for this server.",
    )
    async def ticket_categories(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await _fetch_ticket_categories(guild.id)

        if not rows:
            embed = discord.Embed(
                title="🗂️ Ticket Categories",
                description="No dashboard ticket categories were found for this server.",
                color=discord.Color.blurple(),
                timestamp=now_utc(),
            )
            return await reply_once(interaction, {"embed": embed, "ephemeral": True})

        default_slug = _find_default_category_slug(rows)
        verification_slug = _find_verification_category_slug(rows)
        duplicate_slugs = _duplicate_slugs(rows)
        default_count = len(_default_categories(rows))

        summary_lines = [
            f"Configured categories: **{len(rows)}**",
            f"Default category: `{default_slug}`",
            f"Verification category: `{verification_slug}`",
        ]
        if duplicate_slugs:
            summary_lines.append(
                f"⚠️ Duplicate slugs: {', '.join([f'`{s}`' for s in duplicate_slugs[:6]])}"
            )
        if default_count > 1:
            summary_lines.append(f"⚠️ Multiple defaults detected: `{default_count}`")
        if default_count == 0:
            summary_lines.append("ℹ️ No explicit default detected. Fallback behavior will use `support` when needed.")

        embeds: List[discord.Embed] = []
        chunk_size = 8
        total_pages = (len(rows) + chunk_size - 1) // chunk_size

        for page_index in range(total_pages):
            chunk = rows[page_index * chunk_size : (page_index + 1) * chunk_size]
            embed = discord.Embed(
                title="🗂️ Ticket Categories" if page_index == 0 else "🗂️ Ticket Categories (cont.)",
                description="\n".join(summary_lines) if page_index == 0 else None,
                color=discord.Color.blurple(),
                timestamp=now_utc(),
            )

            for row in chunk:
                slug = _safe_str(row.get("slug"), "unknown")
                name = _safe_str(row.get("name"), slug)
                intake_type = _safe_str(row.get("intake_type"), "general")
                default_tag = " • DEFAULT" if bool(row.get("is_default")) else ""
                keywords = row.get("match_keywords") or []
                kw_text = ", ".join([f"`{_truncate(k, 30)}`" for k in keywords[:6]]) if keywords else "—"
                desc = _truncate(row.get("description") or "No description.", 180)
                value = (
                    f"Type: `{intake_type}`{default_tag}\n"
                    f"Keywords: {kw_text}\n"
                    f"{desc}"
                )
                embed.add_field(name=f"{name} (`{slug}`)", value=value[:1024], inline=False)

            embed.set_footer(text=f"Page {page_index + 1}/{total_pages}")
            embeds.append(embed)

        await _send_paginated_embeds(interaction, embeds)

    @tree.command(
        name="ticket_category_match",
        description="Test which dashboard ticket category a reason would route into.",
    )
    @app_commands.describe(reason="Reason text to test against your ticket category routing")
    async def ticket_category_match(interaction: discord.Interaction, reason: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        clean_reason = _normalize_text(reason, limit=600)
        if not clean_reason:
            return await reply_once(interaction, {"content": "❌ Reason cannot be empty.", "ephemeral": True})

        rows = await _fetch_ticket_categories(guild.id)
        slug, label, score, why, top_matches = _infer_category(rows, clean_reason)
        matched = _find_category_by_slug(rows, slug)

        embed = discord.Embed(
            title="🧪 Ticket Category Match",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Reason", value=_truncate(clean_reason, 1024), inline=False)
        embed.add_field(name="Matched Category", value=f"**{label}**\n`{slug}`", inline=True)
        embed.add_field(name="Match Score", value=f"`{score}`", inline=True)
        embed.add_field(name="Default Category", value=f"`{_find_default_category_slug(rows)}`", inline=True)
        embed.add_field(
            name="Plain-Language Explanation",
            value=(
                "This is the category the current routing logic would most likely choose.\n"
                "If the score is low or the explanation looks weak, your category keywords may need tuning."
            )[:1024],
            inline=False,
        )
        embed.add_field(name="Why It Matched", value=_truncate(why, 1024), inline=False)

        if matched:
            keywords = matched.get("match_keywords") or []
            embed.add_field(
                name="Matched Category Details",
                value=(
                    f"Type: `{_safe_str(matched.get('intake_type'), 'general')}`\n"
                    f"Keywords: {', '.join([f'`{_truncate(k, 25)}`' for k in keywords[:8]]) if keywords else '—'}"
                )[:1024],
                inline=False,
            )

        if top_matches:
            lines = []
            for cat, cat_score in top_matches[:5]:
                lines.append(
                    f"• `{_safe_str(cat.get('slug'), 'unknown')}` — score `{cat_score}`"
                )
            embed.add_field(name="Top Candidates", value="\n".join(lines)[:1024], inline=False)

        if not rows:
            embed.description = "No dashboard categories were found, so this would fall back to `support`."

        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_intake_status",
        description="Show intake and routing status for this server.",
    )
    async def ticket_intake_status(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await _fetch_ticket_categories(guild.id)
        default_slug = _find_default_category_slug(rows)
        verification_slug = _find_verification_category_slug(rows)

        ticket_parent_id = _safe_int(globals().get("TICKET_CATEGORY_ID"), 0)
        ticket_parent = guild.get_channel(ticket_parent_id) if ticket_parent_id > 0 else None

        archive_parent = _resolve_archive_category(guild)

        staff_role_id = _safe_int(globals().get("STAFF_ROLE_ID"), 0)
        staff_role = guild.get_role(staff_role_id) if staff_role_id > 0 else None

        duplicate_slugs = _duplicate_slugs(rows)
        default_count = len(_default_categories(rows))

        embed = discord.Embed(
            title="📡 Ticket Intake Status",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Dashboard Categories", value=f"`{len(rows)}` configured", inline=True)
        embed.add_field(name="Default Category", value=f"`{default_slug}`", inline=True)
        embed.add_field(name="Verification Category", value=f"`{verification_slug}`", inline=True)
        embed.add_field(
            name="Active Ticket Category",
            value=(f"{ticket_parent.mention}\n`{ticket_parent.id}`" if ticket_parent else "Not configured / not found"),
            inline=False,
        )
        embed.add_field(
            name="Archive Ticket Category",
            value=(f"{archive_parent.mention}\n`{archive_parent.id}`" if archive_parent else "Not configured / not found"),
            inline=False,
        )
        embed.add_field(
            name="Staff Role",
            value=(f"{staff_role.mention}\n`{staff_role.id}`" if staff_role else "Not configured / not found"),
            inline=False,
        )
        embed.add_field(name="Public Ticket Panel Command", value="`/post_ticket_panel`", inline=True)
        embed.add_field(name="Ghost Ticket Panel Command", value="`/post_ghost_ticket_panel`", inline=True)
        embed.add_field(name="Ticket Actions Panel Command", value="`/ticket_post_actions`", inline=True)
        embed.add_field(
            name="Staff Note",
            value=(
                "Use `/ticket_post_actions` only in active tickets.\n"
                "Closed or archived tickets should use the closed-ticket controls instead."
            )[:1024],
            inline=False,
        )

        warnings: List[str] = []
        if not rows:
            warnings.append("No dashboard ticket categories found.")
        if duplicate_slugs:
            warnings.append(f"Duplicate slugs detected: {', '.join(duplicate_slugs[:8])}")
        if default_count > 1:
            warnings.append(f"Multiple default categories detected: {default_count}")
        if ticket_parent is None:
            warnings.append("Active ticket category is missing or not reachable.")
        if staff_role is None:
            warnings.append("Staff role is missing or not reachable.")
        if archive_parent is None:
            warnings.append("Archive ticket category is not configured or not detectable.")

        if warnings:
            embed.add_field(
                name="Warnings",
                value="\n".join([f"• {_truncate(w, 180)}" for w in warnings])[:1024],
                inline=False,
            )

        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_category_preview",
        description="Preview one configured dashboard ticket category by slug.",
    )
    @app_commands.describe(slug="Category slug to inspect")
    async def ticket_category_preview(interaction: discord.Interaction, slug: str):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        guild = interaction.guild
        if guild is None:
            return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

        rows = await _fetch_ticket_categories(guild.id)
        row = _find_category_by_slug(rows, slug)

        if row is None:
            return await reply_once(
                interaction,
                {"content": f"❌ No ticket category found for slug `{_safe_str(slug)}`.", "ephemeral": True},
            )

        embed = discord.Embed(
            title="🔎 Ticket Category Preview",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Name", value=_safe_str(row.get("name"), "unknown"), inline=True)
        embed.add_field(name="Slug", value=f"`{_safe_str(row.get('slug'), 'unknown')}`", inline=True)
        embed.add_field(name="Type", value=f"`{_safe_str(row.get('intake_type'), 'general')}`", inline=True)
        embed.add_field(name="Default", value="Yes" if bool(row.get("is_default")) else "No", inline=True)
        embed.add_field(name="Sort Order", value=f"`{row.get('sort_order')}`", inline=True)
        embed.add_field(
            name="Description",
            value=_truncate(row.get("description") or "No description.", 1024),
            inline=False,
        )

        keywords = row.get("match_keywords") or []
        embed.add_field(
            name="Keywords",
            value=((", ".join([f"`{_truncate(k, 40)}`" for k in keywords])) if keywords else "—")[:1024],
            inline=False,
        )
        embed.add_field(
            name="Plain-Language Note",
            value=(
                "This category can be selected by direct user choice or by automatic routing.\n"
                "If it is not matching correctly, adjust its keywords and description."
            )[:1024],
            inline=False,
        )

        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_post_actions",
        description="Post the staff ticket actions panel in the current ticket channel.",
    )
    @app_commands.describe(channel="Ticket channel to post the actions panel into (leave empty to use current channel)")
    async def ticket_post_actions(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ticket_context_for_actions(interaction, channel)
        if ch is None:
            return

        if TicketChannelActionsView is None:
            return await reply_once(interaction, {"content": "❌ Ticket actions view is unavailable.", "ephemeral": True})

        status = _ticket_status(row)
        lifecycle = _ticket_lifecycle_label(ch, row)

        embed = discord.Embed(
            title="🛠️ Ticket Staff Actions",
            description=(
                "Use the buttons below for faster staff workflow.\n\n"
                "This panel is for **active tickets only**.\n"
                "It includes claim, unclaim, transfer, priority, notes, macros, and close."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
        embed.add_field(name="Status", value=f"`{status}`", inline=True)
        embed.add_field(name="Lifecycle", value=lifecycle[:100], inline=True)
        embed.add_field(name="Category", value=f"`{_safe_str((row or {}).get('category'), 'unknown')}`", inline=True)
        embed.add_field(
            name="Staff Note",
            value=(
                "If this ticket later gets closed or archived, do **not** keep using this panel.\n"
                "Use the closed-ticket controls instead."
            )[:1024],
            inline=False,
        )
        embed.set_footer(text="Dank Shield Ticket System")

        try:
            await ch.send(
                embed=embed,
                view=TicketChannelActionsView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as e:
            return await reply_once(
                interaction,
                {"content": f"❌ Failed to post ticket actions panel: {e}", "ephemeral": True},
            )

        try:
            from .common import mark_ticket_activity
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        await reply_once(interaction, {"content": f"✅ Posted ticket actions panel in {ch.mention}.", "ephemeral": True})
