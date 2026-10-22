from __future__ import annotations

"""Self-selectable Stoner/Sesh roles plus the member-facing /toke flow."""

import asyncio
from html.parser import HTMLParser
import re
import time
import weakref
from collections.abc import Mapping
from time import monotonic
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from stoney_verify import toke_sessions
from stoney_verify.toke_live_ui import (
    TokeCheersView, TokeMediaLayout, begin_capture, choose_voice, media_url_from_message, pending_for, render_card, settings_from_config,
)

import aiohttp
import discord
from discord import app_commands
from stoney_verify.share_router_media_network import (
    is_safe_media_download_url, public_get, public_tcp_connector,
)

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
    """Detect embeddable image assets, not provider sharing pages or videos."""
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    host = str(parsed.hostname or "").lower()
    path = str(parsed.path or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return False
    if path.endswith((".mp4", ".webm", ".mov", ".m3u8")):
        return False
    if path.endswith(_TOKE_MEDIA_EXTENSIONS):
        return True
    # Some image CDNs use extensionless paths and specify image type via
    # ?format=jpg or ?format=gif (notably Twitter/X's pbs.twimg.com).
    formats = parse_qs(parsed.query).get("format", [])
    if formats and host in {"pbs.twimg.com", "images.unsplash.com"}:
        return formats[0].lower() in {"gif", "jpg", "jpeg", "png", "webp"}
    if host in {"cdn.discordapp.com", "media.discordapp.net", "media.tenor.com", "i.giphy.com"}:
        return True
    if host.startswith("media") and host.endswith(".giphy.com"):
        return True
    return False


def _is_temporary_discord_media_url(value: str) -> bool:
    """Draft-message CDN/proxy URLs are not durable after source deletion."""
    try:
        host = str(urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return (
        host in {"cdn.discordapp.com", "media.discordapp.net"}
        or (host.startswith("images-ext-") and host.endswith(".discordapp.net"))
    )


def _is_stable_provider_media_url(value: str) -> bool:
    """Accept durable public image URLs from any host, excluding draft CDN assets."""
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    host = str(parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or not is_safe_media_download_url(value):
        return False
    # The temporary composer message is deleted on success. Its Discord CDN
    # attachment URL is not a safe source for future refreshes or restarts.
    if _is_temporary_discord_media_url(value):
        return False
    return _is_direct_media_url(value)


class _TokeMediaMetaParser(HTMLParser):
    """Extract image-only OpenGraph/Twitter assets from bounded public HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        meta = dict(attrs)
        label = str(meta.get("property") or meta.get("name") or "").lower()
        if label in {"og:image", "og:image:secure_url", "og:image:url",
                     "twitter:image", "twitter:image:src"}:
            self.images.append(str(meta.get("content") or ""))


def _provider_page_image(html: str) -> str:
    parser = _TokeMediaMetaParser()
    parser.feed(html)
    # Prefer animation when the page exposes both JPG cover art and GIF.
    # Other providers are permitted, but only direct HTTPS public image assets.
    candidates = [url for url in parser.images if _is_stable_provider_media_url(url)]
    return next(
        (url for url in candidates if urlsplit(url).path.lower().endswith(".gif")),
        candidates[0] if candidates else "",
    )


async def _resolve_toke_media_url(value: str) -> str:
    """Normalize direct GIFs or metadata-backed sharing pages before publishing."""
    if _is_direct_media_url(value):
        return value
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Media sharing link is not a valid public HTTPS URL.") from exc
    if (parsed.scheme.lower() != "https" or parsed.username or parsed.password
            or port not in (None, 443) or not is_safe_media_download_url(value)):
        raise ValueError("Use a public HTTPS GIF/image sharing link or upload the file.")

    # Keep this existing fast path for GIPHY's canonical GIF pages. Other
    # providers are handled by the same generic metadata path below.
    host, path = str(parsed.hostname or "").lower(), str(parsed.path or "")
    if host in {"giphy.com", "www.giphy.com"} and path.startswith("/gifs/"):
        slug = path[len("/gifs/"):].strip("/")
        if "/" not in slug:
            gif_id = slug.rsplit("-", 1)[-1]
            if re.fullmatch(r"[A-Za-z0-9]{6,}", gif_id):
                return f"https://media.giphy.com/media/{gif_id}/giphy.gif"

    # Only the canonical public-network reader can follow redirects: each hop
    # is checked and DNS refuses private/loopback targets. Inspect at most
    # 256 KiB of HTML, never download a whole video/GIF and never follow HTML
    # frames/scripts. This covers Imgur, Reddit, blogs, other providers, etc.
    try:
        timeout = aiohttp.ClientTimeout(total=5, connect=3)
        async with aiohttp.ClientSession(
            connector=public_tcp_connector(limit=2), timeout=timeout,
        ) as session:
            response, final_url = await public_get(session, value, max_redirects=3)
            try:
                if response.status != 200:
                    raise ValueError("The media sharing page is unavailable.")
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type.split(";", 1)[0].strip() in _TOKE_MEDIA_CONTENT_TYPES:
                    # A public image endpoint can be extensionless. Its verified
                    # response MIME makes it embeddable even without .gif/.jpg.
                    destination = urlsplit(final_url)
                    if (destination.scheme.lower() == "https"
                            and is_safe_media_download_url(final_url)
                            and destination.hostname not in {"cdn.discordapp.com", "media.discordapp.net"}):
                        return final_url
                    raise ValueError("The image URL is not stable or publicly embeddable.")
                if "text/html" not in content_type:
                    raise ValueError("The shared URL did not return a GIF/image page.")
                length = str(response.headers.get("Content-Length", "")).strip()
                if length.isascii() and length.isdecimal() and (
                    len(length) > 9 or int(length) > 256 * 1024
                ):
                    raise ValueError("The media sharing page is too large to inspect.")
                chunks: list[bytes] = []
                remaining = 256 * 1024 + 1
                while remaining > 0:
                    # StreamReader.read(n) may return a partial chunk before EOF.
                    # Keep reading within the deadline/cap so later OG tags
                    # aren't incorrectly treated as a missing image.
                    chunk = await response.content.read(min(16 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if remaining == 0:
                    raise ValueError("The media sharing page is too large to inspect.")
                raw = b"".join(chunks)
            finally:
                response.release()
        found = _provider_page_image(raw.decode("utf-8", errors="replace"))
        if found:
            return found
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise ValueError(
            "Could not load this GIF/image sharing page. Upload the file or use its direct image URL."
        ) from exc

    raise ValueError(
        "This sharing page does not expose a public inline GIF/image. "
        "Upload the GIF/photo or use its direct image URL instead."
    )


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


def _cooldown_remaining(*, now: float, guild_id: int, user_id: int, user_seconds: int = TOKE_USER_COOLDOWN_SECONDS, guild_seconds: int = TOKE_GUILD_COOLDOWN_SECONDS) -> tuple[float, str]:
    user_last = _TOKE_USER_LAST.get((int(guild_id), int(user_id)), 0.0)
    guild_last = _TOKE_GUILD_LAST.get(int(guild_id), 0.0)
    user_remaining = max(0.0, user_seconds - (now - user_last)) if user_last else 0.0
    guild_remaining = max(0.0, guild_seconds - (now - guild_last)) if guild_last else 0.0
    if user_remaining >= guild_remaining and user_remaining > 0:
        return user_remaining, "your /toke cooldown"
    if guild_remaining > 0:
        return guild_remaining, "this server's /toke cooldown"
    return 0.0, ""


async def _persistent_cooldown_remaining(
    guild_id: int, user_id: int, settings: Mapping[str, Any]
) -> tuple[float, str]:
    """Use both live monotonic and recorded send times; restart cannot reset limits."""
    user_seconds = int(settings["user_cooldown_minutes"]) * 60
    guild_seconds = int(settings["guild_cooldown_minutes"]) * 60
    volatile, label = _cooldown_remaining(
        now=monotonic(), guild_id=guild_id, user_id=user_id,
        user_seconds=user_seconds, guild_seconds=guild_seconds,
    )
    guild_last, user_last = await toke_sessions.recent_launch_times(guild_id, user_id)
    current = time.time()
    persisted_user = max(0.0, user_seconds - (current - user_last)) if user_last else 0
    persisted_guild = max(0.0, guild_seconds - (current - guild_last)) if guild_last else 0
    if persisted_user > max(volatile, persisted_guild):
        return persisted_user, "your /toke cooldown"
    if persisted_guild > volatile:
        return persisted_guild, "this server's /toke cooldown"
    return volatile, label


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


async def _publish_toke(
    *,
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.TextChannel,
    starter_role: discord.Role,
    notify_role: discord.Role,
    message_text: str,
    clean_media: str,
    upload: Optional[discord.Attachment],
    settings: Mapping[str, Any],
    voice_channel_id: int = 0,
) -> discord.Message:
    """The only outbound /toke send path for slash and normal-composer media."""
    me = guild.me
    if not isinstance(me, discord.Member):
        raise ValueError("Dank Shield could not resolve its guild member.")
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        raise ValueError("Dank Shield needs View Channel, Send Messages and Embed Links in the Toke channel.")
    if upload is not None and not perms.attach_files:
        raise ValueError("Dank Shield needs Attach Files to repost your GIF.")
    if not _ping_permission_ready(notify_role, perms):
        raise ValueError("The Toke notification role is not mentionable and Dank Shield cannot mention it.")
    if upload is not None and not _attachment_is_supported_media(upload):
        raise ValueError("Only GIF, JPG, PNG and WEBP images can be attached.")

    async with _toke_lock(guild.id):
        now = monotonic()
        remaining, label = await _persistent_cooldown_remaining(guild.id, member.id, settings)
        if remaining:
            raise ValueError(f"Wait {_format_wait(remaining)} for {label}.")

        embed = discord.Embed(
            title="💨 Toke Time",
            description=(
                f"**{discord.utils.escape_markdown(member.display_name)}** is calling the sesh crowd."
                + (f"\n\n> {discord.utils.escape_markdown(_clean_member_message(message_text))}" if message_text else "")
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Only members with the configured /toke notification role were notified.")
        upload_file: Optional[discord.File] = None
        if clean_media:
            clean_media = await _resolve_toke_media_url(clean_media)
        if upload is not None:
            # Editable attachment:// embeds duplicate after PATCH, but naked
            # attachments display far too large above the session card.
            # Components V2 MediaGallery renders one full-width inline image through edits.
            upload_file = await upload.to_file()
            # Discord's attachment:// component reference must be a bare,
            # predictable filename. Preserve the true image format (including
            # animated GIFs) rather than copying unsafe mobile upload names.
            declared_type = str(getattr(upload, "content_type", "") or "").split(";", 1)[0].lower()
            content_extensions = {
                "image/gif": "gif", "image/jpeg": "jpg",
                "image/png": "png", "image/webp": "webp",
            }
            original_suffix = str(getattr(upload, "filename", "") or "").rsplit(".", 1)[-1].lower()
            suffix = content_extensions.get(declared_type)
            if suffix is None:
                suffix = original_suffix if f".{original_suffix}" in _TOKE_MEDIA_EXTENSIONS else "png"
            upload_file.filename = f"toke-{guild.id}-{member.id}-{time.time_ns()}.{suffix}"
        elif clean_media:
            # A verified direct image URL remains inline during Cheers,
            # Refresh, VC updates and terminal card edits.
            embed.set_image(url=clean_media)

        voice_id = choose_voice(guild, member, settings)
        if voice_channel_id:
            selected = guild.get_channel(int(voice_channel_id))
            if not isinstance(selected, (discord.VoiceChannel, discord.StageChannel)):
                raise ValueError("This sesh voice channel no longer exists.")
            member_access = selected.permissions_for(member)
            bot_access = selected.permissions_for(me)
            if not (member_access.view_channel and member_access.connect and bot_access.view_channel):
                raise ValueError("Both you and Dank Shield must be able to access the selected voice channel.")
            voice_id = int(selected.id)
        session_data: dict[str, Any] = {
            "guild_id": guild.id,
            "message_id": 0,
            "channel_id": channel.id,
            "starter_id": member.id,
            "starter_role_id": starter_role.id,
            "notify_role_id": notify_role.id,
            "voice_channel_id": voice_id,
            "max_interested": int(settings["max_interested"]),
            "show_member_names": bool(settings["show_member_names"]),
            "empty_vc_end_minutes": int(settings.get("empty_vc_end_minutes", 0)),
            "expires_at": time.time() + int(settings["session_minutes"]) * 60,
        }
        if upload_file is not None:
            session_data["media_layout"] = "gallery_v2"
            session_data["media_filename"] = upload_file.filename
            pending_card = {**session_data, "active": True, "cheers_ids": [], "going_ids": []}
            intro = (
                "## 💨 Toke Time\n"
                f"{notify_role.mention}\n"
                f"**{discord.utils.escape_markdown(member.display_name)}** is calling the sesh crowd."
                + (f"\n\n> {discord.utils.escape_markdown(_clean_member_message(message_text))}" if message_text else "")
            )
            # Discord V2 messages cannot mix content/embeds with layout views.
            # The single authorized role ping is inside the header TextDisplay.
            payload: dict[str, Any] = {
                "view": TokeMediaLayout(guild, pending_card, intro=intro),
                "file": upload_file,
                "allowed_mentions": _toke_allowed_mentions(notify_role),
            }
        else:
            payload = {
                "content": notify_role.mention,
                "embed": embed,
                "view": TokeCheersView(
                    member.id, starter_role.id, voice_channel_id=voice_id, guild_id=guild.id,
                ),
                "allowed_mentions": _toke_allowed_mentions(notify_role),
            }
        sent = await channel.send(**payload)
        try:
            session_data["message_id"] = sent.id
            session = await toke_sessions.save_new(session_data)
        except Exception:
            # A card that cannot track participants must not remain posted with
            # a broken button. Log and attempt to undo the posted announcement.
            try:
                await sent.delete()
            except discord.HTTPException:
                print(f"[toke] CRITICAL could not remove untracked session guild={guild.id} message={sent.id}")
            raise
        _TOKE_USER_LAST[(int(guild.id), int(member.id))] = monotonic()
        _TOKE_GUILD_LAST[int(guild.id)] = monotonic()
        if upload_file is None:
            try:
                await sent.edit(embed=render_card(guild, session, embed), view=TokeCheersView(
                    member.id, starter_role.id, guild_id=guild.id, message_id=sent.id,
                    voice_channel_id=voice_id,
                ))
            except discord.HTTPException as exc:
                print(f"[toke] initial card status edit failed guild={guild.id} message={sent.id}: {exc}")
        # No first PATCH for uploaded images; the V2 layout already shows
        # its empty attendance and its controls before the first interaction.
        return sent


async def publish_toke_from_draft(
    guild: discord.Guild | None,
    member: discord.Member,
    draft: Any,
    media_message: discord.Message | None,
) -> discord.Message:
    if guild is None or not isinstance(member, discord.Member):
        raise ValueError("Your server membership could not be resolved.")
    if int(guild.id) != int(draft.guild_id):
        raise ValueError("Your /toke draft belongs to a different server.")
    cfg = await _config(guild)
    stoner_id, ping_id = toke_role_ids(parse_community_pings(cfg), cfg)
    starter_role, notify_role = guild.get_role(stoner_id), guild.get_role(ping_id)
    if not isinstance(starter_role, discord.Role) or not isinstance(notify_role, discord.Role):
        raise ValueError("The Toke roles need to be repaired in Community & Pings.")
    if not _member_has_role_id(member, stoner_id):
        raise ValueError("You no longer have permission to start /toke.")
    channel = guild.get_channel(int(draft.destination_id))
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Your configured Toke destination has been removed.")

    clean_media = ""
    upload = None
    if media_message is not None:
        found = media_url_from_message(media_message, allow_public_page=True)
        # Prefer a provider-hosted public image when Discord's GIF picker
        # supplied one; it can stay *inside* the embed without a local upload
        # and without leaving a duplicate native attachment on edits.
        if found and _is_stable_provider_media_url(found):
            clean_media = _clean_media_url(found)
        else:
            for attachment in media_message.attachments:
                if _attachment_is_supported_media(attachment):
                    upload = attachment
                    break
            if upload is None and found:
                if _is_temporary_discord_media_url(found):
                    raise ValueError(
                        "Discord's temporary media preview cannot survive deleting your draft. "
                        "Upload the GIF/image or share its original public URL."
                    )
                clean_media = _clean_media_url(found)
        if upload is None and not clean_media:
            raise ValueError("No supported GIF or picture could be read. Check Message Content Intent.")

    return await _publish_toke(
        guild=guild, member=member, channel=channel, starter_role=starter_role,
        notify_role=notify_role, message_text=draft.note,
        clean_media=clean_media, upload=upload, settings=settings_from_config(cfg),
        voice_channel_id=int(getattr(draft, "voice_channel_id", 0) or 0),
    )


@app_commands.describe(
    message="Optional short message to include with the sesh ping.",
    media="Optional image/GIF URL (Discord CDN, direct media, Tenor/Giphy share link).",
    upload="Optional PNG/JPG/GIF/WEBP upload to place on the Toke card.",
    voice="Optional voice room for this session; otherwise use your current/default VC.",
)
async def open_toke_command(
    interaction: discord.Interaction,
    message: Optional[str] = None,
    media: Optional[str] = None,
    upload: Optional[discord.Attachment] = None,
    voice: Optional[discord.VoiceChannel] = None,
    *,
    quick_text_only: bool = False,
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

    settings = settings_from_config(cfg)
    remaining, label = await _persistent_cooldown_remaining(guild.id, member.id, settings)
    if remaining > 0:
        return await _reply(interaction, f"Wait {_format_wait(remaining)} for {label}.")

    clean_message = _clean_member_message(message)
    if upload is None and not clean_media and not quick_text_only:
        if not bool(getattr(interaction.client.intents, "message_content", False)):
            return await _reply(interaction, "Dank Shield needs the Discord Message Content intent to capture normal GIFs. Use /toke upload or media instead.")
        source = interaction.channel
        if not isinstance(source, discord.TextChannel):
            return await _reply(interaction, "Use /toke in a regular text channel to send your GIF normally.")
        source_perms = source.permissions_for(me)
        if not (source_perms.view_channel and source_perms.read_message_history and source_perms.manage_messages):
            return await _reply(
                interaction,
                "Dank Shield needs View Channel, Read Message History and Manage Messages here "
                "to capture and clean up a temporary GIF. Ask staff to grant these or use /toke upload: or media:.",
            )
        draft = pending_for(guild.id, member.id)
        if draft is not None:
            return await _reply(interaction, "You already have a Toke draft waiting for a GIF. Use its Post Without Media or Cancel button.")
        compose = begin_capture(
            guild.id, member.id, source.id, channel.id, clean_message,
            voice_channel_id=int(voice.id) if voice is not None else 0,
        )
        return await interaction.followup.send(
            "💨 **Toke is ready.** Send a GIF or picture using Discord's normal message box "
            "in this channel within 90 seconds. Dank Shield will create the sesh card and "
            "remove your temporary GIF message only after it posts successfully. "
            "Or tap **Post Without Media**.",
            view=compose, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    try:
        sent = await _publish_toke(
            guild=guild, member=member, channel=channel, starter_role=stoner_role,
            notify_role=ping_role, message_text=clean_message,
            clean_media=clean_media, upload=upload, settings=settings,
            voice_channel_id=int(voice.id) if voice is not None else 0,
        )
    except (ValueError, discord.HTTPException, OSError) as exc:
        return await _reply(interaction, f"Toke was not sent: {type(exc).__name__}: {exc}")
    await interaction.followup.send(
        f"💨 Toke call sent in {channel.mention}: {sent.jump_url}",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )


__all__ = [
    "SESH_PING_ROLE_KEY",
    "STONER_ROLE_KEY",
    "TOKE_CHANNEL_KEY",
    "TOKE_GUILD_COOLDOWN_SECONDS",
    "TOKE_USER_COOLDOWN_SECONDS",
    "TokeCheersView",
    "publish_toke_from_draft",
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
