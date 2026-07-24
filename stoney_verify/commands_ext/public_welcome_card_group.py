from __future__ import annotations

import asyncio
import base64
import io
from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import _require_setup_permission, _upsert_config
from ..guild_config import get_guild_config, invalidate_guild_config
from ..welcome_card_renderer import CARD_SIZE, THEMES, normalize_theme_name, render_member_welcome_card
from ..welcome_card_service import (
    configured_theme,
    resolve_welcome_card_channel,
    send_member_welcome_card,
    welcome_card_permission_problems,
    welcome_cards_enabled,
)
from .public_welcome_group import welcome_group

_RUNTIME_REGISTERED = False
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_MAX_STORED_BYTES = 320 * 1024
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


async def _send(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


def _config_copy(cfg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        if hasattr(cfg, "items"):
            out.update(dict(cfg))
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, dict):
                out.update(nested)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict):
                    out.update(nested)
        except Exception:
            pass
    return out


async def _preview_file(interaction: discord.Interaction, *, theme: Optional[str] = None) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(guild.id), refresh=True)
    preview_cfg = _config_copy(cfg)
    if theme:
        preview_cfg["welcome_card_theme"] = normalize_theme_name(theme)
    try:
        payload = await render_member_welcome_card(member, preview_cfg)
        file = discord.File(io.BytesIO(payload), filename=f"welcome-card-preview-{guild.id}.png")
        await interaction.followup.send(
            "Preview only — nothing was posted publicly.",
            file=file,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Could not render the welcome card: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )


def _compress_background_sync(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    if not data or len(data) > _MAX_UPLOAD_BYTES:
        raise ValueError("Upload must be an image no larger than 8 MB.")
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        image = ImageOps.fit(source.convert("RGB"), CARD_SIZE, method=resample, centering=(0.5, 0.5))

    for quality in (82, 74, 66, 58):
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
        payload = output.getvalue()
        if len(payload) <= _MAX_STORED_BYTES:
            return payload
    raise ValueError("Image is too detailed to store safely. Try a simpler JPG or PNG.")


@welcome_group.command(name="card-preview", description="Preview your personalized welcome card without posting it.")
@app_commands.describe(theme="Optional built-in theme to preview without saving it.")
@app_commands.choices(
    theme=[app_commands.Choice(name=value.label, value=value.key) for value in THEMES.values()]
)
async def welcome_card_preview(
    interaction: discord.Interaction,
    theme: Optional[app_commands.Choice[str]] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    await _preview_file(interaction, theme=theme.value if theme else None)


@welcome_group.command(name="card-theme", description="Choose a built-in welcome-card theme and enable cards.")
@app_commands.choices(
    theme=[app_commands.Choice(name=value.label, value=value.key) for value in THEMES.values()]
)
async def welcome_card_theme(
    interaction: discord.Interaction,
    theme: app_commands.Choice[str],
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    selected = normalize_theme_name(theme.value)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_enabled": True,
            "welcome_card_theme": selected,
            "welcome_card_updated_by_id": str(int(interaction.user.id)),
        },
    )
    invalidate_guild_config(int(interaction.guild.id))
    await interaction.followup.send(
        f"✅ Welcome cards are **ON** using **{THEMES[selected].label}**.",
        ephemeral=True,
    )


@welcome_group.command(name="card-toggle", description="Turn personalized image welcome cards on or off.")
async def welcome_card_toggle(interaction: discord.Interaction, enabled: bool) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_enabled": bool(enabled),
            "welcome_card_updated_by_id": str(int(interaction.user.id)),
        },
    )
    invalidate_guild_config(int(interaction.guild.id))
    await interaction.followup.send(
        f"✅ Personalized welcome cards are now **{'ON' if enabled else 'OFF'}**.",
        ephemeral=True,
    )


@welcome_group.command(name="card-background", description="Use your own background image for personalized welcome cards.")
@app_commands.describe(image="PNG/JPG/WebP. 1200×400 or another 3:1 image is recommended.")
async def welcome_card_background(interaction: discord.Interaction, image: discord.Attachment) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    content_type = str(getattr(image, "content_type", "") or "").lower()
    if content_type and content_type not in _ALLOWED_IMAGE_TYPES:
        return await _send(interaction, "❌ Upload a PNG, JPG, or WebP image.")
    if int(getattr(image, "size", 0) or 0) > _MAX_UPLOAD_BYTES:
        return await _send(interaction, "❌ The image must be 8 MB or smaller.")
    await _defer(interaction)
    try:
        raw = await image.read()
        payload = await asyncio.to_thread(_compress_background_sync, raw)
        encoded = base64.b64encode(payload).decode("ascii")
        await _upsert_config(
            int(interaction.guild.id),
            {
                "welcome_card_enabled": True,
                "welcome_card_background_b64": encoded,
                "welcome_card_background_name": str(image.filename or "custom-background")[:120],
                "welcome_card_updated_by_id": str(int(interaction.user.id)),
            },
        )
        invalidate_guild_config(int(interaction.guild.id))
        await interaction.followup.send(
            "✅ Custom background saved, safely cropped to **1200×400**, and welcome cards are **ON**. Use `/dank welcome card-preview` to inspect it.",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Could not save that background: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )


@welcome_group.command(name="card-background-reset", description="Remove the custom background and return to the selected built-in theme.")
async def welcome_card_background_reset(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_background_b64": "",
            "welcome_card_background_name": "",
            "welcome_card_updated_by_id": str(int(interaction.user.id)),
        },
    )
    invalidate_guild_config(int(interaction.guild.id))
    await interaction.followup.send("✅ Custom background removed. The selected built-in theme is active again.", ephemeral=True)


@welcome_group.command(name="card-health", description="Check welcome-card theme, channel, and permissions.")
async def welcome_card_health(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(guild.id), refresh=True)
    channel = resolve_welcome_card_channel(guild, cfg)
    problems = welcome_card_permission_problems(channel)
    custom_name = str(_config_copy(cfg).get("welcome_card_background_name") or "").strip()
    lines = [
        f"**Status:** {'✅ ON' if welcome_cards_enabled(cfg) else '⚪ OFF'}",
        f"**Theme:** `{configured_theme(cfg)}`",
        f"**Background:** {f'custom — `{custom_name}`' if custom_name else 'built-in'}",
        f"**Channel:** {channel.mention if isinstance(channel, discord.TextChannel) else 'not configured'}",
        f"**Permissions:** {'✅ Ready' if not problems else '❌ ' + ', '.join(problems)}",
        "**Render size:** `1200×400` (3:1)",
    ]
    await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _welcome_card_join_listener(member: discord.Member) -> None:
    try:
        await send_member_welcome_card(member)
    except Exception as exc:
        print(
            f"⚠️ native welcome card listener failed guild={getattr(member.guild, 'id', 0)} "
            f"member={getattr(member, 'id', 0)} error={type(exc).__name__}: {exc}"
        )


def register_public_welcome_card_commands(bot: Any, tree: Any) -> None:
    global _RUNTIME_REGISTERED
    _ = tree
    if _RUNTIME_REGISTERED:
        return
    existing = list((getattr(bot, "extra_events", {}) or {}).get("on_member_join") or [])
    if not any(
        getattr(listener, "__module__", "") == __name__
        and getattr(listener, "__name__", "") == "_welcome_card_join_listener"
        for listener in existing
    ):
        bot.add_listener(_welcome_card_join_listener, "on_member_join")
    _RUNTIME_REGISTERED = True
    print("✅ public_welcome_card_group: native welcome-card listener and commands registered")


__all__ = ["register_public_welcome_card_commands"]
