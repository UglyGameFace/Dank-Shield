from __future__ import annotations

"""
Simple public setup entrypoint.

Server owners should not have to guess which setup command starts the process.
This adds the obvious happy path:

/stoney setup

It opens the same guided assistant as /stoney setup-assistant, while keeping the
more specific setup commands available for advanced/manual fixes.
"""

from typing import Any

import discord

from .common import safe_defer
from .public_setup_group import _require_setup_permission, stoney_group


_ATTACHED = False


async def _setup_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        from .public_setup_assistant import _build_assistant_payload

        embed, view = await _build_assistant_payload(guild)
        embed.title = "🚀 Stoney Quick Setup"
        embed.description = (
            "This is the main setup screen. Pick the easiest path below:\n\n"
            "✨ **Auto-Fix Missing Defaults** creates only missing default roles/channels.\n"
            "✏️ **Customize Missing Names** lets you rename missing items first.\n"
            "🧩 **Choose Existing Items** is for servers that already have their own layout.\n\n"
            f"{embed.description or ''}"
        )[:4096]
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Setup assistant failed: `{repr(e)[:300]}`", ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return

    try:
        existing = stoney_group.get_command("setup")
    except Exception:
        existing = None
    if existing is not None:
        _ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="setup",
        description="Start the guided Stoney setup flow.",
        callback=_setup_callback,
    )
    stoney_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_start_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_start: attached /stoney setup quick-start command")
    except Exception:
        pass


__all__ = ["register_public_setup_start_commands"]
