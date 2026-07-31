from __future__ import annotations

from typing import Any, Dict, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from . import ticket_category_admin as legacy
from ..tickets_new.managed_category_service import (
    ManagedCategorySyncError,
    summarize_sync,
    sync_managed_categories,
)


ticket_category_group = app_commands.Group(
    name="ticket-category",
    description="Manage ticket categories, routing keywords, and defaults.",
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


def _is_managed_category(row: Dict[str, Any]) -> bool:
    return bool(row.get("managed_by_dank")) or bool(legacy._safe_str(row.get("managed_category_key")))


async def _block_managed_mutation(
    interaction: discord.Interaction,
    row: Dict[str, Any],
    *,
    action: str,
) -> bool:
    if not _is_managed_category(row):
        return False

    name = legacy._safe_str(row.get("name"), "This category")
    key = legacy._safe_str(row.get("managed_category_key"), legacy._safe_str(row.get("slug"), "managed"))
    await reply_once(
        interaction,
        {
            "content": (
                f"🔒 **{name}** is managed by the global Dank Shield catalog and cannot be {action} locally.\n\n"
                f"Managed key: `{key}`\n"
                "Global category improvements automatically reach every server. "
                "Create a new category with `/ticket-category create` for server-specific routing; custom categories stay untouched."
            ),
            "ephemeral": True,
        },
    )
    return True


def _add_governance_warnings(embed: discord.Embed, rows: list[Dict[str, Any]]) -> discord.Embed:
    try:
        warnings = legacy._governance_warnings(rows)
        if warnings:
            embed.add_field(
                name="Governance Warnings",
                value="\n".join([f"• {w}" for w in warnings])[:1024],
                inline=False,
            )
    except Exception:
        pass
    return embed


@ticket_category_group.command(
    name="sync",
    description="Restore and update this server's global Dank Shield categories.",
)
async def category_sync(interaction: discord.Interaction):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    try:
        rows = await sync_managed_categories(guild.id)
    except ManagedCategorySyncError as exc:
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ Managed ticket categories could not be synchronized. Nothing was changed.\n"
                    f"`{type(exc).__name__}: {str(exc)[:500]}`"
                ),
                "ephemeral": True,
            },
        )

    summary = summarize_sync(rows)
    categories = await legacy._fetch_categories(guild.id)
    managed_count = sum(1 for row in categories if _is_managed_category(row))
    custom_count = max(0, len(categories) - managed_count)

    await reply_once(
        interaction,
        {
            "content": (
                "✅ **Ticket category catalog synchronized.**\n"
                f"• Added missing managed categories: `{summary['inserted']}`\n"
                f"• Updated managed categories: `{summary['updated']}`\n"
                f"• Removed managed duplicates: `{summary['deleted_duplicates']}`\n"
                f"• Current managed/custom total: `{managed_count}` / `{custom_count}`\n\n"
                "Custom server categories were preserved."
            ),
            "ephemeral": True,
        },
    )


