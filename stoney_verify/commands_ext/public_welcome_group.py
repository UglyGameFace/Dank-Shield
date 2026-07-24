from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord
from discord import app_commands

from ..guild_config import get_guild_config, invalidate_guild_config
from ..welcome_message import (
    build_welcome_embed,
    post_or_update_welcome_message,
    reset_welcome_template,
    save_welcome_template,
    welcome_channel_for,
)
from ..welcome_card_renderer import BUILTIN_THEMES, normalize_theme_key
from ..welcome_card_service import (
    encode_custom_background,
    normalize_custom_background_for_storage,
    welcome_card_file,
)
from .public_setup_group import _require_setup_permission, _upsert_config, dank_group

_ATTACHED = False

welcome_group = app_commands.Group(
    name="welcome",
    description="Set up welcome, join, and leave messages for this server.",
)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    return default


def _cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            value = _cfg_value(cfg, key, None)
            if value is None or isinstance(value, bool):
                continue
            text = str(value).strip()
            if text:
                return int(text)
        except Exception:
            continue
    return 0


def _cfg_bool(cfg: Any, *keys: str) -> bool:
    for key in keys:
        try:
            value = _cfg_value(cfg, key, None)
            if value is not None:
                return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
        except Exception:
            continue
    return False


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _send(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


def _event_channel_health(guild: discord.Guild, cfg: Any, *, kind: str) -> list[str]:
    if kind == "leave":
        enabled = _cfg_bool(cfg, "welcome_leave_enabled", "goodbye_enabled", "leave_message_enabled")
        cid = _cfg_int(cfg, "goodbye_channel_id", "leave_channel_id", "welcome_channel_id")
        label = "Leave/goodbye"
    else:
        enabled = _cfg_bool(cfg, "welcome_join_enabled", "join_welcome_enabled")
        cid = _cfg_int(cfg, "join_welcome_channel_id", "welcome_channel_id")
        label = "Join/welcome"
    channel = guild.get_channel(cid) if cid > 0 else None
    lines = [f"{'✅' if enabled else '❌'} {label} messages: {'enabled' if enabled else 'disabled'}"]
    if not enabled:
        return lines
    if not isinstance(channel, discord.TextChannel):
        lines.append(f"❌ {label} channel is not set or no longer exists.")
        return lines
    lines.append(f"✅ {label} channel: {channel.mention}")
    me = guild.me if isinstance(guild.me, discord.Member) else None
    if isinstance(me, discord.Member):
        perms = channel.permissions_for(me)
        needed = {
            "View Channel": perms.view_channel,
            "Send Messages": perms.send_messages,
            "Embed Links": perms.embed_links,
            "Read Message History": perms.read_message_history,
        }
        if kind != "leave" and _cfg_bool(cfg, "welcome_card_enabled"):
            needed["Attach Files"] = perms.attach_files
        missing = [name for name, ok in needed.items() if not ok]
        if missing:
            lines.append(f"❌ {label} missing bot permissions: " + ", ".join(missing))
        else:
            lines.append(f"✅ Bot can post {label.lower()} messages.")
    return lines


async def open_welcome_preview(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(guild.id), refresh=True)
    embed = build_welcome_embed(guild, cfg)
    await interaction.followup.send("Preview only. Press **Post/Update** in `/dank setup` → Feature Centers → Welcome Center when ready.", embed=embed, ephemeral=True)


async def post_welcome_message(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    try:
        result = await post_or_update_welcome_message(guild, channel=channel, actor_id=int(interaction.user.id))
        target = guild.get_channel(result.channel_id)
        mention = target.mention if isinstance(target, discord.TextChannel) else f"`{result.channel_id}`"
        await interaction.followup.send(f"✅ Welcome message **{result.status}** in {mention}. Saved message `{result.message_id}`.", ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not post welcome message: `{type(exc).__name__}: {exc}`", ephemeral=True)


async def save_welcome_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_channel_id": str(int(channel.id)),
            "welcome_message_enabled": True,
            "welcome_message_updated_by_id": str(int(interaction.user.id)),
        },
    )
    invalidate_guild_config(int(interaction.guild.id))
    await interaction.followup.send(f"✅ Welcome channel saved as {channel.mention}. Press **Post/Update** in Welcome Center to post/update the message.", ephemeral=True)


async def save_welcome_template_service(interaction: discord.Interaction, title: Optional[str] = None, body: Optional[str] = None) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    if not title and not body:
        return await _send(interaction, "❌ Provide a title and/or body. Example placeholders: `{server_name}`, `{rules}`, `{verify}`, `{support}`.")
    await _defer(interaction)
    await save_welcome_template(int(interaction.guild.id), title=title, body=body, actor_id=int(interaction.user.id))
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    embed = build_welcome_embed(interaction.guild, cfg)
    await interaction.followup.send("✅ Welcome template saved. Preview below. Press **Post/Update** in Welcome Center to update the public message.", embed=embed, ephemeral=True)


async def reset_welcome_template_service(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    await reset_welcome_template(int(interaction.guild.id), actor_id=int(interaction.user.id))
    await interaction.followup.send("✅ Welcome template reset to the default. Press **Post/Update** in Welcome Center to update the public message.", ephemeral=True)


async def open_welcome_events(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    try:
        from stoney_verify import welcome_event_services

        await welcome_event_services.open_welcome_events_center(interaction)
    except Exception as exc:
        await _send(interaction, f"❌ Could not open join/leave setup: `{type(exc).__name__}: {exc}`")


async def open_welcome_health(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(guild.id), refresh=True)
    channel = welcome_channel_for(guild, cfg)
    lines: list[str] = []
    if channel is None:
        lines.append("❌ Welcome channel is not set. Use Core Setup → Use Existing Roles/Channels, or `/dank welcome set-channel`.")
    else:
        lines.append(f"✅ Welcome channel: {channel.mention}")
        me = guild.me
        if isinstance(me, discord.Member):
            perms = channel.permissions_for(me)
            needed = {
                "View Channel": perms.view_channel,
                "Send Messages": perms.send_messages,
                "Embed Links": perms.embed_links,
                "Read Message History": perms.read_message_history,
            }
            if _cfg_bool(cfg, "welcome_card_enabled"):
                needed["Attach Files"] = perms.attach_files
            missing = [name for name, ok in needed.items() if not ok]
            if missing:
                lines.append("❌ Missing bot permissions: " + ", ".join(missing))
            else:
                lines.append("✅ Bot can post/update the welcome message.")
    lines.append("")
    lines.extend(_event_channel_health(guild, cfg, kind="join"))
    lines.extend(_event_channel_health(guild, cfg, kind="leave"))
    await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@welcome_group.command(name="preview", description="Preview this server's current welcome message.")
async def welcome_preview(interaction: discord.Interaction) -> None:
    await open_welcome_preview(interaction)


@welcome_group.command(name="post", description="Post or update the welcome message without creating duplicates.")
@app_commands.describe(channel="Optional welcome channel. Defaults to the saved welcome channel.")
async def welcome_post(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    await post_welcome_message(interaction, channel=channel)


@welcome_group.command(name="set-channel", description="Save the welcome/start-here channel for this server.")
@app_commands.describe(channel="The channel where Dank Shield should post the welcome message.")
async def welcome_set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    await save_welcome_channel(interaction, channel)


@welcome_group.command(name="template", description="Set a simple custom welcome title/body for this server.")
@app_commands.describe(title="Optional title. Supports {server_name}.", body="Optional body. Supports {server_name}, {rules}, {verify}, and {support}.")
async def welcome_template(interaction: discord.Interaction, title: Optional[str] = None, body: Optional[str] = None) -> None:
    await save_welcome_template_service(interaction, title=title, body=body)


_WELCOME_THEME_CHOICES = [
    app_commands.Choice(name=theme.label, value=theme.key)
    for theme in BUILTIN_THEMES.values()
]


@welcome_group.command(name="card-preview", description="Preview the personalized card used for new members.")
@app_commands.describe(theme="Optional built-in theme to preview without saving it.")
@app_commands.choices(theme=_WELCOME_THEME_CHOICES)
async def welcome_card_preview(
    interaction: discord.Interaction,
    theme: Optional[app_commands.Choice[str]] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    try:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        card = await welcome_card_file(
            interaction.user,
            cfg,
            theme_override=theme.value if theme is not None else None,
        )
        await interaction.followup.send(
            "Preview only — this uses your current profile picture and the server's live member count.",
            file=card,
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Could not render welcome card: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )


@welcome_group.command(name="card-theme", description="Choose one of Dank Shield's built-in welcome card themes.")
@app_commands.describe(theme="The built-in theme to use for new member cards.")
@app_commands.choices(theme=_WELCOME_THEME_CHOICES)
async def welcome_card_theme(
    interaction: discord.Interaction,
    theme: app_commands.Choice[str],
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    theme_key = normalize_theme_key(theme.value)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_enabled": True,
            "welcome_card_theme": theme_key,
        },
    )
    invalidate_guild_config(int(interaction.guild.id))
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    card = await welcome_card_file(interaction.user, cfg) if isinstance(interaction.user, discord.Member) else None
    await interaction.followup.send(
        f"✅ Welcome card theme set to **{BUILTIN_THEMES[theme_key].label}**."
        + (" Clear the custom background with `/dank welcome card-clear-custom` to see the built-in artwork." if _cfg_value(cfg, "welcome_card_background_b64", "") else ""),
        file=card,
        ephemeral=True,
    )


@welcome_group.command(name="card-upload", description="Upload your own safe 3:1 welcome card background.")
@app_commands.describe(background="PNG, JPG, or WEBP. Recommended: 1200×400 or another 3:1 size.")
async def welcome_card_upload(
    interaction: discord.Interaction,
    background: discord.Attachment,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    try:
        filename = str(background.filename or "").lower()
        content_type = str(background.content_type or "").lower()
        if not (
            content_type.startswith("image/")
            or filename.endswith((".png", ".jpg", ".jpeg", ".webp"))
        ):
            raise ValueError("Upload a PNG, JPG, or WEBP image.")
        raw = await background.read()
        normalized, stored_type = await asyncio.to_thread(
            normalize_custom_background_for_storage,
            raw,
        )
        await _upsert_config(
            int(interaction.guild.id),
            {
                "welcome_card_enabled": True,
                "welcome_card_background_b64": encode_custom_background(normalized),
                "welcome_card_background_type": stored_type,
                "welcome_card_background_name": str(background.filename or "custom-background")[:120],
            },
        )
        invalidate_guild_config(int(interaction.guild.id))
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        card = await welcome_card_file(interaction.user, cfg) if isinstance(interaction.user, discord.Member) else None
        await interaction.followup.send(
            "✅ Custom welcome background saved, cropped to **1200×400**, and previewed below.",
            file=card,
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Could not save custom background: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
        )


@welcome_group.command(name="card-clear-custom", description="Remove the custom background and return to the selected built-in theme.")
async def welcome_card_clear_custom(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "welcome_card_background_b64": "",
            "welcome_card_background_type": "",
            "welcome_card_background_name": "",
        },
    )
    invalidate_guild_config(int(interaction.guild.id))
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    card = await welcome_card_file(interaction.user, cfg) if isinstance(interaction.user, discord.Member) else None
    await interaction.followup.send(
        "✅ Custom background removed. The selected built-in theme is active.",
        file=card,
        ephemeral=True,
    )


@welcome_group.command(name="card-enabled", description="Enable or disable personalized image cards without changing join messages.")
@app_commands.describe(enabled="On sends image cards; off falls back to the normal welcome embed.")
async def welcome_card_enabled(
    interaction: discord.Interaction,
    enabled: bool,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    await _upsert_config(
        int(interaction.guild.id),
        {"welcome_card_enabled": bool(enabled)},
    )
    invalidate_guild_config(int(interaction.guild.id))
    await interaction.followup.send(
        f"✅ Personalized welcome cards are now **{'enabled' if enabled else 'disabled'}**.",
        ephemeral=True,
    )


@welcome_group.command(name="events", description="Set up optional join and leave announcements for this server.")
async def welcome_events(interaction: discord.Interaction) -> None:
    await open_welcome_events(interaction)


@welcome_group.command(name="reset", description="Reset the welcome message template back to the public default.")
async def welcome_reset(interaction: discord.Interaction) -> None:
    await reset_welcome_template_service(interaction)


@welcome_group.command(name="health", description="Check whether welcome, join, and leave message setup is ready.")
async def welcome_health(interaction: discord.Interaction) -> None:
    await open_welcome_health(interaction)


def _attach() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        existing = dank_group.get_command("welcome")
        if existing is not None:
            _ATTACHED = True
            return True
    except Exception:
        pass
    try:
        dank_group.add_command(welcome_group)
        _ATTACHED = True
        return True
    except Exception as exc:
        try:
            print(f"⚠️ public_welcome_group failed attaching /dank welcome: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


def register_public_welcome_group_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    if _attach():
        try:
            print("✅ public_welcome_group: attached /dank welcome message commands")
        except Exception:
            pass


_attach()

__all__ = [
    "register_public_welcome_group_commands",
    "welcome_group",
    "open_welcome_health",
    "open_welcome_preview",
    "post_welcome_message",
    "save_welcome_channel",
    "save_welcome_template_service",
    "reset_welcome_template_service",
    "open_welcome_events",
    "welcome_card_preview",
    "welcome_card_theme",
    "welcome_card_upload",
    "welcome_card_clear_custom",
    "welcome_card_enabled",
]
