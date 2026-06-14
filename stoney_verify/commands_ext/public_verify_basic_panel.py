from __future__ import annotations

from typing import Optional

import discord

from .public_verify_group import _send


async def verify_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    await _send(interaction, "Panel command is registered.")


def apply() -> bool:
    return True


apply()
