from __future__ import annotations

"""
Restore the public user-facing ticket panel command in the public command set.

Why this exists:
The public profile intentionally skips legacy top-level ticket admin commands to
keep global slash commands clean. That also means old commands like
/post_ticket_panel are not registered. Users still need an obvious way to post
the public "Create Ticket" button, so this patch adds:

/ticket-intake post-panel

It posts the existing TicketPanelView into the configured support/ticket-panel
channel, or into a selected/current channel.
"""

import builtins
import sys
from typing import Any, Optional

import discord
from discord import app_commands

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🎫 runtime_public_ticket_panel_command {message}")
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


async def _reply_once(interaction: discord.Interaction, content: str, *, ephemeral: bool = True) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(content, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass


def _staff_check(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext.common import _staff_check as common_staff_check

        return bool(common_staff_check(interaction))
    except Exception:
        try:
            member = interaction.user
            return bool(
                isinstance(member, discord.Member)
                and (
                    member.guild_permissions.administrator
                    or member.guild_permissions.manage_guild
                    or member.guild_permissions.manage_channels
                )
            )
        except Exception:
            return False


async def _configured_ticket_panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild.id, refresh=True)
        for attr in ("ticket_panel_channel_id", "support_channel_id", "verify_channel_id"):
            cid = _safe_int(getattr(cfg, attr, 0), 0)
            if cid <= 0:
                continue
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch
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


async def _post_ticket_panel_command(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    if not _staff_check(interaction):
        return await _reply_once(interaction, "❌ Staff only.")

    await _defer(interaction)

    guild = interaction.guild
    if guild is None:
        return await _reply_once(interaction, "❌ This command must be used inside a server.")

    target = channel
    if target is None:
        target = await _configured_ticket_panel_channel(guild)
    if target is None and isinstance(interaction.channel, discord.TextChannel):
        target = interaction.channel

    if target is None:
        return await _reply_once(
            interaction,
            "❌ I could not find a text channel to post the ticket panel. Pick a channel explicitly.",
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
            return await _reply_once(
                interaction,
                f"❌ I cannot post the ticket panel in {target.mention}. Missing: {', '.join(missing)}.",
            )

    try:
        from stoney_verify.tickets_new.panel import TicketPanelView
    except Exception as e:
        return await _reply_once(interaction, f"❌ Ticket panel view is unavailable: `{type(e).__name__}`")

    try:
        view = TicketPanelView()
    except Exception as e:
        return await _reply_once(interaction, f"❌ Could not build the Create Ticket button view: `{type(e).__name__}: {_truncate(e, 180)}`")

    try:
        msg = await target.send(embed=_panel_embed(guild), view=view, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        return await _reply_once(interaction, f"❌ Failed posting ticket panel in {target.mention}: `{type(e).__name__}: {_truncate(e, 180)}`")

    try:
        from stoney_verify.commands_ext.public_setup_config_writer import upsert_guild_config
        from stoney_verify.guild_config import invalidate_guild_config

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

    return await _reply_once(interaction, f"✅ Posted the **Create Ticket** panel in {target.mention}.")


def _attach_to_public_ticket_intake_group(module: Any) -> None:
    global _PATCHED
    if _PATCHED:
        return

    group = getattr(module, "ticket_intake_group", None)
    if group is None:
        return

    try:
        existing = group.get_command("post-panel")
    except Exception:
        existing = None
    if existing is not None:
        _PATCHED = True
        return

    try:
        command = app_commands.Command(
            name="post-panel",
            description="Post the public Create Ticket button panel for users.",
            callback=_post_ticket_panel_command,
        )
        try:
            command._params["channel"].description = "Optional text channel. Defaults to configured support/ticket panel channel."
        except Exception:
            pass
        group.add_command(command)
        _PATCHED = True
        _log("attached /ticket-intake post-panel command for the user-facing Create Ticket button")
    except Exception as e:
        _log(f"failed attaching /ticket-intake post-panel: {e!r}")


def _patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.commands_ext.public_ticket_intake_group")
        if module is not None:
            _attach_to_public_ticket_intake_group(module)
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.commands_ext.public_ticket_intake_group" or name.endswith("commands_ext.public_ticket_intake_group"):
            target = sys.modules.get("stoney_verify.commands_ext.public_ticket_intake_group") or sys.modules.get(name)
            if target is not None:
                _attach_to_public_ticket_intake_group(target)
        else:
            _patch_loaded()
    except Exception:
        pass
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; public ticket panel post command patch active")
