from __future__ import annotations

"""Per-guild Share Router download delivery, through its one message owner.

A member submits one public video URL in a staff-configured intake channel.
Staff explicitly choose private DM, a permitted server channel, or both.
Legacy active download channels retain their prior DM-only behavior.
No bulk unsolicited DMs, unbounded file cache, or upload-size tricks.
"""

import asyncio
import time
from pathlib import Path
from typing import Any

import discord

from .share_router_media_network import is_safe_media_download_url
from .share_router_media_titles import title_from_embeds, title_from_filename, title_from_media_url, title_prefix
from .share_router_media_resolver import (
    MEDIA_URL_RE,
    MediaResolution,
    canonicalize_media_url,
    provider_for_url,
    provider_label,
    resolve_first_media,
)
from .share_router_media_remux import remux_media_for_discord
from .share_router_external_links import delivery_status, file_max_bytes, stage_video, revoke_video

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


_PUBLIC_FAILURE_TEXT = {
    "extract_failed": "The provider did not return usable video information.",
    "extractor_unavailable": "The media extractor is not installed or could not start.",
    "provider_bot_verification": "The provider requested bot verification from the download server.",
    "provider_private_or_restricted": "The provider has restricted this video.",
    "provider_login_required": "The provider requires sign-in or verification.",
    "provider_rate_limited": "The provider is limiting requests from this server.",
    "provider_forbidden": "The media provider refused the server's request.",
    "provider_video_unavailable": "The video is unavailable from the provider.",
    "provider_extractor_incompatible": "The provider changed its page format; the current media extractor could not parse it.",
    "provider_no_downloadable_formats": "The provider returned no downloadable video formats for this post.",
    "provider_extract_timeout": "The provider did not return video information before the timeout.",
    "no_safe_progressive_format": "No supported downloadable format could be selected within the current limits.",
    "separate_audio_video_requires_merge": "Separate audio and video tracks could not be prepared.",
    "fragmented_media_requires_controlled_download": "The provider returned fragmented media that could not be prepared.",
    "live_stream_requires_player": "Live video requires an external player.",
    "provider_disabled": "This provider is disabled by the bot's media policy.",
    "unsupported_provider": "This provider is not supported.",
    "media_transfer_failed": "The selected video could not be fetched within the transfer constraints.",
    "media_remux_failed": "The selected video could not be assembled into a compatible file.",
    "ffmpeg_unavailable": "The server's FFmpeg media processor is unavailable.",
}


