from __future__ import annotations

"""Compatibility entry for the canonical Dank Shield Embed Studio.

Server Setup, feature centers, and Community Tools share one editor. No
separate modal, channel-picking workflow, or duplicate publish handler lives
here anymore. Existing setup callers keep the same public entry function.
"""

import discord


async def open_embed_builder_center(interaction: discord.Interaction) -> None:
    from .embed_studio_ui import open_embed_studio

    await open_embed_studio(interaction)


__all__ = ["open_embed_builder_center"]
