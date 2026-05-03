from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from . import ticket_intake_admin as legacy


ticket_intake_group = app_commands.Group(
    name="ticket-intake",
    description="Ticket intake, category routing, and staff action panel tools.",
)


async def _staff_only(interaction: discord.Interaction) -> bool:
    if not _staff_check(interaction):
        await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        return False
    return True


async def _guild_only(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        return None
    return guild


def _legacy_config_int(name: str, default: int = 0) -> int:
    return legacy._safe_int(getattr(legacy, name, default), default)


def _category_summary_lines(rows: List[Dict[str, Any]]) -> List[str]:
    default_slug = legacy._find_default_category_slug(rows)
    verification_slug = legacy._find_verification_category_slug(rows)
    duplicate_slugs = legacy._duplicate_slugs(rows)
    default_count = len(legacy._default_categories(rows))

    summary_lines = [
        f"Configured categories: **{len(rows)}**",
        f"Default category: `{default_slug}`",
        f"Verification category: `{verification_slug}`",
    ]

    if duplicate_slugs:
        rendered = ", ".join([f"`{slug}`" for slug in duplicate_slugs[:6]])
        summary_lines.append(f"⚠️ Duplicate slugs: {rendered}")
    if default_count > 1:
        summary_lines.append(f"⚠️ Multiple defaults detected: `{default_count}`")
    if default_count == 0:
        summary_lines.append("ℹ️ No explicit default detected. Fallback behavior will use `support` when needed.")

    return summary_lines


@ticket_intake_group.command(
    name="post-panel",
    description="Post the public menu-first Create Ticket panel.",
)
@app_commands.describe(channel="Optional channel. Defaults to configured support/ticket-panel channel, then current channel.")
async def intake_post_panel(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    try:
        from .public_ticket_panel_command_guard import post_public_ticket_panel
    except Exception as e:
        return await reply_once(
            interaction,
            {"content": f"❌ Public ticket panel module unavailable: `{type(e).__name__}`", "ephemeral": True},
        )

    await post_public_ticket_panel(interaction, channel)


@ticket_intake_group.command(
    name="categories",
    description="Show configured dashboard ticket categories for this server.",
)
async def intake_categories(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    rows = await legacy._fetch_ticket_categories(guild.id)
    if not rows:
        embed = discord.Embed(
            title="🗂️ Ticket Categories",
            description="No dashboard ticket categories were found for this server.",
            color=discord.Color.blurple(),
            timestamp=legacy.now_utc(),
        )
        return await reply_once(interaction, {"embed": embed, "ephemeral": True})

    summary_lines = _category_summary_lines(rows)
    embeds: List[discord.Embed] = []
    chunk_size = 8
    total_pages = (len(rows) + chunk_size - 1) // chunk_size

    for page_index in range(total_pages):
        chunk = rows[page_index * chunk_size : (page_index + 1) * chunk_size]
        embed = discord.Embed(
            title="🗂️ Ticket Categories" if page_index == 0 else "🗂️ Ticket Categories (cont.)",
            description="\n".join(summary_lines) if page_index == 0 else None,
            color=discord.Color.blurple(),
            timestamp=legacy.now_utc(),
        )

        for row in chunk:
            slug = legacy._safe_str(row.get("slug"), "unknown")
            name = legacy._safe_str(row.get("name"), slug)
            intake_type = legacy._safe_str(row.get("intake_type"), "general")
            default_tag = " • DEFAULT" if bool(row.get("is_default")) else ""
            keywords = row.get("match_keywords") or []
            kw_text = ", ".join([f"`{legacy._truncate(k, 30)}`" for k in keywords[:6]]) if keywords else "—"
            desc = legacy._truncate(row.get("description") or "No description.", 180)
            value = (
                f"Type: `{intake_type}`{default_tag}\n"
                f"Keywords: {kw_text}\n"
                f"{desc}"
            )
            embed.add_field(name=f"{name} (`{slug}`)", value=value[:1024], inline=False)

        embed.set_footer(text=f"Page {page_index + 1}/{total_pages}")
        embeds.append(embed)

    await legacy._send_paginated_embeds(interaction, embeds)


@ticket_intake_group.command(
    name="match",
    description="Test which category a ticket reason would route into.",
)
@app_commands.describe(reason="Reason text to test against your ticket category routing")
async def intake_match(interaction: discord.Interaction, reason: str):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    clean_reason = legacy._normalize_text(reason, limit=600)
    if not clean_reason:
        return await reply_once(interaction, {"content": "❌ Reason cannot be empty.", "ephemeral": True})

    rows = await legacy._fetch_ticket_categories(guild.id)
    slug, label, score, why, top_matches = legacy._infer_category(rows, clean_reason)
    matched = legacy._find_category_by_slug(rows, slug)

    embed = discord.Embed(
        title="🧪 Ticket Category Match",
        color=discord.Color.green(),
        timestamp=legacy.now_utc(),
    )
    embed.add_field(name="Reason", value=legacy._truncate(clean_reason, 1024), inline=False)
    embed.add_field(name="Matched Category", value=f"**{label}**\n`{slug}`", inline=True)
    embed.add_field(name="Match Score", value=f"`{score}`", inline=True)
    embed.add_field(name="Default Category", value=f"`{legacy._find_default_category_slug(rows)}`", inline=True)
    embed.add_field(
        name="Plain-Language Explanation",
        value=(
            "This is the category the current routing logic would most likely choose.\n"
            "If the score is low or the explanation looks weak, your category keywords may need tuning."
        )[:1024],
        inline=False,
    )
    embed.add_field(name="Why It Matched", value=legacy._truncate(why, 1024), inline=False)

    if matched:
        keywords = matched.get("match_keywords") or []
        kw_text = ", ".join([f"`{legacy._truncate(k, 25)}`" for k in keywords[:8]]) if keywords else "—"
        embed.add_field(
            name="Matched Category Details",
            value=(
                f"Type: `{legacy._safe_str(matched.get('intake_type'), 'general')}`\n"
                f"Keywords: {kw_text}"
            )[:1024],
            inline=False,
        )

    if top_matches:
        lines = []
        for cat, cat_score in top_matches[:5]:
            lines.append(f"• `{legacy._safe_str(cat.get('slug'), 'unknown')}` — score `{cat_score}`")
        embed.add_field(name="Top Candidates", value="\n".join(lines)[:1024], inline=False)

    if not rows:
        embed.description = "No dashboard categories were found, so this would fall back to `support`."

    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@ticket_intake_group.command(
    name="status",
    description="Show ticket intake and routing status for this server.",
)
async def intake_status(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    rows = await legacy._fetch_ticket_categories(guild.id)
    default_slug = legacy._find_default_category_slug(rows)
    verification_slug = legacy._find_verification_category_slug(rows)

    ticket_parent_id = _legacy_config_int("TICKET_CATEGORY_ID", 0)
    ticket_parent = guild.get_channel(ticket_parent_id) if ticket_parent_id > 0 else None

    archive_parent = legacy._resolve_archive_category(guild)

    staff_role_id = _legacy_config_int("STAFF_ROLE_ID", 0)
    staff_role = guild.get_role(staff_role_id) if staff_role_id > 0 else None

    duplicate_slugs = legacy._duplicate_slugs(rows)
    default_count = len(legacy._default_categories(rows))

    embed = discord.Embed(
        title="📡 Ticket Intake Status",
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
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
    embed.add_field(name="Public Ticket Panel", value="`/ticket-panel post` or `/ticket-intake post-panel`", inline=True)
    embed.add_field(name="Actions Panel", value="`/ticket-intake post-actions`", inline=True)
    embed.add_field(
        name="Staff Note",
        value=(
            "Use `/ticket-panel post` to post the public **Create Ticket** button for users.\n"
            "Use `/ticket-intake post-actions` only inside an active ticket channel for staff controls."
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
            value="\n".join([f"• {legacy._truncate(w, 180)}" for w in warnings])[:1024],
            inline=False,
        )

    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@ticket_intake_group.command(
    name="preview",
    description="Preview a configured dashboard ticket category by slug.",
)
@app_commands.describe(slug="Category slug to inspect")
async def intake_preview(interaction: discord.Interaction, slug: str):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    rows = await legacy._fetch_ticket_categories(guild.id)
    row = legacy._find_category_by_slug(rows, slug)

    if row is None:
        return await reply_once(
            interaction,
            {"content": f"❌ No ticket category found for slug `{legacy._safe_str(slug)}`.", "ephemeral": True},
        )

    embed = discord.Embed(
        title="🔎 Ticket Category Preview",
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
    )
    embed.add_field(name="Name", value=legacy._safe_str(row.get("name"), "unknown"), inline=True)
    embed.add_field(name="Slug", value=f"`{legacy._safe_str(row.get('slug'), 'unknown')}`", inline=True)
    embed.add_field(name="Type", value=f"`{legacy._safe_str(row.get('intake_type'), 'general')}`", inline=True)
    embed.add_field(name="Default", value="Yes" if bool(row.get("is_default")) else "No", inline=True)
    embed.add_field(name="Sort Order", value=f"`{row.get('sort_order')}`", inline=True)
    embed.add_field(
        name="Description",
        value=legacy._truncate(row.get("description") or "No description.", 1024),
        inline=False,
    )

    keywords = row.get("match_keywords") or []
    embed.add_field(
        name="Keywords",
        value=", ".join([f"`{legacy._truncate(k, 40)}`" for k in keywords])[:1024] if keywords else "—",
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


@ticket_intake_group.command(
    name="post-actions",
    description="Post the staff ticket actions panel in an active ticket channel.",
)
@app_commands.describe(channel="Ticket channel to post the actions panel into. Leave empty to use current channel.")
async def intake_post_actions(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
):
    if not await _staff_only(interaction):
        return

    ch, row = await legacy._ticket_context_for_actions(interaction, channel)
    if ch is None:
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ This is the **staff actions** command, not the public Create Ticket button.\n"
                    "Use `/ticket-panel post` or `/ticket-intake post-panel` in your support channel to post the user-facing Create Ticket button."
                ),
                "ephemeral": True,
            },
        )

    if legacy.TicketChannelActionsView is None:
        return await reply_once(interaction, {"content": "❌ Ticket actions view is unavailable.", "ephemeral": True})

    status = legacy._ticket_status(row)
    lifecycle = legacy._ticket_lifecycle_label(ch, row)

    embed = discord.Embed(
        title="🛠️ Ticket Staff Actions",
        description=(
            "Use the buttons below for faster staff workflow.\n\n"
            "This panel is for **active tickets only**.\n"
            "It includes claim, unclaim, transfer, priority, notes, macros, and close."
        ),
        color=discord.Color.blurple(),
        timestamp=legacy.now_utc(),
    )
    embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
    embed.add_field(name="Status", value=f"`{status}`", inline=True)
    embed.add_field(name="Lifecycle", value=lifecycle, inline=True)
    embed.set_footer(text="stoney_verify:ticket_staff_actions:v1")

    try:
        await ch.send(embed=embed, view=legacy.TicketChannelActionsView())
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed posting actions panel: {e}", "ephemeral": True})

    await reply_once(interaction, {"content": f"✅ Posted staff actions panel in {ch.mention}.", "ephemeral": True})


def register_public_ticket_intake_group_commands(bot, tree) -> None:
    _ = bot
    existing = None
    try:
        existing = tree.get_command("ticket-intake", guild=None)
    except Exception:
        existing = None

    if existing is not None:
        try:
            print("ℹ️ public_ticket_intake_group: /ticket-intake already registered; skipping")
        except Exception:
            pass
        return

    tree.add_command(ticket_intake_group)
    try:
        print("✅ public_ticket_intake_group: registered /ticket-intake grouped command")
    except Exception:
        pass


__all__ = ["register_public_ticket_intake_group_commands", "ticket_intake_group"]
