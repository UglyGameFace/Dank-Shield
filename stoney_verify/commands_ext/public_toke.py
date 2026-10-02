from __future__ import annotations

"""Self-selectable Stoner/Sesh roles plus the member-facing /toke flow."""

import asyncio
import re
import weakref
from collections.abc import Mapping
from time import monotonic
from typing import Any, Optional
from urllib.parse import urlsplit

import discord
from discord import app_commands

from stoney_verify.community_pings_service import (
    COMMUNITY_PINGS_KEY,
    LEGACY_SESH_PING_ROLE_KEY,
    LEGACY_STONER_ROLE_KEY,
    LEGACY_TOKE_CHANNEL_KEY,
    parse_community_pings,
    toke_role_ids,
)
STONER_ROLE_KEY = LEGACY_STONER_ROLE_KEY
SESH_PING_ROLE_KEY = LEGACY_SESH_PING_ROLE_KEY
TOKE_CHANNEL_KEY = LEGACY_TOKE_CHANNEL_KEY

TOKE_USER_COOLDOWN_SECONDS = 15 * 60
TOKE_GUILD_COOLDOWN_SECONDS = 5 * 60

_TOKE_MEDIA_EXTENSIONS = (".gif", ".jpg", ".jpeg", ".png", ".webp")
_TOKE_MEDIA_CONTENT_TYPES = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)


_TOKE_USER_LAST: dict[tuple[int, int], float] = {}
_TOKE_GUILD_LAST: dict[int, float] = {}
_TOKE_LOCKS: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _configured_ids(config: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        _safe_int(config.get(STONER_ROLE_KEY), 0),
        _safe_int(config.get(SESH_PING_ROLE_KEY), 0),
        _safe_int(config.get(TOKE_CHANNEL_KEY), 0),
    )


def _member_has_role_id(member: Any, role_id: int) -> bool:
    rid = int(role_id or 0)
    if rid <= 0:
        return False
    for role in list(getattr(member, "roles", []) or []):
        if _safe_int(getattr(role, "id", 0), 0) == rid:
            return True
    return False


