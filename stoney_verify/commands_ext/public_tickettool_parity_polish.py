from __future__ import annotations

"""Small public TicketTool parity polish layer.

This module is not a root runtime patch. It attaches missing public command aliases,
registers the public Create Ticket panel commands through the normal command
surface, and tightens the parity checker so it cannot pass when the real user
panel command is missing.
"""

from typing import Any, Dict, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from . import ticket_category_admin as legacy
from . import ticket_admin as legacy_ticket_admin
from . import public_ticket_category_group as category_group_module
from .public_ticket_category_group import ticket_category_group, _add_governance_warnings

_ATTACHED = False
_CHECKER_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"✅ public_tickettool_parity_polish: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_tickettool_parity_polish: {message}")
    except Exception:
        pass


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


def _row_value(row: Dict[str, Any]) -> str:
    slug = legacy._safe_str(row.get("slug"), "unknown")
    intake_type = legacy._safe_str(row.get("intake_type"), "general")
    is_default = bool(row.get("is_default"))
    sort_order = row.get("sort_order")
    keywords = row.get("match_keywords") or []
    keyword_count = len(keywords) if isinstance(keywords, list) else 0
    default_text = " ⭐ **default**" if is_default else ""
    return (
        f"Slug: `{slug}`{default_text}\n"
        f"Type: `{intake_type}` • Sort: `{sort_order}` • Keywords: `{keyword_count}`"
    )[:1024]


async def _category_list_callback(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_only(interaction)
    if guild is None:
        return

    rows = await legacy._fetch_categories(guild.id)
    if not rows:
        return await reply_once(
            interaction,
            {
                "content": "ℹ️ No ticket categories are configured yet. Use `/ticket-category create` or `/stoney setup-defaults`.",
                "ephemeral": True,
            },
        )

    embed = discord.Embed(
        title="🎫 Ticket Categories",
        description=f"{len(rows)} configured category/categories for this server.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    for index, row in enumerate(rows[:25], start=1):
        name = legacy._safe_str(row.get("name"), "Unnamed Category")
        embed.add_field(
            name=f"{index}. {name}"[:256],
            value=_row_value(row),
            inline=False,
        )

    if len(rows) > 25:
        embed.add_field(
            name="More categories",
            value=f"Showing 25 of {len(rows)} categories.",
            inline=False,
        )

    _add_governance_warnings(embed, rows)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


async def _category_update_callback(
    interaction: discord.Interaction,
    slug: str,
    name: Optional[str] = None,
    intake_type: Optional[str] = None,
    description: Optional[str] = None,
    keywords: Optional[str] = None,
    is_default: Optional[bool] = None,
    sort_order: Optional[int] = None,
) -> None:
    """TicketTool-friendly alias for /ticket-category edit."""
    target = getattr(category_group_module, "category_edit", None)
    callback = getattr(target, "callback", None) or target
    if not callable(callback):
        return await reply_once(
            interaction,
            {"content": "❌ Category edit handler is unavailable.", "ephemeral": True},
        )

    await callback(
        interaction,
        slug,
        name=name,
        intake_type=intake_type,
        description=description,
        keywords=keywords,
        is_default=is_default,
        sort_order=sort_order,
    )


_category_list_callback = app_commands.describe()(  # type: ignore[assignment]
    _category_list_callback
)
_category_update_callback = app_commands.describe(  # type: ignore[assignment]
    slug="Existing category slug to update.",
    name="New display name.",
    intake_type="New intake type.",
    description="New description.",
    keywords="New comma-separated keywords.",
    is_default="Set or unset as default.",
    sort_order="New sort order.",
)(_category_update_callback)


def _ensure_command(name: str, description: str, callback: Any) -> bool:
    try:
        if ticket_category_group.get_command(name) is not None:
            return False
    except Exception:
        pass

    ticket_category_group.add_command(
        app_commands.Command(
            name=name,
            description=description,
            callback=callback,
        )
    )
    return True


def _expose_staff_action_view_for_parity_check() -> None:
    try:
        from ..tickets_new.panel import TicketChannelActionsView

        setattr(legacy_ticket_admin, "TicketChannelActionsView", TicketChannelActionsView)
        _log("exposed TicketChannelActionsView to parity checker")
    except Exception as e:
        _warn(f"could not expose TicketChannelActionsView: {e!r}")


def _register_first_class_ticket_panel_commands(bot: Any, tree: Any) -> None:
    try:
        from .public_ticket_panel_commands import register_public_ticket_panel_commands

        register_public_ticket_panel_commands(bot, tree)
    except Exception as e:
        _warn(f"could not register first-class ticket panel commands: {e!r}")


def _patch_tickettool_checker_strictness(tree: Any) -> None:
    """Make /stoney tickettool-check fail if the user-facing panel command is missing."""
    global _CHECKER_PATCHED
    if _CHECKER_PATCHED:
        return

    try:
        from . import public_tickettool_check as checker

        try:
            checker._REQUIRED_INTAKE_COMMANDS.add("post-panel")
        except Exception:
            pass

        original = getattr(checker, "_command_surface_checks", None)
        if not callable(original) or getattr(original, "_panel_strict_wrapped", False):
            _CHECKER_PATCHED = True
            return

        def _strict_command_surface_checks():
            blockers, warnings, ok = original()

            has_top_level = False
            try:
                has_top_level = tree.get_command("ticket-panel", guild=None) is not None
            except Exception:
                has_top_level = False

            if has_top_level:
                ok.append("`/ticket-panel` public Create Ticket panel command is registered.")
            else:
                blockers.append("Missing top-level `/ticket-panel` command for posting the public Create Ticket button.")

            return blockers, warnings, ok

        try:
            setattr(_strict_command_surface_checks, "_panel_strict_wrapped", True)
        except Exception:
            pass
        setattr(checker, "_command_surface_checks", _strict_command_surface_checks)
        _CHECKER_PATCHED = True
        _log("tightened TicketTool checker to require /ticket-panel and /ticket-intake post-panel")
    except Exception as e:
        _warn(f"could not patch TicketTool checker strictness: {e!r}")


def register_public_tickettool_parity_polish(bot: Any, tree: Any) -> None:
    global _ATTACHED

    _register_first_class_ticket_panel_commands(bot, tree)

    if not _ATTACHED:
        added: list[str] = []
        if _ensure_command("list", "List configured dashboard ticket categories.", _category_list_callback):
            added.append("list")
        if _ensure_command("update", "Alias for editing/updating an existing ticket category.", _category_update_callback):
            added.append("update")
        _ATTACHED = True
        if added:
            _log(f"attached /ticket-category aliases: {', '.join(added)}")

    _expose_staff_action_view_for_parity_check()
    _patch_tickettool_checker_strictness(tree)


__all__ = ["register_public_tickettool_parity_polish"]
