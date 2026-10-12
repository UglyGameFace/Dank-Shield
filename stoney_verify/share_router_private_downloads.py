from __future__ import annotations

"""Opt-in Share Router video download-to-DM, through its one message owner.

A download request is a member's explicit public URL in a configured channel.
No unsolicited bulk DMs, no persistent media cache, and no upload-size tricks.
Anything beyond the bot's DM limit is a *link*, not a disguised attachment.
"""

import asyncio
import time
from pathlib import Path
from typing import Any

import discord

from .share_router_media_network import is_safe_media_download_url
from .share_router_media_resolver import (
    MEDIA_URL_RE,
    canonicalize_media_url,
    provider_for_url,
    resolve_first_media,
)
from .share_router_media_remux import remux_media_for_discord

# The bot's DM is outside any boosted guild: Level 3's 100 MiB limit does NOT apply.
# Leave headroom below the documented 20 MiB default for API transport overhead.
DM_ATTACHMENT_BYTES = 19 * 1024 * 1024
MAX_ACTIVE_DM_DOWNLOADS = 2
MEMBER_COOLDOWN_SECONDS = 120
MAX_COOLDOWN_RECORDS = 2048
_DOWNLOAD_SLOTS: asyncio.Semaphore | None = None
_DOWNLOAD_LOOP: asyncio.AbstractEventLoop | None = None
_BUSY: set[tuple[int, int]] = set()
_LAST_REQUEST: dict[tuple[int, int], float] = {}


def _gate() -> asyncio.Semaphore:
    global _DOWNLOAD_SLOTS, _DOWNLOAD_LOOP
    loop = asyncio.get_running_loop()
    if _DOWNLOAD_LOOP is not loop:
        _DOWNLOAD_LOOP = loop
        _DOWNLOAD_SLOTS = asyncio.Semaphore(MAX_ACTIVE_DM_DOWNLOADS)
        _BUSY.clear()
        _LAST_REQUEST.clear()
    assert _DOWNLOAD_SLOTS is not None
    return _DOWNLOAD_SLOTS


def _valid_request_url(content: str) -> str:
    urls = MEDIA_URL_RE.findall(content or "")
    if len(urls) != 1:
        return ""
    url = canonicalize_media_url(urls[0])
    if len(url) > 1400 or not is_safe_media_download_url(url) or not provider_for_url(url):
        return ""
    return url


def _prune_cooldowns(now: float) -> None:
    for key, stamp in list(_LAST_REQUEST.items()):
        if now - stamp >= MEMBER_COOLDOWN_SECONDS:
            _LAST_REQUEST.pop(key, None)
    if len(_LAST_REQUEST) > MAX_COOLDOWN_RECORDS:
        for key, _ in sorted(_LAST_REQUEST.items(), key=lambda row: row[1])[
            :len(_LAST_REQUEST) - MAX_COOLDOWN_RECORDS
        ]:
            _LAST_REQUEST.pop(key, None)


