from __future__ import annotations

"""First-class public Create Ticket panel commands.

This replaces the fragile import-hook guard path for the public ticket panel.
The panel must be part of the normal command registration surface so readiness
audits cannot pass while the actual slash command is missing.

Commands provided:
- /ticket-panel
- /ticket-intake post-panel
"""

from typing import Any, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from .public_ticket_intake_group import ticket_intake_group

_ATTACHED_GROUP = False
_ATTACHED_TOP_LEVEL = False


def _log(message: str) -> None:
    try:
        print(f"✅ public_ticket_panel_commands: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _truncate(value: Any, limit: int = 300) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


async def _staff_only(interaction: discord.Interaction) -> bool:
    if _staff_check(interaction):
        return True
    await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    return False


async def _configured_ticket_panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from ..guild_config import get_guild_config

        cfg = await get_guild_config(guild.id, refresh=True)
        for attr in ("ticket_panel_channel_id", "support_channel_id", "verify_channel_id"):
            cid = _safe_int(getattr(cfg, attr, 0), 0)
            if cid <= 0:
                continue
            channel = guild.get_channel(cid)
            if isinstance(channel, discord.TextChannel):
                return channel
    except Exception:
        pass
    return None


def _panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Need help? Open a ticket",
        description=(
            "Press **Create Ticket** below to open a private support ticket.\n\n"
            "A staff member will help you as soon as possible. Please include a clear reason when asked."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="How it works",
        value=(
            "1. Press **Create Ticket**\n"
            "2. Choose or describe what you need\n"
            "3. A private ticket channel opens for you and staff"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{guild.name} • Stoney Verify ticket panel")
    return embed


async def post_ticket_panel_callback(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    if not await _staff_only(interaction):
        return

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    guild = interaction.guild
    if guild is None:
        return await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})

    target = channel or await _configured_ticket_panel_channel(guild)
    if target is None and isinstance(interaction.channel, discord.TextChannel):
        target = interaction.channel
    if target is None:
        return await reply_once(
            interaction,
            {"content": "❌ I could not find a text channel to post the ticket panel. Pick a channel explicitly.", "ephemeral": True},
        )

    me = guild.me
    if me is not None:
        perms = target.permissions_for(me)
        missing: list[str] = []
        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.embed_links:
            missing.append("Embed Links")
        if missing:
            return await reply_once(
                interaction,
                {"content": f"❌ I cannot post the ticket panel in {target.mention}. Missing: {', '.join(missing)}.", "ephemeral": True},
            )

    try:
        from ..tickets_new.panel import TicketPanelView

        view = TicketPanelView()
    except Exception as e:
        return await reply_once(
            interaction,
            {"content": f"❌ Ticket panel button view is unavailable: `{type(e).__name__}: {_truncate(e, 180)}`", "ephemeral": True},
        )

    try:
        msg = await target.send(
            embed=_panel_embed(guild),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as e:
        return await reply_once(
            interaction,
            {"content": f"❌ Failed posting ticket panel in {target.mention}: `{type(e).__name__}: {_truncate(e, 180)}`", "ephemeral": True},
        )

    try:
        from .public_setup_config_writer import upsert_guild_config
        from ..guild_config import invalidate_guild_config

        await upsert_guild_config(
            guild.id,
            {
                "ticket_panel_channel_id": str(int(target.id)),
                "ticket_panel_message_id": str(int(msg.id)),
            },
        )
        invalidate_guild_config(guild.id)
    except Exception:
        pass

    await reply_once(
        interaction,
        {"content": f"✅ Posted the public **Create Ticket** panel in {target.mention}.", "ephemeral": True},
    )


post_ticket_panel_callback = app_commands.describe(  # type: ignore[assignment]
    channel="Optional channel. Defaults to configured support/ticket-panel channel, then current channel."
)(post_ticket_panel_callback)


def _add_group_command() -> bool:
    global _ATTACHED_GROUP
    if _ATTACHED_GROUP:
        return False
    try:
        if ticket_intake_group.get_command("post-panel") is not None:
            _ATTACHED_GROUP = True
            return False
    except Exception:
        pass

    ticket_intake_group.add_command(
        app_commands.Command(
            name="post-panel",
            description="Post the public Create Ticket button panel for users.",
            callback=post_ticket_panel_callback,
        )
    )
    _ATTACHED_GROUP = True
    return True


def _add_top_level_command(tree: Any) -> bool:
    global _ATTACHED_TOP_LEVEL
    if _ATTACHED_TOP_LEVEL:
        return False
    try:
        if tree.get_command("ticket-panel", guild=None) is not None:
            _ATTACHED_TOP_LEVEL = True
            return False
    except Exception:
        pass

    tree.add_command(
        app_commands.Command(
            name="ticket-panel",
            description="Post the public Create Ticket button panel for users.",
            callback=post_ticket_panel_callback,
        )
    )
    _ATTACHED_TOP_LEVEL = True
    return True


def register_public_ticket_panel_commands(bot: Any, tree: Any) -> None:
    _ = bot
    added_group = _add_group_command()
    added_top = _add_top_level_command(tree)
    added: list[str] = []
    if added_group:
        added.append("/ticket-intake post-panel")
    if added_top:
        added.append("/ticket-panel")
    _log("registered " + (", ".join(added) if added else "existing panel commands already present"))


__all__ = ["register_public_ticket_panel_commands", "post_ticket_panel_callback"]