def _clean_member_message(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = discord.utils.escape_mentions(text)
    return text[:180]


def _clean_media_url(value: Any) -> str:
    text = str(value or "").strip().strip("<>")
    if not text:
        return ""
    if len(text) > 2048:
        raise ValueError("Media URL is too long.")
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Media URL must be a valid http:// or https:// link.")
    if any(ch.isspace() for ch in text):
        raise ValueError("Media URL cannot contain spaces.")
    return text


def _is_direct_media_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlsplit(text)
    host = str(parsed.hostname or "").lower()
    path = str(parsed.path or "").lower()
    if path.endswith(_TOKE_MEDIA_EXTENSIONS):
        return True
    if host in {"cdn.discordapp.com", "media.discordapp.net", "media.tenor.com", "i.giphy.com"}:
        return True
    if host.startswith("media") and host.endswith(".giphy.com"):
        return True
    return False


def _attachment_is_supported_media(attachment: Any) -> bool:
    content_type = str(getattr(attachment, "content_type", "") or "").lower()
    if ";" in content_type:
        content_type = content_type.split(";", 1)[0].strip()
    if content_type in _TOKE_MEDIA_CONTENT_TYPES:
        return True
    if content_type and content_type != "application/octet-stream":
        return False
    filename = str(getattr(attachment, "filename", "") or "").lower()
    return filename.endswith(_TOKE_MEDIA_EXTENSIONS)


def _format_wait(seconds: float) -> str:
    remaining = max(1, int(round(seconds)))
    minutes, secs = divmod(remaining, 60)
    if minutes and secs:
        return f"{minutes}m {secs}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _cooldown_remaining(*, now: float, guild_id: int, user_id: int) -> tuple[float, str]:
    user_last = _TOKE_USER_LAST.get((int(guild_id), int(user_id)), 0.0)
    guild_last = _TOKE_GUILD_LAST.get(int(guild_id), 0.0)
    user_remaining = max(0.0, TOKE_USER_COOLDOWN_SECONDS - (now - user_last)) if user_last else 0.0
    guild_remaining = max(0.0, TOKE_GUILD_COOLDOWN_SECONDS - (now - guild_last)) if guild_last else 0.0
    if user_remaining >= guild_remaining and user_remaining > 0:
        return user_remaining, "your /toke cooldown"
    if guild_remaining > 0:
        return guild_remaining, "this server's /toke cooldown"
    return 0.0, ""


def _prune_cooldowns(now: float) -> None:
    horizon = max(TOKE_USER_COOLDOWN_SECONDS, TOKE_GUILD_COOLDOWN_SECONDS) * 2
    for key, ts in list(_TOKE_USER_LAST.items()):
        if now - ts > horizon:
            _TOKE_USER_LAST.pop(key, None)
    for key, ts in list(_TOKE_GUILD_LAST.items()):
        if now - ts > horizon:
            _TOKE_GUILD_LAST.pop(key, None)


def _toke_lock(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _TOKE_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _TOKE_LOCKS[gid] = lock
    return lock


def _ping_permission_ready(role: discord.Role, permissions: discord.Permissions) -> bool:
    return bool(role.mentionable or permissions.mention_everyone)


def _toke_allowed_mentions(role: discord.abc.Snowflake) -> discord.AllowedMentions:
    return discord.AllowedMentions(
        users=False,
        roles=[role],
        everyone=False,
        replied_user=False,
    )


async def _config(guild: discord.Guild) -> Mapping[str, Any]:
    from stoney_verify.guild_config import get_guild_config
    return await get_guild_config(int(guild.id), refresh=True)


async def _reply(interaction: discord.Interaction, content: str, *, ok: bool = False) -> None:
    prefix = "✅ " if ok else "❌ "
    payload = {
        "content": prefix + content,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.send_message(**payload)
    else:
        await interaction.followup.send(**payload)


async def _defer_private(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    await interaction.response.defer(ephemeral=True, thinking=True)


async def open_toke_preset_setup(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    """Compatibility entrypoint for the canonical Community & Pings manager.

    The old preset view used Discord-native role/channel selectors and could
    make valid resources appear missing in large servers. Keep one setup owner.
    """

    await open_community_ping_setup(interaction, replace_message=replace_message)


async def open_community_ping_setup(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    from .public_community_pings import open_community_ping_setup as open_generic_manager

    await open_generic_manager(interaction, replace_message=replace_message)


async def open_member_community_pings(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    from .public_community_pings import open_member_community_pings as open_generic_member

    await open_generic_member(interaction, replace_message=replace_message)


class TokeCheersView(discord.ui.View):
    def __init__(self, starter_id: int, stoner_role_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.starter_id = int(starter_id)
        self.stoner_role_id = int(stoner_role_id)
        self.cheered_ids: set[int] = set()
        self.cheers_count = 0

    @discord.ui.button(label="Cheers", emoji="💨", style=discord.ButtonStyle.success)
    async def cheers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        user_id = _safe_int(getattr(member, "id", 0), 0)
        if member is None or user_id <= 0 or not _member_has_role_id(member, self.stoner_role_id):
            return await _reply(interaction, "The configured /toke starter role is required to join this cheers.")
        if user_id in self.cheered_ids:
            return await _reply(interaction, "You already sent cheers on this call.")
        self.cheered_ids.add(user_id)
        self.cheers_count += 1
        button.label = f"Cheers · {self.cheers_count}"
        await interaction.response.edit_message(view=self)


def _target_channel(
    guild: discord.Guild,
    interaction: discord.Interaction,
    configured_channel_id: int,
) -> Optional[discord.TextChannel]:
    if configured_channel_id > 0:
        channel = guild.get_channel(int(configured_channel_id))
        return channel if isinstance(channel, discord.TextChannel) else None
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


@app_commands.describe(
    message="Optional short message to include with the sesh ping.",
    media="Optional image/GIF URL (Discord CDN, direct media, Tenor/Giphy share link).",
    upload="Optional PNG/JPG/GIF/WEBP upload to place on the Toke card.",
)
async def open_toke_command(
    interaction: discord.Interaction,
    message: Optional[str] = None,
    media: Optional[str] = None,
    upload: Optional[discord.Attachment] = None,
) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "/toke only works inside a server.")

    try:
        clean_media = _clean_media_url(media)
    except ValueError as exc:
        return await _reply(interaction, str(exc))
    if clean_media and upload is not None:
        return await _reply(interaction, "Choose either a media URL or an upload, not both.")
    if upload is not None and not _attachment_is_supported_media(upload):
        return await _reply(
            interaction,
            "Upload a PNG, JPG/JPEG, GIF, or WEBP image for /toke media.",
        )

    await _defer_private(interaction)
    cfg = await _config(guild)
    community_model = parse_community_pings(cfg)
    stoner_id, ping_id = toke_role_ids(community_model, cfg)
    _legacy_stoner_id, _legacy_ping_id, channel_id = _configured_ids(cfg)
    if stoner_id <= 0 or ping_id <= 0:
        return await _reply(
            interaction,
            "This server has not configured the /toke starter and notification roles in Community & Pings.",
        )

    stoner_role = guild.get_role(stoner_id)
    ping_role = guild.get_role(ping_id)
    if not isinstance(stoner_role, discord.Role) or not isinstance(ping_role, discord.Role):
        return await _reply(
            interaction,
            "A configured /toke Community & Pings role no longer exists. Staff should repair the mapping.",
        )
    if not _member_has_role_id(member, stoner_id):
        return await _reply(interaction, f"You need the configured Community & Pings starter role {stoner_role.mention} to use /toke.")
    channel = _target_channel(guild, interaction, channel_id)
    if not isinstance(channel, discord.TextChannel):
        return await _reply(
            interaction,
            "The preferred sesh channel is missing, or /toke was used outside a normal text channel.",
        )

    me = guild.me
    if not isinstance(me, discord.Member):
        return await _reply(interaction, "Dank Shield could not resolve its server permissions.")
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        return await _reply(interaction, f"Dank Shield cannot post the sesh card in {channel.mention}.")
    if upload is not None and not perms.attach_files:
        return await _reply(
            interaction,
            f"Dank Shield needs **Attach Files** in {channel.mention} to include uploaded /toke media.",
        )
    if not _ping_permission_ready(ping_role, perms):
        return await _reply(
            interaction,
            f"{ping_role.mention} is not mentionable and Dank Shield lacks Mention @everyone, @here, and All Roles in {channel.mention}.",
        )

    now = monotonic()
    _prune_cooldowns(now)
    remaining, label = _cooldown_remaining(now=now, guild_id=guild.id, user_id=member.id)
    if remaining > 0:
        return await _reply(interaction, f"Wait {_format_wait(remaining)} for {label}.")

    clean_message = _clean_member_message(message)
    async with _toke_lock(int(guild.id)):
        now = monotonic()
        remaining, label = _cooldown_remaining(now=now, guild_id=guild.id, user_id=member.id)
        if remaining > 0:
            return await _reply(interaction, f"Wait {_format_wait(remaining)} for {label}.")

        embed = discord.Embed(
            title="💨 Toke Time",
            description=(
                f"**{discord.utils.escape_markdown(member.display_name)}** is calling the sesh crowd."
                + (f"\n\n> {discord.utils.escape_markdown(clean_message)}" if clean_message else "")
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Only members with the configured /toke notification role were notified.")

        upload_file: Optional[discord.File] = None
        direct_media = bool(clean_media and _is_direct_media_url(clean_media))
        if upload is not None:
            try:
                upload_file = await upload.to_file()
            except discord.HTTPException as exc:
                return await _reply(
                    interaction,
                    f"Discord could not read the uploaded /toke media: {type(exc).__name__}.",
                )
            embed.set_image(url=f"attachment://{upload_file.filename}")
        elif direct_media:
            embed.set_image(url=clean_media)

        send_content = ping_role.mention
        if clean_media and not direct_media:
            # Keep share-page URLs (for example normal Tenor/Giphy links) on
            # the same message so Discord can render its native link preview.
            # Dank Shield never fetches arbitrary user URLs server-side.
            send_content += f"\n{clean_media}"

        send_payload: dict[str, Any] = {
            "content": send_content,
            "embed": embed,
            "view": TokeCheersView(member.id, stoner_role.id),
            "allowed_mentions": _toke_allowed_mentions(ping_role),
        }
        if upload_file is not None:
            send_payload["file"] = upload_file

        try:
            sent = await channel.send(**send_payload)
        except discord.Forbidden:
            return await _reply(interaction, f"Discord blocked the sesh ping in {channel.mention}.")
        except discord.HTTPException as exc:
            return await _reply(interaction, f"Discord could not send the sesh ping: {type(exc).__name__}.")

        stamp = monotonic()
        _TOKE_USER_LAST[(int(guild.id), int(member.id))] = stamp
        _TOKE_GUILD_LAST[int(guild.id)] = stamp

    await interaction.followup.send(
        f"💨 Toke call sent in {channel.mention}: {sent.jump_url}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


__all__ = [
    "SESH_PING_ROLE_KEY",
    "STONER_ROLE_KEY",
    "TOKE_CHANNEL_KEY",
    "TOKE_GUILD_COOLDOWN_SECONDS",
    "TOKE_USER_COOLDOWN_SECONDS",
    "TokeCheersView",
    "_attachment_is_supported_media",
    "_clean_media_url",
    "_clean_member_message",
    "_is_direct_media_url",
    "_configured_ids",
    "_cooldown_remaining",
    "_member_has_role_id",
    "_ping_permission_ready",
    "_toke_allowed_mentions",
    "open_community_ping_setup",
    "open_member_community_pings",
    "open_toke_preset_setup",
    "open_toke_command",
]