async def _reply_problem(message: discord.Message, text: str) -> None:
    # No user/content mention; do not expose signed URLs or full exception text.
    try:
        await message.reply(
            text,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        pass


async def handle_download_message(message: discord.Message) -> bool:
    """Process a request in the chosen download channel. Returns handled flag."""
    if message.guild is None or not isinstance(message.author, discord.Member):
        return False
    if message.author.bot or getattr(message, "webhook_id", None):
        return False

    channel = message.channel
    try:
        perms = channel.permissions_for(message.author)
        if not perms.view_channel or not perms.send_messages:
            return True
        if channel.is_nsfw():
            await _reply_problem(message, "This download channel must not be age-restricted.")
            return True
    except Exception:
        return True

    _gate()  # Initialize per-event-loop limits BEFORE tracking this user.
    key = (int(message.guild.id), int(message.author.id))
    now = time.monotonic()
    _prune_cooldowns(now)
    if key in _BUSY:
        # Silently ignore duplicate active posts instead of spamming the intake.
        return True
    if now - _LAST_REQUEST.get(key, -1e12) < MEMBER_COOLDOWN_SECONDS:
        return True
    # Invalid requests count toward the cooldown too, or a malicious member
    # could make the bot generate an unlimited stream of public error replies.
    _LAST_REQUEST[key] = now
    url = _valid_request_url(message.content)
    if not url:
        await _reply_problem(
            message,
            "Send one safe public video link per message. Private or unsupported links cannot be downloaded.",
        )
        return True
    # A semaphore alone queues unlimited waiters. Reject overflow BEFORE
    # spawning a network transfer to keep bot memory and latency bounded.
    if len(_BUSY) >= MAX_ACTIVE_DM_DOWNLOADS:
        await _reply_problem(message, "Download capacity is busy. Try again in two minutes.")
        return True
    _BUSY.add(key)
    try:
        async with _gate():
            # Resolve only one URL and never buffer full videos in memory.
            resolution = await resolve_first_media(url, max_bytes=DM_ATTACHMENT_BYTES)
            if resolution is None:
                await _reply_problem(message, "This video source is not currently supported.")
                return True

            # Reuse the canonical Share Router downloader. No separate media
            # provider implementation and no unbounded torrent download path.
            from .share_router_runtime import _download_trusted_video, RoutedVideo

            video: Any = None
            if resolution.progressive:
                video = await _download_trusted_video(
                    resolution.media_url,
                    max_bytes=DM_ATTACHMENT_BYTES,
                    request_headers=dict(resolution.request_headers),
                )
            elif resolution.delivery in {"manifest", "merge"}:
                staged = await remux_media_for_discord(
                    resolution, max_bytes=DM_ATTACHMENT_BYTES,
                )
                if staged is not None:
                    try:
                        f = discord.File(str(staged.path), filename=staged.filename)
                        video = RoutedVideo(
                            file=f,
                            source_url=resolution.canonical_url,
                            size_bytes=staged.size_bytes,
                            cleanup_path=staged.path,
                        )
                    except Exception:
                        staged.cleanup()

            try:
                if video is not None:
                    try:
                        try:
                            await message.author.send(
                                "🎬 Your video from the Share Router Download channel:",
                                file=video.file,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        except discord.HTTPException as exc:
                            # API-side upload caps can change independently of
                            # what our installed Discord client library knows.
                            # Never claim the file was sent if Discord rejects it.
                            if getattr(exc, "status", None) != 413 and getattr(exc, "code", None) != 40005:
                                raise
                            await message.author.send(
                                "🔗 Discord rejected this video attachment as too large. "
                                "The original public source is here instead:\n"
                                + resolution.canonical_url
                                + "\n\nThis is a link, not a downloaded file.",
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                    finally:
                        try:
                            video.file.close()
                        except Exception:
                            pass
                        path = getattr(video, "cleanup_path", None)
                        if path:
                            Path(path).unlink(missing_ok=True)
                else:
                    # A DM URL is not a DM attachment. Never imply an oversized
                    # file was actually uploaded or that a provider link is
                    # guaranteed to offer direct downloads.
                    await message.author.send(
                        "🔗 I couldn't attach this video within Discord's DM "
                        "size/format limits. You can open the original source "
                        "in your browser:\n"
                        + resolution.canonical_url
                        + "\n\nThis is a link, not a downloaded video file. "
                        "A private large-file download service isn't configured yet.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except discord.Forbidden:
                await _reply_problem(
                    message,
                    "I couldn't DM you. Allow messages from the bot and retry after "
                    "the cooldown; your original link has been kept.",
                )
                return True
            except discord.HTTPException:
                await _reply_problem(
                    message,
                    "Discord couldn't deliver this download. The source message "
                    "was kept so you can retry after the cooldown.",
                )
                return True

            # Only delete a submission once a DM was actually delivered.
            # Do not erase it if Manage Messages is unavailable.
            try:
                me = message.guild.me
                if me is not None and channel.permissions_for(me).manage_messages:
                    await message.delete()
            except discord.HTTPException:
                pass
            return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ share_router private download unavailable guild={message.guild.id} error={type(exc).__name__}")
        await _reply_problem(message, "This download failed safely. The original link has been kept.")
        return True
    finally:
        _BUSY.discard(key)


def reset_download_state_for_tests() -> None:
    global _DOWNLOAD_LOOP, _DOWNLOAD_SLOTS
    _DOWNLOAD_LOOP = None
    _DOWNLOAD_SLOTS = None
    _BUSY.clear()
    _LAST_REQUEST.clear()