def download_failure_message(
    resolution: MediaResolution, *,
    reason: str,
    hosting_ready: bool,
    hosting_reason: str,
) -> str:
    """Only stable public reason codes; never expose extractor errors or CDN tokens."""
    category = reason if reason in _PUBLIC_FAILURE_TEXT else "extract_failed"
    message = (
        "⚠️ Dank Shield could not prepare a video file.\n"
        f"Source: {provider_label(resolution.provider)}\n"
        f"Reason: {_PUBLIC_FAILURE_TEXT[category]} (`{category}`)\n\n"
        "No video was downloaded or hosted. "
        "This is the original source, not a downloadable attachment:\n"
        + resolution.canonical_url
    )
    if not hosting_ready:
        message += (
            "\n\nLarge-file hosting is also unavailable: "
            + (hosting_reason or "Not ready")
            + ". Staff can check Share Router → Download Settings → Test HTTPS Hosting."
        )
    else:
        message += (
            "\n\nLarge-file hosting is ready locally, but no media file "
            "was available to stage. Staff can check Share Router → Test Provider."
        )
    return message


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

    from .share_router_runtime import guild_download_delivery, is_share_router_design_resource
    delivery_mode, destination_id = await guild_download_delivery(int(message.guild.id))
    if delivery_mode == "off":
        return True

    destination = None
    if delivery_mode in {"channel", "both"}:
        destination = message.guild.get_channel(destination_id)
        if (
            not isinstance(destination, discord.TextChannel)
            or int(destination.id) == int(channel.id)
            or getattr(destination.guild, "id", None) != int(message.guild.id)
            or is_share_router_design_resource(destination)
        ):
            await _reply_problem(message, "The chosen video destination is missing or unsafe. The link was kept.")
            return True
        try:
            sender_access = destination.permissions_for(message.author)
            bot = message.guild.me
            bot_access = destination.permissions_for(bot) if bot is not None else None
            if (
                destination.is_nsfw()
                or not sender_access.view_channel or not sender_access.send_messages
                or bot_access is None or not bot_access.view_channel
                or not bot_access.send_messages or not bot_access.attach_files
            ):
                await _reply_problem(message, "The video destination is unavailable or its permissions changed. The link was kept.")
                return True
        except Exception:
            return True

    async def send_delivery(text: str, *, file: Any = None) -> bool:
        recipients = []
        if delivery_mode in {"dm", "both"}:
            recipients.append(message.author)
        if destination is not None:
            recipients.append(destination)
        successful = 0
        errors: list[discord.HTTPException] = []
        for index, recipient in enumerate(recipients):
            try:
                if file is not None and index:
                    if hasattr(file, "reset"):
                        file.reset(seek=True)
                    else:
                        file.fp.seek(0)
                kwargs: dict[str, Any] = {
                    "allowed_mentions": discord.AllowedMentions.none(),
                }
                if file is not None:
                    kwargs["file"] = file
                await recipient.send(text, **kwargs)
                successful += 1
            except discord.HTTPException as exc:
                errors.append(exc)
        if errors and not successful:
            raise errors[0]
        if errors:
            await _reply_problem(
                message,
                "The video reached only some chosen destinations. "
                "The original link was kept; check Discord channel and DM permissions.",
            )
        return not errors

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
            # Only prepare larger media when HTTPS hosting and bounded storage
            # are operational. A disabled host retains the original DM flow.
            hosting_ready, hosting_reason = delivery_status()
            download_cap = file_max_bytes() if hosting_ready else DM_ATTACHMENT_BYTES
            # Facebook may expose direct media in Discord's embed even if the
            # pinned page extractor cannot parse this public Reel/Watch post.
            # Reuse the existing guarded and size-bounded routing download path.
            from .share_router_runtime import (
                _download_resolved_progressive,
                _download_trusted_video,
                _video_source_urls,
                RoutedVideo,
            )

            video: Any = None
            resolution: MediaResolution | None = None
            if provider_for_url(url) == "facebook":
                # Try at most the Discord proxy and original CDN media URL.
                # Unrelated attachments must never substitute for this post.
                for embedded_url in _video_source_urls(message, embeds_only=True)[:2]:
                    video = await _download_trusted_video(
                        embedded_url, max_bytes=download_cap,
                    )
                    if video is not None:
                        resolution = MediaResolution(
                            source_url=url,
                            canonical_url=url,
                            identity=f"facebook:{url}",
                            provider="facebook",
                            delivery="progressive",
                            media_url=embedded_url,
                        )
                        break

            if resolution is None:
                resolution = await resolve_first_media(url, max_bytes=download_cap)
                if resolution is None:
                    await _reply_problem(message, "This video source is not currently supported.")
                    return True

            failure_reason = resolution.reason or "extract_failed"
            if video is None and resolution.progressive:
                video = await _download_resolved_progressive(
                    resolution,
                    max_bytes=download_cap,
                )
                if video is None:
                    failure_reason = "media_transfer_failed"
            elif video is None and resolution.delivery in {"manifest", "merge"}:
                # Distinguish a missing FFmpeg executable from provider metadata
                # failure; do not speculate about codec restrictions.
                import shutil
                failure_reason = (
                    "ffmpeg_unavailable" if not shutil.which("ffmpeg")
                    else "media_remux_failed"
                )
                staged = await remux_media_for_discord(
                    resolution, max_bytes=download_cap,
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

            delivered_file_or_hosted_link = False
            try:
                if video is not None:
                    video_title = (
                        resolution.title
                        or title_from_embeds(message, url)
                        or (title_from_media_url(resolution.source_url) if resolution.provider == "direct" else "")
                        or title_from_filename(getattr(video.file, "filename", ""))
                    )
                    prefix = title_prefix(video_title)
                    stored_id = ""
                    try:
                        use_host = video.size_bytes > DM_ATTACHMENT_BYTES and hosting_ready
                        if not use_host:
                            try:
                                delivered_file_or_hosted_link = await send_delivery(
                                    prefix + "🎬 Video shared with Dank Shield:",
                                    file=video.file,
                                )
                            except discord.HTTPException as exc:
                                # Discord can reject uploads regardless of the
                                # advertised maximum; retry as hosted link only
                                # if an approved public host is operational.
                                if getattr(exc, "status", None) != 413 and getattr(exc, "code", None) != 40005:
                                    raise
                                use_host = hosting_ready
                                if not use_host:
                                    await send_delivery(
                                        "🔗 Discord rejected this video attachment as too large. "
                                        "The original public source is here instead:\n"
                                        + resolution.canonical_url
                                        + "\n\nThis is a link, not a downloaded file.",
                                    )

                        if use_host:
                            from .share_router_runtime import guild_download_expiry_seconds
                            ttl = await guild_download_expiry_seconds(int(message.guild.id))
                            name = str(getattr(video.file, "filename", "") or "").lower()
                            ext = next(
                                (extension for extension in (".mp4", ".webm", ".mov", ".gif") if name.endswith(extension)),
                                ".mp4",
                            )
                            staged_link = await stage_video(video.file.fp, ttl_seconds=ttl, ext=ext)
                            if staged_link is None:
                                await send_delivery(
                                    "🔗 This video could not be hosted within the storage/size limit. "
                                    "The original public source is available here:\n"
                                    + resolution.canonical_url,
                                )
                            else:
                                watch_url, download_url, stored_id = staged_link
                                try:
                                    delivered_file_or_hosted_link = await send_delivery(
                                        prefix + "🎬 Your video is ready.\n"
                                        f"▶️ Watch: {watch_url}\n"
                                        f"📥 Download: {download_url}\n"
                                        f"⏳ Both links expire in {ttl // 86400} day(s)."
                                        if ttl >= 86400 else
                                        prefix + "🎬 Your video is ready.\n"
                                        f"▶️ Watch: {watch_url}\n"
                                        f"📥 Download: {download_url}\n"
                                        "⏳ Both links expire in 1 hour.",
                                    )
                                except Exception:
                                    await revoke_video(stored_id)
                                    raise
                    finally:
                        try:
                            video.file.close()
                        except Exception:
                            pass
                        path = getattr(video, "cleanup_path", None)
                        if path:
                            Path(path).unlink(missing_ok=True)
                else:
                    # Nothing was downloaded or staged. Name the actual failure
                    # stage, not Discord's DM upload limit or hypothetical config.
                    await send_delivery(
                        download_failure_message(
                            resolution,
                            reason=failure_reason,
                            hosting_ready=hosting_ready,
                            hosting_reason=hosting_reason,
                        ),
                    )
            except discord.Forbidden:
                await _reply_problem(
                    message,
                    ("I couldn't DM you. Allow messages from the bot and retry after "
                     "the cooldown; your original link has been kept."
                     if delivery_mode == "dm" else
                     "I couldn't send to the selected destination. Check its permissions; "
                     "your original link was kept."),
                )
                return True
            except discord.HTTPException:
                await _reply_problem(
                    message,
                    "Discord couldn't deliver this download. The source message "
                    "was kept so you can retry after the cooldown.",
                )
                return True

            # Do not delete the original source if only an error/source link
            # was delivered, or either selected destination failed.
            # Only actual attachments or staged hosted links count as delivery.
            if not delivered_file_or_hosted_link:
                return True
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
