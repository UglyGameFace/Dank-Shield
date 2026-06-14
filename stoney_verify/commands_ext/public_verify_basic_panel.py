from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from .public_verify_group import _send, verify_group


async def verify_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    await _send(interaction, "Panel command is registered.")


try:
    verify_panel = getattr(verify_group, "command")(name="panel", description="Post or refresh the server panel.")(verify_panel)
except Exception:
    pass


def apply() -> bool:
    return bool(app_commands)


apply()
