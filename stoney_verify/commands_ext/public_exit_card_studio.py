from __future__ import annotations

"""Attachment entry points for the canonical Exit Card Studio."""

import asyncio

import discord

from ..guild_config import get_guild_config, invalidate_guild_config
from ..welcome_card_renderer import MAX_CUSTOM_BACKGROUND_BYTES
from ..welcome_card_service import (
    encode_custom_background,
    normalize_custom_background_for_storage,
)
from .public_setup_group import _require_setup_permission, _upsert_config
from .public_welcome_group import welcome_group


async def _defer(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


@welcome_group.command(
    name="exit-card-upload",
    description="Upload custom 3:1 artwork for live Exit Cards.",
)
async def exit_card_upload(
    interaction: discord.Interaction,
    background: discord.Attachment,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            "❌ Use this inside a server.",
            ephemeral=True,
        )
    await _defer(interaction)
    try:
        filename = str(background.filename or "").lower()
        content_type = str(background.content_type or "").lower()
        if not (
            content_type.startswith("image/")
            or filename.endswith((".png", ".jpg", ".jpeg", ".webp"))
        ):
            raise ValueError("Upload a PNG, JPG, or WEBP image.")
        if int(getattr(background, "size", 0) or 0) > MAX_CUSTOM_BACKGROUND_BYTES:
            raise ValueError("Custom background exceeds the 8 MB upload limit.")

        raw = await background.read()
        normalized, stored_type = await asyncio.to_thread(
            normalize_custom_background_for_storage,
            raw,
        )
        await _upsert_config(
            int(interaction.guild.id),
            {
                "exit_card_enabled": True,
                "exit_card_background_mode": "custom",
                "exit_card_background_b64": encode_custom_background(normalized),
                "exit_card_background_type": stored_type,
                "exit_card_background_name": str(background.filename or "exit-background")[:120],
            },
        )
        invalidate_guild_config(int(interaction.guild.id))
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)

        from ..exit_card_service import exit_card_file

        card = await exit_card_file(interaction.user, cfg)
        await interaction.followup.send(
            "✅ Custom Exit Card artwork saved, normalized to **1200×400**, and previewed below.",
            file=card,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Could not save Exit Card artwork: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


__all__ = ["exit_card_upload"]
