from __future__ import annotations

import asyncio
import html
import io
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from .. import globals as app_globals
from ..globals import bot, now_utc
from .service import attach_transcript_to_ticket, mark_ticket_deleted

try:
    from .repository import get_ticket_by_any_channel_id
except Exception:
    async def get_ticket_by_any_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None


# ============================================================
# tickets_new/transcript_service.py
# ------------------------------------------------------------
# P0 hardening goals:
# - no delete/transcript service can hang forever
# - every lock acquisition has a timeout
# - transcript posting has a timeout
# - DB metadata writes cannot block channel deletion forever
# - channel.delete() is always attempted unless Discord blocks it
# - every public service returns a dict/tuple instead of silently dying
#
# Multi-guild hardening goals:
# - transcript channel resolution must be guild-aware
# - never fetch/post to a transcript channel that belongs to another guild
# - support per-guild transcript channel overrides
# - safe fallback to a same-guild transcripts channel by name
# ============================================================

_DELETE_LOCKS: Dict[str, asyncio.Lock] = {}
_TRANSCRIPT_POST_LOCKS: Dict[str, asyncio.Lock] = {}
_LOCK_LAST_USED: Dict[str, float] = {}
_LOCK_CLEANUP_INTERVAL_SECONDS = 600.0
_LAST_LOCK_CLEANUP_AT = 0.0

_TRANSCRIPT_MARKER = "stoney_verify:transcript_posted:v9"

DEFAULT_LOCK_TIMEOUT_SECONDS = 3.0
DEFAULT_HISTORY_TIMEOUT_SECONDS = 15.0
DEFAULT_TRANSCRIPT_POST_TIMEOUT_SECONDS = 18.0
DEFAULT_DB_WRITE_TIMEOUT_SECONDS = 6.0
DEFAULT_CHANNEL_DELETE_TIMEOUT_SECONDS = 8.0

DEFAULT_TRANSCRIPT_MESSAGE_LIMIT = 2500


# ============================================================
# Logging / safe helpers
# ============================================================

def _debug(msg: str) -> None:
    try:
        print(f"🧾 transcript_service {msg}")
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        print(f"⚠️ transcript_service {msg}")
    except Exception:
        pass


def _error(msg: str) -> None:
    try:
        print(f"❌ transcript_service {msg}")
    except Exception:
        pass


