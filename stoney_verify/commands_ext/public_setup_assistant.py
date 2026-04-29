from __future__ import annotations

"""
Interactive setup assistant for fresh public guilds.

This is the friendly layer on top of /stoney health:
- scans what the server is missing
- offers a one-click recommended default setup
- offers a custom setup path for owners who want their own names/layout
- lets admins re-check health without guessing which command comes next
"""

from typing import Any

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _field_text,
    _health_embed,
    _require_setup_permission,
    stoney_group,
)
from ..guild_config import get_guild_config


_ATTACHED = False


def _short_lines(lines: list[str], *, limit: int = 900, empty: str = "✅ Nothing missing") -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        extra = len(text) + 1
        if total + extra > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(text)
        total += extra
    return "\n".join(out) or empty


def _setup_command_guide() -> str:
    return (
        "**Recommended / automatic setup**\n"
        "`/stoney setup-defaults` — creates professional default roles/channels/categories.\n\n"
        "**Custom setup**\n"
        "`/stoney setup-access` — choose server-control and staff roles.\n"
        "`/stoney setup-tickets` — choose ticket category, archive category, staff role, transcripts.\n"
        "`/stoney setup-verify` — choose verify channel, roles, VC verify channel.\n"
        "`/stoney setup-logs` — choose mod/security/join log channels.\n"
        "`/stoney setup-status` — choose bot status channel.\n\n"
        "**Finding IDs or existing channels**\n"
        "`/stoney setup-picker` — guided dropdown setup.\n"
        "`/stoney setup-find` — search server items when Discord selectors are annoying."
    )


async def _build_assistant_embed(guild: discord.Guild) -> discord.Embed:
    cfg = await get_guild_config(guild.id, refresh=True)
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    ready = not blockers

    embed = discord.Embed(
        title="🧭 Stoney Setup Assistant",
        description=(
            "✅ **This server looks ready.** You can still use the buttons below to re-check or review custom setup commands."
            if ready
            else "I found missing setup pieces. Choose **Create Recommended Defaults** for a clean starter layout, or use **Custom Setup Guide** if this server already has its own channels/roles."
        ),
        color=discord.Color.green() if ready else discord.Color.blurple(),
    )
    embed.add_field(name="Missing / Blockers", value=_short_lines(blockers), inline=False)
    embed.add_field(name="Warnings", value=_short_lines(warnings, empty="✅ None"), inline=False)
    if ok:
        embed.add_field(name="Already Working", value=_field_text(ok[:8], empty="Nothing checked yet."), inline=False)
    embed.add_field(
        name="Recommended Choice",
        value=(
            "For a brand-new server, press **Create Recommended Defaults**.\n"
            "For an existing/custom server, press **Custom Setup Guide** and choose your own roles/channels."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • setup assistant")
    return embed


class SetupAssistantView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    async def _require(self, interaction: discord.Interaction) -> bool:
        return await _require_setup_permission(interaction)

    @discord.ui.button(label="Create Recommended Defaults", emoji="✨", style=discord.ButtonStyle.success)
    async def create_defaults(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        try:
            from .public_setup_defaults import _setup_defaults_callback
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Default setup is unavailable right now: `{type(e).__name__}`",
                ephemeral=True,
            )
        await _setup_defaults_callback(
            interaction,
            control_role=None,
            staff_role=None,
            create_missing_roles=True,
            apply_channel_permissions=True,
        )

    @discord.ui.button(label="Custom Setup Guide", emoji="🛠️", style=discord.ButtonStyle.primary)
    async def custom_guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        embed = discord.Embed(
            title="🛠️ Custom Setup Guide",
            description=(
                "Use this path when the server owner wants their own channel names, role names, or category layout.\n\n"
                + _setup_command_guide()
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Good public defaults",
            value=(
                "Roles: `Bot Manager`, `Support Team`, `Unverified`, `Verified`, `Member`\n"
                "Channels: `👋・welcome`, `✅・verify`, `🎫・support`, `📑・transcripts`, `🛡️・mod-log`, `📡・bot-status`"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Run Health Check", emoji="🩺", style=discord.ButtonStyle.secondary)
    async def run_health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        await interaction.followup.send(embed=_health_embed(guild, cfg), view=SetupAssistantView(), ephemeral=True)


async def _setup_assistant_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    embed = await _build_assistant_embed(guild)
    await interaction.followup.send(embed=embed, view=SetupAssistantView(), ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return

    try:
        existing = stoney_group.get_command("setup-assistant")
    except Exception:
        existing = None

    if existing is not None:
        _ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="setup-assistant",
        description="Show missing setup pieces and choose automatic or custom setup.",
        callback=_setup_assistant_callback,
    )
    stoney_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_assistant_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_assistant: attached /stoney setup-assistant command")
    except Exception:
        pass


__all__ = ["register_public_setup_assistant_commands"]