@ticket_category_group.command(
    name="create",
    description="Create a server-specific custom ticket category.",
)
@app_commands.describe(
    name="Display name for the category.",
    slug="URL-style slug, like billing-help or clan-application.",
    intake_type="Type used by routing logic.",
    description="Optional category description.",
    keywords="Comma-separated match keywords.",
    is_default="Whether this should become the default category.",
    sort_order="Optional sort order. Lower numbers appear first.",
)
async def category_create(
    interaction: discord.Interaction,
    name: str,
    slug: str,
    intake_type: str,
    description: Optional[str] = None,
    keywords: Optional[str] = None,
    is_default: Optional[bool] = False,
    sort_order: Optional[int] = None,
):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    slug_clean = legacy._slugify(slug)
    if not slug_clean:
        return await reply_once(interaction, {"content": "❌ Invalid slug.", "ephemeral": True})

    name_clean = legacy._normalize_name(name)
    if not name_clean:
        return await reply_once(interaction, {"content": "❌ Category name cannot be empty.", "ephemeral": True})

    type_clean = legacy._safe_str(intake_type, "general").lower()
    if type_clean not in legacy._ALLOWED_INTAKE_TYPES:
        return await reply_once(
            interaction,
            {"content": f"❌ Invalid intake type. Use one of: {legacy._human_intake_types()}", "ephemeral": True},
        )

    sort_clean = legacy._validated_sort_order(sort_order)
    if sort_order is not None and sort_clean is None:
        return await reply_once(
            interaction,
            {
                "content": f"❌ Sort order must be between `{legacy._MIN_SORT_ORDER}` and `{legacy._MAX_SORT_ORDER}`.",
                "ephemeral": True,
            },
        )

    existing = await legacy._fetch_category_by_slug(guild.id, slug_clean)
    if existing:
        ownership = "managed" if _is_managed_category(existing) else "custom"
        return await reply_once(
            interaction,
            {
                "content": f"❌ Category `{slug_clean}` already exists and is `{ownership}`.",
                "ephemeral": True,
            },
        )

    payload = {
        "guild_id": str(guild.id),
        "slug": slug_clean,
        "name": name_clean,
        "description": legacy._normalize_description(description),
        "intake_type": type_clean,
        "match_keywords": legacy._normalize_keywords(legacy._safe_str(keywords)),
        "is_default": bool(is_default),
        "sort_order": sort_clean,
        "managed_by_dank": False,
        "managed_catalog_version": None,
        "managed_category_key": None,
    }

    ok = await legacy._insert_category(payload)
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to create ticket category.", "ephemeral": True})

    if bool(is_default):
        await legacy._set_default(guild.id, slug_clean)

    created = await legacy._fetch_category_by_slug(guild.id, slug_clean) or payload
    embed = legacy._category_embed("✅ Custom Ticket Category Created", created)
    rows = await legacy._fetch_categories(guild.id)
    _add_governance_warnings(embed, rows)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@ticket_category_group.command(
    name="edit",
    description="Edit an existing custom ticket category.",
)
@app_commands.describe(
    slug="Existing custom category slug to edit.",
    name="New display name.",
    intake_type="New intake type.",
    description="New description.",
    keywords="New comma-separated keywords.",
    is_default="Set or unset as default.",
    sort_order="New sort order.",
)
async def category_edit(
    interaction: discord.Interaction,
    slug: str,
    name: Optional[str] = None,
    intake_type: Optional[str] = None,
    description: Optional[str] = None,
    keywords: Optional[str] = None,
    is_default: Optional[bool] = None,
    sort_order: Optional[int] = None,
):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    slug_clean = legacy._slugify(slug)
    row = await legacy._fetch_category_by_slug(guild.id, slug_clean)
    if not row:
        return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})
    if await _block_managed_mutation(interaction, row, action="edited"):
        return

    patch: Dict[str, Any] = {}

    if name is not None:
        name_clean = legacy._normalize_name(name)
        if not name_clean:
            return await reply_once(interaction, {"content": "❌ Category name cannot be empty.", "ephemeral": True})
        patch["name"] = name_clean

    if intake_type is not None:
        type_clean = legacy._safe_str(intake_type, "general").lower()
        if type_clean not in legacy._ALLOWED_INTAKE_TYPES:
            return await reply_once(
                interaction,
                {"content": f"❌ Invalid intake type. Use one of: {legacy._human_intake_types()}", "ephemeral": True},
            )
        patch["intake_type"] = type_clean

    if description is not None:
        patch["description"] = legacy._normalize_description(description)

    if keywords is not None:
        patch["match_keywords"] = legacy._normalize_keywords(legacy._safe_str(keywords))

    if sort_order is not None:
        sort_clean = legacy._validated_sort_order(sort_order)
        if sort_clean is None:
            return await reply_once(
                interaction,
                {
                    "content": f"❌ Sort order must be between `{legacy._MIN_SORT_ORDER}` and `{legacy._MAX_SORT_ORDER}`.",
                    "ephemeral": True,
                },
            )
        patch["sort_order"] = sort_clean

    if is_default is not None:
        patch["is_default"] = bool(is_default)

    if not patch:
        return await reply_once(interaction, {"content": "ℹ️ Nothing to update.", "ephemeral": True})

    ok = await legacy._update_category(guild.id, slug_clean, patch)
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to update ticket category.", "ephemeral": True})

    if is_default is True:
        await legacy._set_default(guild.id, slug_clean)
    elif is_default is False and bool(row.get("is_default")):
        rows_after = await legacy._fetch_categories(guild.id)
        replacement = legacy._choose_replacement_default(rows_after, slug_clean)
        if replacement is not None:
            await legacy._set_default(guild.id, legacy._safe_str(replacement.get("slug")))
        else:
            await legacy._set_default(guild.id, slug_clean)

    updated = await legacy._fetch_category_by_slug(guild.id, slug_clean) or {**row, **patch}
    embed = legacy._category_embed("✅ Custom Ticket Category Updated", updated)
    rows = await legacy._fetch_categories(guild.id)
    _add_governance_warnings(embed, rows)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@ticket_category_group.command(
    name="delete",
    description="Delete a custom ticket category safely.",
)
@app_commands.describe(slug="Custom category slug to delete.")
async def category_delete(interaction: discord.Interaction, slug: str):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    slug_clean = legacy._slugify(slug)
    row = await legacy._fetch_category_by_slug(guild.id, slug_clean)
    if not row:
        return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})
    if await _block_managed_mutation(interaction, row, action="deleted"):
        return

    rows_before = await legacy._fetch_categories(guild.id)
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

    verification_rows = legacy._verification_like_categories(rows_before)
    deleting_verification_like = any(
        legacy._safe_str(x.get("slug")).lower() == slug_clean for x in verification_rows
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
        replacement_default = legacy._choose_replacement_default(rows_before, slug_clean)

    ok = await legacy._delete_category(guild.id, slug_clean)
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to delete ticket category.", "ephemeral": True})

    auto_msg = ""
    if replacement_default is not None:
        replacement_slug = legacy._safe_str(replacement_default.get("slug"))
        if replacement_slug:
            await legacy._set_default(guild.id, replacement_slug)
            auto_msg = f"\n⭐ Auto-promoted `{replacement_slug}` as the new default category."

    await reply_once(
        interaction,
        {"content": f"✅ Deleted custom ticket category `{slug_clean}`.{auto_msg}", "ephemeral": True},
    )


@ticket_category_group.command(
    name="set-default",
    description="Set a custom category as this server's local default.",
)
@app_commands.describe(slug="Custom category slug to make default.")
async def category_set_default(interaction: discord.Interaction, slug: str):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    slug_clean = legacy._slugify(slug)
    row = await legacy._fetch_category_by_slug(guild.id, slug_clean)
    if not row:
        return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})
    if await _block_managed_mutation(interaction, row, action="made the local default"):
        return

    ok = await legacy._set_default(guild.id, slug_clean)
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to set default category.", "ephemeral": True})

    updated = await legacy._fetch_category_by_slug(guild.id, slug_clean) or {**row, "is_default": True}
    embed = legacy._category_embed("⭐ Custom Default Ticket Category Updated", updated)
    rows = await legacy._fetch_categories(guild.id)
    _add_governance_warnings(embed, rows)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@ticket_category_group.command(
    name="reorder",
    description="Set sort order for a custom ticket category.",
)
@app_commands.describe(
    slug="Custom category slug to reorder.",
    sort_order="Lower numbers appear first.",
)
async def category_reorder(interaction: discord.Interaction, slug: str, sort_order: int):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    slug_clean = legacy._slugify(slug)
    row = await legacy._fetch_category_by_slug(guild.id, slug_clean)
    if not row:
        return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})
    if await _block_managed_mutation(interaction, row, action="reordered"):
        return

    sort_clean = legacy._validated_sort_order(sort_order)
    if sort_clean is None:
        return await reply_once(
            interaction,
            {
                "content": f"❌ Sort order must be between `{legacy._MIN_SORT_ORDER}` and `{legacy._MAX_SORT_ORDER}`.",
                "ephemeral": True,
            },
        )

    ok = await legacy._update_category(guild.id, slug_clean, {"sort_order": sort_clean})
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to reorder ticket category.", "ephemeral": True})

    updated = await legacy._fetch_category_by_slug(guild.id, slug_clean) or {**row, "sort_order": sort_clean}
    embed = legacy._category_embed("↕️ Custom Ticket Category Reordered", updated)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@ticket_category_group.command(
    name="keywords",
    description="Replace the keyword list for a custom ticket category.",
)
@app_commands.describe(
    slug="Custom category slug to update.",
    keywords="Comma-separated keywords to use for routing.",
)
async def category_keywords(interaction: discord.Interaction, slug: str, keywords: str):
    if not await _staff_only(interaction):
        return

    guild = await _guild_only(interaction)
    if guild is None:
        return

    slug_clean = legacy._slugify(slug)
    row = await legacy._fetch_category_by_slug(guild.id, slug_clean)
    if not row:
        return await reply_once(interaction, {"content": f"❌ Category `{slug_clean}` was not found.", "ephemeral": True})
    if await _block_managed_mutation(interaction, row, action="given local keywords"):
        return

    kw = legacy._normalize_keywords(keywords)
    ok = await legacy._update_category(guild.id, slug_clean, {"match_keywords": kw})
    if not ok:
        return await reply_once(interaction, {"content": "❌ Failed to update category keywords.", "ephemeral": True})

    updated = await legacy._fetch_category_by_slug(guild.id, slug_clean) or {**row, "match_keywords": kw}
    embed = legacy._category_embed("🏷️ Custom Ticket Category Keywords Updated", updated)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


def register_public_ticket_category_group_commands(bot, tree) -> None:
    _ = bot
    existing = None
    try:
        existing = tree.get_command("ticket-category", guild=None)
    except Exception:
        existing = None

    if existing is not None:
        try:
            print("ℹ️ public_ticket_category_group: /ticket-category already registered; skipping")
        except Exception:
            pass
        return

    tree.add_command(ticket_category_group)
    try:
        print("✅ public_ticket_category_group: registered /ticket-category grouped command")
    except Exception:
        pass


__all__ = ["register_public_ticket_category_group_commands", "ticket_category_group"]