def _safe_text(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return dt.isoformat()
        except Exception:
            return None


def _safe_filename(name: str) -> str:
    cleaned: List[str] = []
    for ch in _safe_text(name):
        if ch.isalnum() or ch in {"-", "_", "."}:
            cleaned.append(ch)
        else:
            cleaned.append("-")
    out = "".join(cleaned).strip("-")
    return out[:90] if out else "transcript"


def _truncate_for_discord(text: str, limit: int = 1900) -> str:
    safe = _safe_text(text)
    if len(safe) <= limit:
        return safe
    return safe[: max(0, limit - 3)] + "..."


def _env_int(name: str, default: int = 0) -> int:
    try:
        value = getattr(app_globals, name, None)
        if value is None:
            value = globals().get(name)
        return _safe_int(value, default)
    except Exception:
        return default


def _env_guild_override_int(base_name: str, guild_id: int, default: int = 0) -> int:
    """
    Supports several env naming patterns without breaking existing single-guild envs:
      - TRANSCRIPTS_CHANNEL_ID_<guild_id>
      - TRANSCRIPTS_CHANNEL_ID__<guild_id>
      - GUILD_<guild_id>_TRANSCRIPTS_CHANNEL_ID
    """
    try:
        gid = int(guild_id or 0)
    except Exception:
        gid = 0

    if gid <= 0:
        return default

    candidates = (
        f"{base_name}_{gid}",
        f"{base_name}__{gid}",
        f"GUILD_{gid}_{base_name}",
    )

    for key in candidates:
        raw = os.getenv(key, "")
        val = _safe_int(raw, 0)
        if val > 0:
            return val

    return default


def _actor_id(actor: Optional[discord.abc.User]) -> Optional[int]:
    try:
        return int(actor.id) if actor is not None else None
    except Exception:
        return None


def _actor_name(actor: Optional[discord.abc.User]) -> Optional[str]:
    try:
        return str(actor) if actor is not None else None
    except Exception:
        return None


def _message_author_label(message: discord.Message) -> str:
    try:
        author = message.author
        display = getattr(author, "display_name", None) or getattr(author, "name", None) or str(author)
        return f"{display} ({getattr(author, 'id', 'unknown')})"
    except Exception:
        return "Unknown"


def _collect_attachment_urls(message: discord.Message) -> List[str]:
    urls: List[str] = []
    try:
        for attachment in message.attachments:
            try:
                if attachment.url:
                    urls.append(str(attachment.url))
            except Exception:
                continue
    except Exception:
        pass
    return urls


def _collect_sticker_names(message: discord.Message) -> List[str]:
    names: List[str] = []
    try:
        for sticker in getattr(message, "stickers", []) or []:
            try:
                name = _safe_text(getattr(sticker, "name", "")).strip()
                if name:
                    names.append(name)
            except Exception:
                continue
    except Exception:
        pass
    return names


def _render_embed_summary_text(message: discord.Message) -> str:
    try:
        embeds = getattr(message, "embeds", None) or []
        if not embeds:
            return ""

        chunks: List[str] = []
        for index, embed in enumerate(embeds, start=1):
            section: List[str] = [f"[embed {index}]"]

            try:
                title = _safe_text(getattr(embed, "title", "") or "").strip()
                description = _safe_text(getattr(embed, "description", "") or "").strip()
                url = _safe_text(getattr(embed, "url", "") or "").strip()

                if title:
                    section.append(f"title={title}")
                if description:
                    section.append(f"description={description}")
                if url:
                    section.append(f"url={url}")

                for field in getattr(embed, "fields", []) or []:
                    try:
                        fname = _safe_text(getattr(field, "name", "") or "").strip()
                        fvalue = _safe_text(getattr(field, "value", "") or "").strip()
                        if fname or fvalue:
                            section.append(f"field={fname}: {fvalue}")
                    except Exception:
                        continue
            except Exception:
                pass

            chunks.append("\n".join(section))

        return "\n".join(chunks)
    except Exception:
        return ""


# ============================================================
# Multi-guild transcript channel resolution
# ============================================================

def _same_guild_channel(candidate: Any, guild: discord.Guild) -> bool:
    try:
        return (
            isinstance(candidate, discord.TextChannel)
            and getattr(candidate, "guild", None) is not None
            and int(candidate.guild.id) == int(guild.id)
        )
    except Exception:
        return False


def _candidate_transcripts_channel_ids(
    guild: discord.Guild,
    ticket_row: Optional[Dict[str, Any]] = None,
) -> List[int]:
    out: List[int] = []
    seen: set[int] = set()

    def _push(value: Any) -> None:
        cid = _safe_int(value, 0)
        if cid > 0 and cid not in seen:
            seen.add(cid)
            out.append(cid)

    # Row-level transcript channel metadata wins first if it already exists.
    if isinstance(ticket_row, dict):
        _push(ticket_row.get("transcript_channel_id"))
        _push(ticket_row.get("transcripts_channel_id"))

    # Per-guild env override(s).
    _push(_env_guild_override_int("TRANSCRIPTS_CHANNEL_ID", int(guild.id), 0))

    # Legacy/global env fallback.
    _push(_env_int("TRANSCRIPTS_CHANNEL_ID", 0))

    return out


def _find_same_guild_transcripts_channel_by_name(guild: discord.Guild) -> Optional[discord.TextChannel]:
    exact_names = {
        "transcripts",
        "ticket-transcripts",
        "ticket_transcripts",
        "support-transcripts",
        "archive-transcripts",
    }

    contains_terms = (
        "transcript",
        "tickets-log",
        "ticket-log",
    )

    try:
        # Exact match first.
        for ch in guild.text_channels:
            name = _safe_text(getattr(ch, "name", "")).strip().lower()
            if name in exact_names:
                return ch
    except Exception:
        pass

    try:
        # Then fuzzy contains.
        for ch in guild.text_channels:
            name = _safe_text(getattr(ch, "name", "")).strip().lower()
            if any(term in name for term in contains_terms):
                return ch
    except Exception:
        pass

    return None


async def _resolve_same_guild_text_channel_by_id(
    guild: discord.Guild,
    channel_id: int,
) -> Optional[discord.TextChannel]:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return None

    # First: check guild-local cache.
    try:
        ch = guild.get_channel(cid)
        if _same_guild_channel(ch, guild):
            return ch
    except Exception:
        pass

    # Second: check bot-wide cache to detect foreign-guild leakage early.
    try:
        bot_cached = bot.get_channel(cid)
        if isinstance(bot_cached, discord.TextChannel):
            if int(bot_cached.guild.id) != int(guild.id):
                _warn(
                    f"configured transcript channel id={cid} belongs to a different guild "
                    f"expected_guild={guild.id} actual_guild={bot_cached.guild.id}"
                )
                return None
            return bot_cached
    except Exception:
        pass

    # Third: fetch through the guild, but treat foreign-guild resolution as invalid.
    try:
        fetched = await asyncio.wait_for(
            guild.fetch_channel(cid),
            timeout=DEFAULT_CHANNEL_DELETE_TIMEOUT_SECONDS,
        )
        if _same_guild_channel(fetched, guild):
            return fetched
        if isinstance(fetched, discord.TextChannel):
            _warn(
                f"fetched transcript channel id={cid} but it resolved to a different guild "
                f"expected_guild={guild.id} actual_guild={getattr(getattr(fetched, 'guild', None), 'id', 'unknown')}"
            )
        return None
    except discord.InvalidData as e:
        _warn(
            f"transcript channel id={cid} invalid for guild={guild.id}; "
            f"likely belongs to a different guild error={repr(e)}"
        )
        return None
    except discord.NotFound:
        return None
    except asyncio.TimeoutError:
        _warn(f"timeout fetching transcript channel id={cid} guild={guild.id}")
        return None
    except Exception as e:
        _warn(f"failed to fetch transcript channel id={cid} guild={guild.id} error={repr(e)}")
        return None


# ============================================================
# Lock helpers
# ============================================================

def _touch_lock_key(key: str) -> None:
    try:
        _LOCK_LAST_USED[str(key)] = time.monotonic()
    except Exception:
        pass


def _cleanup_stale_locks_if_needed() -> None:
    global _LAST_LOCK_CLEANUP_AT

    now_mono = time.monotonic()
    if (now_mono - _LAST_LOCK_CLEANUP_AT) < _LOCK_CLEANUP_INTERVAL_SECONDS:
        return

    _LAST_LOCK_CLEANUP_AT = now_mono

    try:
        for bucket_name, bucket in (
            ("delete", _DELETE_LOCKS),
            ("transcript", _TRANSCRIPT_POST_LOCKS),
        ):
            for channel_key, lock in list(bucket.items()):
                key = f"{bucket_name}:{channel_key}"
                if lock.locked():
                    continue
                last_used = float(_LOCK_LAST_USED.get(key, 0.0) or 0.0)
                if last_used > 0.0 and (now_mono - last_used) > _LOCK_CLEANUP_INTERVAL_SECONDS:
                    bucket.pop(channel_key, None)
                    _LOCK_LAST_USED.pop(key, None)
    except Exception:
        pass


def _channel_lock(bucket: Dict[str, asyncio.Lock], channel_id: int | str, bucket_name: str) -> asyncio.Lock:
    _cleanup_stale_locks_if_needed()
    key = str(channel_id)
    lock = bucket.get(key)
    if lock is None:
        lock = asyncio.Lock()
        bucket[key] = lock
    _touch_lock_key(f"{bucket_name}:{key}")
    return lock


def _delete_lock(channel_id: int | str) -> asyncio.Lock:
    return _channel_lock(_DELETE_LOCKS, channel_id, "delete")


def _transcript_post_lock(channel_id: int | str) -> asyncio.Lock:
    return _channel_lock(_TRANSCRIPT_POST_LOCKS, channel_id, "transcript")


async def _acquire_lock_with_timeout(
    lock: asyncio.Lock,
    *,
    timeout: float,
    label: str,
    channel_id: int | str,
) -> bool:
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        _warn(f"{label} lock timeout channel={channel_id} timeout={timeout}s")
        return False
    except Exception as e:
        _warn(f"{label} lock acquire failed channel={channel_id} error={repr(e)}")
        return False


# ============================================================
# Ticket row / archive helpers
# ============================================================

async def _ticket_row(channel_id: int | str) -> Optional[Dict[str, Any]]:
    try:
        row = await asyncio.wait_for(
            get_ticket_by_any_channel_id(channel_id),
            timeout=DEFAULT_DB_WRITE_TIMEOUT_SECONDS,
        )
        if isinstance(row, dict):
            return row
    except asyncio.TimeoutError:
        _warn(f"ticket row lookup timeout channel={channel_id}")
    except Exception as e:
        _warn(f"ticket row lookup failed channel={channel_id} error={repr(e)}")
    return None


def _row_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = str((row or {}).get("status") or "").strip().lower()
        if raw in {"open", "claimed", "closed", "deleted"}:
            return raw
        if raw in {"active", "reopened"}:
            return "open"
        return raw
    except Exception:
        return ""


def _row_has_transcript(row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(row, dict):
        return False
    try:
        return bool(
            str(row.get("transcript_url") or "").strip()
            or str(row.get("transcript_message_id") or "").strip()
            or str(row.get("transcript_channel_id") or "").strip()
        )
    except Exception:
        return False


def _jump_url_from_ids(*, guild_id: int, channel_id: Optional[int], message_id: Optional[int]) -> Optional[str]:
    try:
        gid = int(guild_id or 0)
        cid = int(channel_id or 0)
        mid = int(message_id or 0)
        if gid <= 0 or cid <= 0 or mid <= 0:
            return None
        return f"https://discord.com/channels/{gid}/{cid}/{mid}"
    except Exception:
        return None


def _row_transcript_payload(
    row: Optional[Dict[str, Any]],
    *,
    guild_id: Optional[int] = None,
) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    if not isinstance(row, dict):
        return None, None, None

    url: Optional[str]
    msg_id: Optional[int]
    ch_id: Optional[int]

    try:
        url = _safe_text(row.get("transcript_url") or "").strip() or None
    except Exception:
        url = None

    try:
        msg_id = _safe_int(row.get("transcript_message_id"), 0) or None
    except Exception:
        msg_id = None

    try:
        ch_id = _safe_int(row.get("transcript_channel_id"), 0) or None
    except Exception:
        ch_id = None

    if not url and guild_id and ch_id and msg_id:
        url = _jump_url_from_ids(guild_id=int(guild_id), channel_id=ch_id, message_id=msg_id)

    return url, msg_id, ch_id


def _detect_ticket_number(name: str) -> Optional[str]:
    try:
        m = re.search(r"(\d{3,})$", str(name or ""))
        return m.group(1) if m else None
    except Exception:
        return None


def _transcript_basename(channel: discord.TextChannel, ticket_row: Optional[Dict[str, Any]] = None) -> str:
    try:
        if isinstance(ticket_row, dict):
            ticket_number = ticket_row.get("ticket_number")
            if ticket_number is not None and str(ticket_number).strip():
                return f"ticket-{int(ticket_number):04d}"
    except Exception:
        pass

    detected = _detect_ticket_number(channel.name)
    if detected:
        return f"ticket-{detected.zfill(4)}"

    return _safe_filename(channel.name)


def _ticket_archive_category_id() -> int:
    for key in (
        "TICKET_ARCHIVE_CATEGORY_ID",
        "TICKET_ARCHIVED_CATEGORY_ID",
        "ARCHIVED_TICKET_CATEGORY_ID",
        "ARCHIVE_TICKET_CATEGORY_ID",
    ):
        value = _env_int(key, 0)
        if value > 0:
            return value
    return 0


def _looks_like_archive_category_name(name: str) -> bool:
    text = _safe_text(name).strip().lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "archive",
            "archived",
            "ticket archive",
            "tickets archive",
            "archived tickets",
            "closed tickets",
        )
    )


def _resolve_archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_archive_category_id()
    if explicit_id > 0:
        try:
            maybe = guild.get_channel(int(explicit_id))
            if isinstance(maybe, discord.CategoryChannel):
                return maybe
        except Exception:
            pass

    try:
        for category in guild.categories:
            if _looks_like_archive_category_name(category.name):
                return category
    except Exception:
        pass

    return None


def _channel_is_in_archive_category(channel: discord.TextChannel) -> bool:
    try:
        category = channel.category
        if not isinstance(category, discord.CategoryChannel):
            return False

        archive = _resolve_archive_category(channel.guild)
        if archive is not None and int(category.id) == int(archive.id):
            return True

        return _looks_like_archive_category_name(category.name)
    except Exception:
        return False


def _channel_lifecycle_location(channel: discord.TextChannel) -> str:
    try:
        if _channel_is_in_archive_category(channel):
            return f"archive:{channel.category.name if channel.category else 'unknown'}"
        if channel.category is not None:
            return f"category:{channel.category.name}"
    except Exception:
        pass
    return "uncategorized"


def _channel_effectively_closed(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> bool:
    status = _row_status(row)
    if status in {"open", "claimed"}:
        return False
    if status in {"closed", "deleted"}:
        return True
    if _channel_is_in_archive_category(channel):
        return True
    try:
        return str(channel.name or "").lower().startswith("closed-")
    except Exception:
        return False


async def _channel_still_exists(guild: discord.Guild, channel_id: int | str) -> bool:
    try:
        cached = guild.get_channel(int(channel_id))
        if cached is not None:
            return True
    except Exception:
        pass

    try:
        fetched = await asyncio.wait_for(
            guild.fetch_channel(int(channel_id)),
            timeout=DEFAULT_CHANNEL_DELETE_TIMEOUT_SECONDS,
        )
        return fetched is not None
    except discord.NotFound:
        return False
    except asyncio.TimeoutError:
        return True
    except Exception:
        return True


# ============================================================
# Message collection / transcript rendering
# ============================================================

async def _collect_messages(
    channel: discord.TextChannel,
    *,
    limit: Optional[int] = None,
) -> List[discord.Message]:
    max_limit = limit
    if max_limit is None:
        max_limit = _env_int("TRANSCRIPT_MAX_MESSAGES", DEFAULT_TRANSCRIPT_MESSAGE_LIMIT)
        if max_limit <= 0:
            max_limit = DEFAULT_TRANSCRIPT_MESSAGE_LIMIT

    async def _inner() -> List[discord.Message]:
        messages: List[discord.Message] = []
        async for msg in channel.history(limit=max_limit, oldest_first=True):
            messages.append(msg)
        return messages

    try:
        return await asyncio.wait_for(_inner(), timeout=DEFAULT_HISTORY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        _warn(f"history collection timeout channel={channel.id} limit={max_limit}")
        return []
    except Exception as e:
        _warn(f"history collection failed channel={channel.id} error={repr(e)}")
        return []


def _message_to_text_block(message: discord.Message) -> str:
    created = _utc_iso(getattr(message, "created_at", None)) or ""
    edited = _utc_iso(getattr(message, "edited_at", None)) or ""
    author = _message_author_label(message)
    content = message.content or ""

    lines = [f"[{created}] {author}: {content}"]

    if edited:
        lines.append(f"[edited_at] {edited}")

    attachments = _collect_attachment_urls(message)
    if attachments:
        lines.append("[attachments]")
        lines.extend(attachments)

    stickers = _collect_sticker_names(message)
    if stickers:
        lines.append("[stickers]")
        lines.extend(stickers)

    embed_text = _render_embed_summary_text(message)
    if embed_text:
        lines.append(embed_text)

    return "\n".join(lines)


def _build_text_transcript(messages: List[discord.Message]) -> bytes:
    blocks: List[str] = []
    for msg in messages:
        try:
            blocks.append(_message_to_text_block(msg))
        except Exception as e:
            blocks.append(f"[ERROR RENDERING MESSAGE {getattr(msg, 'id', 'unknown')}] {repr(e)}")
    return "\n\n".join(blocks).encode("utf-8", errors="replace")


def _safe_avatar_url(message: discord.Message) -> str:
    try:
        return _safe_text(message.author.display_avatar.url)
    except Exception:
        return ""


def _render_html_message(message: discord.Message) -> str:
    created = html.escape(_utc_iso(getattr(message, "created_at", None)) or "")
    edited_at = html.escape(_utc_iso(getattr(message, "edited_at", None)) or "")
    author = html.escape(_message_author_label(message))
    content = html.escape(message.content or "").replace("\n", "<br>")

    avatar = html.escape(_safe_avatar_url(message))
    fallback = html.escape((author[:1] or "?").upper())
    if avatar:
        avatar_html = f'<img class="avatar" src="{avatar}" alt="avatar">'
    else:
        avatar_html = f'<div class="avatar avatar-fallback">{fallback}</div>'

    edited_html = f'<span class="edited">(edited {edited_at})</span>' if edited_at else ""

    attachment_html_parts: List[str] = []
    try:
        for attachment in message.attachments:
            url = html.escape(_safe_text(getattr(attachment, "url", "") or ""))
            filename = html.escape(_safe_text(getattr(attachment, "filename", "") or "attachment"))
            size = getattr(attachment, "size", None)
            content_type = html.escape(_safe_text(getattr(attachment, "content_type", "") or ""))
            meta_bits = []
            if size is not None:
                meta_bits.append(f"{size} bytes")
            if content_type:
                meta_bits.append(content_type)
            meta = f" ({html.escape(' • '.join(meta_bits))})" if meta_bits else ""
            if url:
                attachment_html_parts.append(
                    f'<div class="attachment"><a href="{url}" target="_blank" rel="noopener noreferrer">{filename}</a>{meta}</div>'
                )
    except Exception:
        pass

    sticker_html = "".join(
        f'<div class="sticker-note">Sticker: {html.escape(name)}</div>'
        for name in _collect_sticker_names(message)
    )

    embed_html = ""
    try:
        for index, embed in enumerate(getattr(message, "embeds", []) or [], start=1):
            title = html.escape(_safe_text(getattr(embed, "title", "") or ""))
            description = html.escape(_safe_text(getattr(embed, "description", "") or "")).replace("\n", "<br>")
            url = html.escape(_safe_text(getattr(embed, "url", "") or ""))

            title_html = f'<div class="embed-title">{title}</div>' if title else ""
            desc_html = f'<div class="embed-description">{description}</div>' if description else ""
            url_html = (
                f'<div class="embed-url"><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></div>'
                if url
                else ""
            )

            field_parts: List[str] = []
            for field in getattr(embed, "fields", []) or []:
                try:
                    fname = html.escape(_safe_text(getattr(field, "name", "") or ""))
                    fvalue = html.escape(_safe_text(getattr(field, "value", "") or "")).replace("\n", "<br>")
                    field_parts.append(
                        f'<div class="embed-field"><div class="embed-field-name">{fname}</div><div class="embed-field-value">{fvalue}</div></div>'
                    )
                except Exception:
                    continue

            embed_html += f'<div class="embed-block" data-index="{index}">{title_html}{desc_html}{url_html}{"".join(field_parts)}</div>'
    except Exception:
        pass

    return f"""
    <div class="message">
      <div class="avatar-wrap">{avatar_html}</div>
      <div class="body">
        <div class="meta">
          <span class="author">{author}</span>
          <span class="time">{created}</span>
          {edited_html}
        </div>
        <div class="content">{content}</div>
        {"".join(attachment_html_parts)}
        {sticker_html}
        {embed_html}
      </div>
    </div>
    """


def _build_html_transcript(
    channel: discord.TextChannel,
    messages: List[discord.Message],
    ticket_row: Optional[Dict[str, Any]],
) -> bytes:
    channel_name = html.escape(channel.name)
    guild_name = html.escape(channel.guild.name)
    topic = html.escape(_safe_text(channel.topic or "No topic"))
    generated_at = html.escape(_utc_iso(now_utc()) or "")
    lifecycle = html.escape(_channel_lifecycle_location(channel))

    status = html.escape(_safe_text((ticket_row or {}).get("status") or "unknown"))
    category = html.escape(_safe_text((ticket_row or {}).get("category") or "unknown"))
    priority = html.escape(_safe_text((ticket_row or {}).get("priority") or "unknown"))
    ticket_number = html.escape(_safe_text((ticket_row or {}).get("ticket_number") or "unknown"))
    owner_id = html.escape(
        _safe_text(
            (ticket_row or {}).get("user_id")
            or (ticket_row or {}).get("owner_id")
            or (ticket_row or {}).get("requester_id")
            or "unknown"
        )
    )

    rendered_messages: List[str] = []
    for msg in messages:
        try:
            rendered_messages.append(_render_html_message(msg))
        except Exception as e:
            rendered_messages.append(
                f'<div class="message error">Failed to render message {html.escape(str(getattr(msg, "id", "unknown")))}: {html.escape(repr(e))}</div>'
            )

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Transcript - #{channel_name}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{
  margin: 0;
  padding: 0;
  background: #0b1020;
  color: #e8edf8;
  font-family: Arial, Helvetica, sans-serif;
}}
.wrap {{
  max-width: 980px;
  margin: 0 auto;
  padding: 20px;
}}
.header {{
  background: #121a2f;
  border: 1px solid #24304d;
  border-radius: 16px;
  padding: 18px;
  margin-bottom: 18px;
}}
.title {{
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 6px;
}}
.sub {{
  color: #9fb0d1;
  font-size: 14px;
  margin-bottom: 4px;
}}
.message {{
  display: flex;
  gap: 12px;
  padding: 14px;
  margin-bottom: 10px;
  background: #10172b;
  border: 1px solid #1f2a46;
  border-radius: 14px;
}}
.avatar-wrap {{
  flex: 0 0 44px;
}}
.avatar {{
  width: 44px;
  height: 44px;
  border-radius: 999px;
  object-fit: cover;
  background: #1b2540;
  display: block;
}}
.avatar-fallback {{
  width: 44px;
  height: 44px;
  border-radius: 999px;
  background: #1b2540;
  color: #dfe7fb;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}}
.body {{
  flex: 1;
  min-width: 0;
}}
.meta {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
  margin-bottom: 6px;
}}
.author {{
  font-weight: 700;
  color: #ffffff;
}}
.time, .edited {{
  color: #91a2c7;
  font-size: 12px;
}}
.content {{
  line-height: 1.45;
  overflow-wrap: anywhere;
}}
.attachment {{
  margin-top: 8px;
}}
.attachment a, .embed-url a {{
  color: #77b2ff;
  text-decoration: none;
}}
.sticker-note {{
  margin-top: 8px;
  color: #b0c4f5;
  font-size: 13px;
}}
.embed-block {{
  margin-top: 10px;
  padding: 10px 12px;
  border-left: 4px solid #5b7fff;
  background: #0d1528;
  border-radius: 10px;
}}
.embed-title {{
  font-weight: 700;
  margin-bottom: 4px;
}}
.embed-description, .embed-field-value {{
  color: #d7e2ff;
  line-height: 1.4;
}}
.embed-field {{
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #22304f;
}}
.embed-field-name {{
  font-weight: 700;
  margin-bottom: 3px;
}}
.error {{
  color: #ffb1b1;
}}
</style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="title">Transcript for #{channel_name}</div>
      <div class="sub">Guild: {guild_name}</div>
      <div class="sub">Ticket #: {ticket_number}</div>
      <div class="sub">Owner ID: {owner_id}</div>
      <div class="sub">Status: {status}</div>
      <div class="sub">Category: {category}</div>
      <div class="sub">Priority: {priority}</div>
      <div class="sub">Lifecycle: {lifecycle}</div>
      <div class="sub">Topic: {topic}</div>
      <div class="sub">Generated At: {generated_at}</div>
      <div class="sub">Messages Captured: {len(messages)}</div>
    </div>
    {"".join(rendered_messages)}
  </div>
</body>
</html>
"""
    return doc.encode("utf-8", errors="replace")


async def generate_transcript_files(
    channel: discord.TextChannel,
) -> Tuple[discord.File, discord.File, int]:
    row = await _ticket_row(channel.id)
    messages = await _collect_messages(channel)

    basename = _safe_filename(_transcript_basename(channel, row))
    txt_bytes = _build_text_transcript(messages)
    html_bytes = _build_html_transcript(channel, messages, row)

    txt_file = discord.File(
        io.BytesIO(txt_bytes),
        filename=f"{basename}-{channel.id}.txt",
    )
    html_file = discord.File(
        io.BytesIO(html_bytes),
        filename=f"{basename}-{channel.id}.html",
    )

    return txt_file, html_file, len(messages)


# ============================================================
# Transcript posting
# ============================================================

async def _get_transcripts_channel(
    guild: discord.Guild,
    ticket_row: Optional[Dict[str, Any]] = None,
) -> Optional[discord.TextChannel]:
    candidate_ids = _candidate_transcripts_channel_ids(guild, ticket_row)

    for cid in candidate_ids:
        ch = await _resolve_same_guild_text_channel_by_id(guild, cid)
        if isinstance(ch, discord.TextChannel):
            _debug(f"resolved transcripts channel guild={guild.id} channel={ch.id} source=id")
            return ch

    # Final same-guild fallback by channel name.
    fallback = _find_same_guild_transcripts_channel_by_name(guild)
    if isinstance(fallback, discord.TextChannel):
        _debug(f"resolved transcripts channel guild={guild.id} channel={fallback.id} source=name")
        return fallback

    _warn(
        f"cannot resolve transcripts channel for guild={guild.id}; "
        f"checked_ids={candidate_ids or []} fallback_name_search_failed=True"
    )
    return None


def _transcript_summary_embed(
    *,
    ticket_channel: discord.TextChannel,
    deleted_by: Optional[discord.abc.User],
    reason: Optional[str],
    message_count: int,
    ticket_row: Optional[Dict[str, Any]],
) -> discord.Embed:
    actor_text = _safe_text(deleted_by) if deleted_by else "Unknown"
    reason_text = reason or "Ticket transcript requested"

    embed = discord.Embed(
        title=f"🧾 Transcript for #{ticket_channel.name}",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Channel ID", value=f"`{ticket_channel.id}`", inline=True)
    embed.add_field(name="Guild ID", value=f"`{ticket_channel.guild.id}`", inline=True)
    embed.add_field(name="Messages", value=f"`{message_count}`", inline=True)
    embed.add_field(name="Lifecycle", value=f"`{_channel_lifecycle_location(ticket_channel)}`", inline=False)

    if isinstance(ticket_row, dict):
        ticket_number = ticket_row.get("ticket_number")
        owner_id = (
            ticket_row.get("user_id")
            or ticket_row.get("owner_id")
            or ticket_row.get("requester_id")
        )
        status = ticket_row.get("status") or "unknown"
        category = ticket_row.get("category") or "unknown"
        priority = ticket_row.get("priority") or "unknown"

        embed.add_field(name="Ticket #", value=f"`{ticket_number or 'unknown'}`", inline=True)
        embed.add_field(name="Owner ID", value=f"`{owner_id or 'unknown'}`", inline=True)
        embed.add_field(name="Status", value=f"`{status}`", inline=True)
        embed.add_field(name="Category", value=f"`{category}`", inline=True)
        embed.add_field(name="Priority", value=f"`{priority}`", inline=True)

    embed.add_field(name="Actor", value=_truncate_for_discord(actor_text, 256), inline=True)
    embed.add_field(name="Reason", value=_truncate_for_discord(reason_text, 1024), inline=False)
    embed.set_footer(text=_TRANSCRIPT_MARKER)
    return embed


async def _attach_transcript_best_effort(
    *,
    channel_id: int,
    transcript_url: Optional[str],
    transcript_message_id: Optional[int],
    transcript_channel_id: Optional[int],
    actor: Optional[discord.abc.User],
) -> bool:
    attempts = [
        {
            "channel_id": channel_id,
            "transcript_url": transcript_url,
            "transcript_message_id": transcript_message_id,
            "transcript_channel_id": transcript_channel_id,
            "actor": actor,
        },
        {
            "channel_id": channel_id,
            "transcript_url": transcript_url,
            "transcript_message_id": transcript_message_id,
            "transcript_channel_id": transcript_channel_id,
            "actor_id": _actor_id(actor),
            "actor_name": _actor_name(actor),
        },
        {
            "channel_id": channel_id,
            "transcript_url": transcript_url,
            "transcript_message_id": transcript_message_id,
            "transcript_channel_id": transcript_channel_id,
        },
    ]

    for kwargs in attempts:
        try:
            await asyncio.wait_for(
                attach_transcript_to_ticket(**kwargs),
                timeout=DEFAULT_DB_WRITE_TIMEOUT_SECONDS,
            )
            _debug(f"transcript metadata attached channel={channel_id} message={transcript_message_id}")
            return True
        except TypeError:
            continue
        except asyncio.TimeoutError:
            _warn(f"attach transcript metadata timeout channel={channel_id}")
            return False
        except Exception as e:
            _warn(f"attach transcript metadata failed channel={channel_id} error={repr(e)}")
            return False

    return False


async def post_transcript_to_channel(
    ticket_channel: discord.TextChannel,
    deleted_by: Optional[discord.Member | discord.User] = None,
    reason: Optional[str] = None,
) -> Tuple[Optional[discord.Message], Optional[str]]:
    """
    Post transcript files to the configured transcript channel exactly once.

    Returns:
        (posted_message, transcript_url)
    """
    channel_id = int(ticket_channel.id)
    lock = _transcript_post_lock(channel_id)

    acquired = await _acquire_lock_with_timeout(
        lock,
        timeout=DEFAULT_LOCK_TIMEOUT_SECONDS,
        label="transcript-post",
        channel_id=channel_id,
    )
    if not acquired:
        row = await _ticket_row(channel_id)
        transcript_url, _msg_id, _ch_id = _row_transcript_payload(row, guild_id=ticket_channel.guild.id)
        return None, transcript_url

    try:
        row = await _ticket_row(channel_id)
        if _row_has_transcript(row):
            transcript_url, msg_id, ch_id = _row_transcript_payload(row, guild_id=ticket_channel.guild.id)
            _debug(f"transcript already exists channel={channel_id} message={msg_id} transcript_channel={ch_id}")
            return None, transcript_url

        transcript_channel = await _get_transcripts_channel(ticket_channel.guild, row)
        if transcript_channel is None:
            _warn(
                f"cannot post transcript; transcript channel unavailable "
                f"ticket_channel={channel_id} guild={ticket_channel.guild.id}"
            )
            return None, None

        try:
            txt_file, html_file, message_count = await asyncio.wait_for(
                generate_transcript_files(ticket_channel),
                timeout=DEFAULT_TRANSCRIPT_POST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            _warn(f"generate transcript files timeout channel={channel_id}; posting timeout marker only")
            txt_file = discord.File(
                io.BytesIO(b"Transcript generation timed out before messages could be collected.\n"),
                filename=f"ticket-{channel_id}-timeout.txt",
            )
            html_file = discord.File(
                io.BytesIO(b"<!doctype html><html><body><h1>Transcript generation timed out.</h1></body></html>"),
                filename=f"ticket-{channel_id}-timeout.html",
            )
            message_count = 0
        except Exception as e:
            _warn(f"generate transcript files failed channel={channel_id} error={repr(e)}")
            txt_file = discord.File(
                io.BytesIO(f"Transcript generation failed: {repr(e)}\n".encode("utf-8", errors="replace")),
                filename=f"ticket-{channel_id}-error.txt",
            )
            html_file = discord.File(
                io.BytesIO(
                    f"<!doctype html><html><body><h1>Transcript generation failed</h1><pre>{html.escape(repr(e))}</pre></body></html>".encode(
                        "utf-8",
                        errors="replace",
                    )
                ),
                filename=f"ticket-{channel_id}-error.html",
            )
            message_count = 0

        row = row or await _ticket_row(channel_id)

        embed = _transcript_summary_embed(
            ticket_channel=ticket_channel,
            deleted_by=deleted_by,
            reason=reason,
            message_count=message_count,
            ticket_row=row,
        )

        content = (
            f"{_TRANSCRIPT_MARKER}\n"
            f"Transcript saved for `#{ticket_channel.name}` (`{ticket_channel.id}`)."
        )

        try:
            posted = await asyncio.wait_for(
                transcript_channel.send(
                    content=content,
                    embed=embed,
                    files=[txt_file, html_file],
                    allowed_mentions=discord.AllowedMentions.none(),
                ),
                timeout=DEFAULT_TRANSCRIPT_POST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            _warn(f"transcript channel.send timeout ticket_channel={channel_id}")
            return None, None
        except Exception as e:
            _warn(f"transcript channel.send failed ticket_channel={channel_id} error={repr(e)}")
            return None, None

        transcript_url = getattr(posted, "jump_url", None)

        await _attach_transcript_best_effort(
            channel_id=channel_id,
            transcript_url=transcript_url,
            transcript_message_id=int(posted.id),
            transcript_channel_id=int(posted.channel.id),
            actor=deleted_by,
        )

        _debug(
            f"post success channel={channel_id} posted_message={posted.id} transcript_channel={posted.channel.id}"
        )
        return posted, transcript_url

    finally:
        try:
            lock.release()
        except RuntimeError:
            pass
        except Exception:
            pass


# ============================================================
# Delete flow
# ============================================================

async def _mark_ticket_deleted_best_effort(
    *,
    channel: discord.TextChannel,
    actor: Optional[discord.abc.User],
    reason: str,
) -> bool:
    attempts = [
        {
            "channel_id": channel.id,
            "deleted_by": actor,
            "reason": reason,
        },
        {
            "channel_id": channel.id,
            "actor": actor,
            "reason": reason,
        },
        {
            "channel_id": channel.id,
            "deleted_by_id": _actor_id(actor),
            "deleted_by_name": _actor_name(actor),
            "reason": reason,
        },
        {
            "channel_id": channel.id,
            "reason": reason,
        },
    ]

    for kwargs in attempts:
        try:
            await asyncio.wait_for(
                mark_ticket_deleted(**kwargs),
                timeout=DEFAULT_DB_WRITE_TIMEOUT_SECONDS,
            )
            _debug(f"db mark deleted success channel={channel.id}")
            return True
        except TypeError:
            continue
        except asyncio.TimeoutError:
            _warn(f"db mark deleted timeout channel={channel.id}")
            return False
        except Exception as e:
            _warn(f"db mark deleted failed channel={channel.id} error={repr(e)}")
            return False

    return False


async def _delete_discord_channel(
    *,
    channel: discord.TextChannel,
    reason: str,
) -> Dict[str, Any]:
    try:
        _debug(f"discord channel.delete starting channel={channel.id} name={channel.name}")
        await asyncio.wait_for(
            channel.delete(reason=reason[:512]),
            timeout=DEFAULT_CHANNEL_DELETE_TIMEOUT_SECONDS,
        )
        _debug(f"discord channel.delete success channel={channel.id}")
        return {"deleted": True, "channel_deleted": True, "reason": "channel.delete success"}
    except asyncio.TimeoutError:
        _error(f"discord channel.delete timeout channel={channel.id}")
        return {
            "deleted": False,
            "channel_deleted": False,
            "reason": "Discord channel.delete timed out.",
        }
    except discord.NotFound:
        _debug(f"discord channel.delete already gone channel={channel.id}")
        return {"deleted": True, "channel_deleted": True, "reason": "channel already gone"}
    except discord.Forbidden as e:
        _error(f"discord channel.delete forbidden channel={channel.id} error={repr(e)}")
        return {
            "deleted": False,
            "channel_deleted": False,
            "reason": "Discord denied channel deletion. Check Manage Channels and channel permissions.",
            "error": repr(e),
        }
    except discord.HTTPException as e:
        _error(f"discord channel.delete HTTPException channel={channel.id} error={repr(e)}")
        return {
            "deleted": False,
            "channel_deleted": False,
            "reason": f"Discord HTTP error while deleting channel: {e}",
            "error": repr(e),
        }
    except Exception as e:
        _error(f"discord channel.delete unexpected channel={channel.id} error={repr(e)}")
        return {
            "deleted": False,
            "channel_deleted": False,
            "reason": f"Unexpected channel delete error: {e}",
            "error": repr(e),
        }


async def delete_ticket_with_optional_transcript(
    channel: discord.TextChannel,
    deleted_by: Optional[discord.Member | discord.User] = None,
    is_ghost: bool = False,
    force_transcript_for_ghost: bool = False,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Canonical hardened ticket delete.

    This function intentionally avoids shared mutation locks because the old
    delete path could deadlock when transcript + DB lifecycle code nested locks.
    """
    if not isinstance(channel, discord.TextChannel):
        return {"deleted": False, "reason": "Invalid channel type."}

    channel_id = int(channel.id)
    delete_reason = reason or "Ticket deleted"

    lock = _delete_lock(channel_id)
    acquired = await _acquire_lock_with_timeout(
        lock,
        timeout=DEFAULT_LOCK_TIMEOUT_SECONDS,
        label="delete",
        channel_id=channel_id,
    )
    if not acquired:
        return {
            "deleted": False,
            "channel_deleted": False,
            "reason": "Delete is already running for this ticket.",
            "locked": True,
        }

    started_at = time.monotonic()
    transcript_posted = False
    transcript_url: Optional[str] = None
    db_marked_deleted = False

    try:
        row = await _ticket_row(channel_id)

        if _row_status(row) == "deleted":
            still_exists = await _channel_still_exists(channel.guild, channel_id)
            if not still_exists:
                return {
                    "deleted": True,
                    "channel_deleted": True,
                    "reason": "Ticket was already deleted.",
                    "already_deleted": True,
                }

        if not _channel_effectively_closed(channel=channel, row=row):
            _warn(
                f"delete requested for open-like channel={channel_id} "
                f"status={_row_status(row)!r} lifecycle={_channel_lifecycle_location(channel)!r}"
            )

        should_post_transcript = True
        if is_ghost and not force_transcript_for_ghost:
            should_post_transcript = False

        if _row_has_transcript(row):
            transcript_url, _msg_id, _ch_id = _row_transcript_payload(row, guild_id=channel.guild.id)
            transcript_posted = bool(transcript_url)
            _debug(f"delete flow transcript already present channel={channel_id}")
        elif should_post_transcript:
            try:
                posted, transcript_url = await asyncio.wait_for(
                    post_transcript_to_channel(
                        ticket_channel=channel,
                        deleted_by=deleted_by,
                        reason=delete_reason,
                    ),
                    timeout=DEFAULT_TRANSCRIPT_POST_TIMEOUT_SECONDS + 4.0,
                )
                transcript_posted = posted is not None or bool(transcript_url)
            except asyncio.TimeoutError:
                _warn(f"delete flow transcript post timeout channel={channel_id}; continuing to delete")
            except Exception as e:
                _warn(f"delete flow transcript post failed channel={channel_id} error={repr(e)}; continuing to delete")
        else:
            _debug(f"delete flow skipping transcript channel={channel_id} ghost={is_ghost}")

        db_marked_deleted = await _mark_ticket_deleted_best_effort(
            channel=channel,
            actor=deleted_by,
            reason=delete_reason,
        )

        delete_result = await _delete_discord_channel(
            channel=channel,
            reason=delete_reason,
        )

        elapsed_ms = int((time.monotonic() - started_at) * 1000)

        result = {
            **delete_result,
            "transcript_posted": transcript_posted,
            "transcript_url": transcript_url,
            "db_marked_deleted": db_marked_deleted,
            "elapsed_ms": elapsed_ms,
            "channel_id": channel_id,
        }

        if result.get("deleted"):
            _debug(
                f"delete complete channel={channel_id} transcript={transcript_posted} "
                f"db={db_marked_deleted} elapsed_ms={elapsed_ms}"
            )
        else:
            _warn(f"delete incomplete channel={channel_id} result={result!r}")

        return result

    except Exception as e:
        _error(f"delete flow unexpected channel={channel_id} error={repr(e)}")
        return {
            "deleted": False,
            "channel_deleted": False,
            "reason": f"Unexpected delete flow error: {e}",
            "error": repr(e),
            "transcript_posted": transcript_posted,
            "transcript_url": transcript_url,
            "db_marked_deleted": db_marked_deleted,
            "channel_id": channel_id,
        }
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass
        except Exception:
            pass


async def staff_delete_closed_ticket(
    channel: discord.TextChannel,
    staff_member: discord.Member,
    is_ghost: bool = False,
    reason: str = "Deleted by staff",
) -> Dict[str, Any]:
    """
    Staff-facing delete wrapper.

    This must always return quickly enough for interaction callbacks. The caller
    may still wrap it in wait_for, but this function also internally timeboxes
    every expensive operation.
    """
    if not isinstance(channel, discord.TextChannel):
        return {"deleted": False, "reason": "Invalid channel."}

    if not isinstance(staff_member, discord.Member):
        return {"deleted": False, "reason": "Staff member could not be resolved."}

    try:
        if not (
            staff_member.guild_permissions.manage_channels
            or staff_member.guild_permissions.manage_messages
            or staff_member.guild_permissions.administrator
        ):
            return {"deleted": False, "reason": "Staff only."}
    except Exception:
        return {"deleted": False, "reason": "Staff permission check failed."}

    _debug(
        f"staff delete start channel={channel.id} staff={staff_member.id} "
        f"ghost={is_ghost} reason={reason!r}"
    )

    try:
        return await asyncio.wait_for(
            delete_ticket_with_optional_transcript(
                channel=channel,
                deleted_by=staff_member,
                is_ghost=is_ghost,
                force_transcript_for_ghost=False,
                reason=reason,
            ),
            timeout=DEFAULT_TRANSCRIPT_POST_TIMEOUT_SECONDS
            + DEFAULT_DB_WRITE_TIMEOUT_SECONDS
            + DEFAULT_CHANNEL_DELETE_TIMEOUT_SECONDS
            + 8.0,
        )
    except asyncio.TimeoutError:
        _error(f"staff delete wrapper timeout channel={channel.id}; attempting direct channel.delete fallback")

        db_marked_deleted = await _mark_ticket_deleted_best_effort(
            channel=channel,
            actor=staff_member,
            reason=f"{reason} (timeout fallback)",
        )
        delete_result = await _delete_discord_channel(
            channel=channel,
            reason=f"{reason} (timeout fallback)",
        )
        delete_result["db_marked_deleted"] = db_marked_deleted
        delete_result["timeout_fallback"] = True
        return delete_result
    except Exception as e:
        _error(f"staff delete wrapper unexpected channel={channel.id} error={repr(e)}")
        return {
            "deleted": False,
            "channel_deleted": False,
            "reason": f"Unexpected staff delete error: {e}",
            "error": repr(e),
        }


__all__ = [
    "generate_transcript_files",
    "post_transcript_to_channel",
    "delete_ticket_with_optional_transcript",
    "staff_delete_closed_ticket",
]
