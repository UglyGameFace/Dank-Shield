from __future__ import annotations

import asyncio
import html
import io
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from ..globals import TRANSCRIPTS_CHANNEL_ID, now_utc
from .service import attach_transcript_to_ticket, mark_ticket_deleted

try:
    from .repository import get_ticket_by_any_channel_id
except Exception:
    async def get_ticket_by_any_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None


# ============================================================
# transcript_service.py
# ------------------------------------------------------------
# Purpose:
# - Generate .txt + .html transcripts
# - Post transcripts to TRANSCRIPTS_CHANNEL_ID
# - Keep transcript metadata attached exactly once
# - Let staff finish deletion safely
# - Mark ticket deleted in DB before final delete
# - Avoid duplicate concurrent transcript/delete flows
# - Prevent stale closed-ticket delete controls from deleting a reopened ticket
# ============================================================

_DELETE_LOCKS: Dict[str, asyncio.Lock] = {}
_TRANSCRIPT_POST_LOCKS: Dict[str, asyncio.Lock] = {}

_TRANSCRIPT_MARKER = "stoney_verify:transcript_posted:v5"


# ============================================================
# Small helpers
# ============================================================

def _channel_lock(bucket: Dict[str, asyncio.Lock], channel_id: int | str) -> asyncio.Lock:
    key = str(channel_id)
    lock = bucket.get(key)
    if lock is None:
        lock = asyncio.Lock()
        bucket[key] = lock
    return lock


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
    for ch in (name or ""):
        if ch.isalnum() or ch in ("-", "_", "."):
            cleaned.append(ch)
        else:
            cleaned.append("-")
    out = "".join(cleaned).strip("-")
    return out[:90] if out else "transcript"


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


def _safe_topic_text(value: Any, max_len: int = 800) -> str:
    text = _safe_text(value or "").strip()
    if not text:
        return "No topic"
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _truncate_for_discord(text: str, limit: int = 1900) -> str:
    safe = _safe_text(text)
    if len(safe) <= limit:
        return safe
    return safe[: limit - 3] + "..."


def _safe_avatar_url(msg: discord.Message) -> str:
    try:
        return _safe_text(msg.author.display_avatar.url)
    except Exception:
        return ""


def _collect_attachment_urls(msg: discord.Message) -> List[str]:
    urls: List[str] = []
    try:
        for a in msg.attachments:
            try:
                if a.url:
                    urls.append(a.url)
            except Exception:
                continue
    except Exception:
        pass
    return urls


def _collect_attachment_rows(msg: discord.Message) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        for a in msg.attachments:
            try:
                rows.append(
                    {
                        "url": _safe_text(getattr(a, "url", "") or ""),
                        "filename": _safe_text(getattr(a, "filename", "") or ""),
                        "size": getattr(a, "size", None),
                        "content_type": _safe_text(getattr(a, "content_type", "") or ""),
                    }
                )
            except Exception:
                continue
    except Exception:
        pass
    return rows


def _collect_sticker_names(msg: discord.Message) -> List[str]:
    names: List[str] = []
    try:
        for s in getattr(msg, "stickers", []) or []:
            try:
                names.append(_safe_text(getattr(s, "name", "")))
            except Exception:
                continue
    except Exception:
        pass
    return [n for n in names if n]


def _render_embed_summary_text(msg: discord.Message) -> str:
    try:
        embeds = getattr(msg, "embeds", None) or []
        if not embeds:
            return ""
        parts: List[str] = []
        for index, embed in enumerate(embeds, start=1):
            try:
                section: List[str] = [f"[embed {index}]"]
                title = _safe_text(getattr(embed, "title", "")).strip()
                description = _safe_text(getattr(embed, "description", "")).strip()
                url = _safe_text(getattr(embed, "url", "")).strip()

                if title:
                    section.append(f"title={title}")
                if description:
                    section.append(f"description={description}")
                if url:
                    section.append(f"url={url}")

                fields = getattr(embed, "fields", None) or []
                if fields:
                    for f in fields:
                        try:
                            fname = _safe_text(getattr(f, "name", "")).strip()
                            fvalue = _safe_text(getattr(f, "value", "")).strip()
                            section.append(f"field={fname}: {fvalue}")
                        except Exception:
                            continue

                parts.append("\n".join(section))
            except Exception:
                parts.append(f"[embed {index}]")

        return "\n".join(parts)
    except Exception:
        return ""


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


def _jump_url_from_ids(*, guild_id: int, channel_id: Optional[int], message_id: Optional[int]) -> Optional[str]:
    try:
        cid = int(channel_id or 0)
        mid = int(message_id or 0)
        gid = int(guild_id or 0)
        if gid <= 0 or cid <= 0 or mid <= 0:
            return None
        return f"https://discord.com/channels/{gid}/{cid}/{mid}"
    except Exception:
        return None


# ============================================================
# Message collection / rendering
# ============================================================

async def _collect_messages(channel: discord.TextChannel) -> List[discord.Message]:
    msgs: List[discord.Message] = []
    try:
        async for msg in channel.history(limit=None, oldest_first=True):
            msgs.append(msg)
    except Exception as e:
        print("⚠️ Failed collecting transcript messages:", repr(e))
    return msgs


def _message_to_text_line(msg: discord.Message) -> str:
    created = _utc_iso(getattr(msg, "created_at", None)) or ""
    edited = _utc_iso(getattr(msg, "edited_at", None)) or ""
    author = _safe_text(getattr(msg, "author", "Unknown"))
    content = msg.content or ""

    lines: List[str] = [f"[{created}] {author}: {content}"]

    if edited:
        lines.append(f"[edited_at] {edited}")

    attachments = _collect_attachment_urls(msg)
    if attachments:
        lines.append("[attachments]")
        lines.extend(attachments)

    stickers = _collect_sticker_names(msg)
    if stickers:
        lines.append("[stickers]")
        lines.extend(stickers)

    embed_text = _render_embed_summary_text(msg)
    if embed_text:
        lines.append(embed_text)

    return "\n".join(lines)


def _build_text_transcript(messages: List[discord.Message]) -> bytes:
    lines: List[str] = []

    for msg in messages:
        try:
            lines.append(_message_to_text_line(msg))
        except Exception as e:
            lines.append(
                f"[ERROR RENDERING MESSAGE {getattr(msg, 'id', 'unknown')}] {repr(e)}"
            )

    return "\n\n".join(lines).encode("utf-8", errors="replace")


def _render_embed_html(msg: discord.Message) -> str:
    try:
        embeds = getattr(msg, "embeds", None) or []
        if not embeds:
            return ""

        blocks: List[str] = []
        for embed in embeds:
            try:
                title = html.escape(_safe_text(getattr(embed, "title", "")))
                description = html.escape(
                    _safe_text(getattr(embed, "description", ""))
                ).replace("\n", "<br>")
                url = html.escape(_safe_text(getattr(embed, "url", "")))
                fields = getattr(embed, "fields", None) or []

                fields_html: List[str] = []
                for field in fields:
                    try:
                        fname = html.escape(_safe_text(getattr(field, "name", "")))
                        fvalue = html.escape(
                            _safe_text(getattr(field, "value", ""))
                        ).replace("\n", "<br>")
                        fields_html.append(
                            f'<div class="embed-field"><div class="embed-field-name">{fname}</div><div class="embed-field-value">{fvalue}</div></div>'
                        )
                    except Exception:
                        continue

                title_html = f'<div class="embed-title">{title}</div>' if title else ""
                desc_html = (
                    f'<div class="embed-description">{description}</div>'
                    if description
                    else ""
                )
                url_html = (
                    f'<div class="embed-url"><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></div>'
                    if url
                    else ""
                )

                blocks.append(
                    f'<div class="embed-block">{title_html}{desc_html}{url_html}{"".join(fields_html)}</div>'
                )
            except Exception:
                blocks.append('<div class="embed-block">Failed to render embed.</div>')

        return "".join(blocks)
    except Exception:
        return ""


def _render_avatar_html(msg: discord.Message) -> str:
    avatar = html.escape(_safe_avatar_url(msg))
    author = html.escape(_safe_text(getattr(msg, "author", "Unknown"))[:1] or "?")

    if avatar:
        return f'<img class="avatar" src="{avatar}" alt="avatar">'

    return f'<div class="avatar avatar-fallback" aria-label="avatar-fallback">{author.upper()}</div>'


def _render_html_message(msg: discord.Message) -> str:
    created = html.escape(_utc_iso(getattr(msg, "created_at", None)) or "")
    edited_at = html.escape(_utc_iso(getattr(msg, "edited_at", None)) or "")
    author = html.escape(_safe_text(getattr(msg, "author", "Unknown")))
    content = html.escape(msg.content or "").replace("\n", "<br>")

    edited_html = f'<span class="edited">(edited {edited_at})</span>' if edited_at else ""

    attachment_html = ""
    attachments = _collect_attachment_rows(msg)
    if attachments:
        parts: List[str] = []
        for a in attachments:
            try:
                url = html.escape(_safe_text(a.get("url") or ""))
                filename = html.escape(_safe_text(a.get("filename") or "attachment"))
                size = a.get("size")
                ctype = html.escape(_safe_text(a.get("content_type") or ""))
                meta_bits: List[str] = []
                if size is not None:
                    meta_bits.append(f"{size} bytes")
                if ctype:
                    meta_bits.append(ctype)
                meta = f" ({html.escape(' • '.join(meta_bits))})" if meta_bits else ""
                parts.append(
                    f'<div class="attachment"><a href="{url}" target="_blank" rel="noopener noreferrer">{filename}</a>{meta}</div>'
                )
            except Exception:
                continue
        attachment_html = "".join(parts)

    sticker_html = ""
    sticker_names = _collect_sticker_names(msg)
    if sticker_names:
        sticker_html = "".join(
            f'<div class="sticker-note">Sticker: {html.escape(name)}</div>'
            for name in sticker_names
        )

    embed_html = _render_embed_html(msg)
    avatar_html = _render_avatar_html(msg)

    return f"""
    <div class="message">
      <div class="avatar-wrap">
        {avatar_html}
      </div>
      <div class="body">
        <div class="meta">
          <span class="author">{author}</span>
          <span class="time">{created}</span>
          {edited_html}
        </div>
        <div class="content">{content}</div>
        {attachment_html}
        {sticker_html}
        {embed_html}
      </div>
    </div>
    """


def _build_html_transcript(
    channel: discord.TextChannel,
    messages: List[discord.Message],
) -> bytes:
    channel_name = html.escape(channel.name)
    guild_name = html.escape(channel.guild.name)
    topic = html.escape(_safe_topic_text(channel.topic))
    generated_at = html.escape(_utc_iso(now_utc()) or "")

    rendered: List[str] = []
    for msg in messages:
        try:
            rendered.append(_render_html_message(msg))
        except Exception as e:
            rendered.append(
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
    font-size: 18px;
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
    word-wrap: break-word;
    white-space: normal;
  }}
  .attachment {{
    margin-top: 8px;
  }}
  .attachment a {{
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
  .embed-description {{
    color: #d7e2ff;
    line-height: 1.4;
  }}
  .embed-url {{
    margin-top: 6px;
    font-size: 12px;
  }}
  .embed-url a {{
    color: #77b2ff;
    text-decoration: none;
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
  .embed-field-value {{
    color: #d7e2ff;
    line-height: 1.4;
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
      <div class="sub">Topic: {topic}</div>
      <div class="sub">Generated At: {generated_at}</div>
    </div>
    {"".join(rendered)}
  </div>
</body>
</html>
"""
    return doc.encode("utf-8", errors="replace")


async def generate_transcript_files(
    channel: discord.TextChannel,
) -> Tuple[discord.File, discord.File, int]:
    messages = await _collect_messages(channel)
    row = await _ticket_row(channel.id)
    safe = _safe_filename(_transcript_basename(channel, row))

    txt_bytes = _build_text_transcript(messages)
    html_bytes = _build_html_transcript(channel, messages)

    txt_file = discord.File(
        io.BytesIO(txt_bytes),
        filename=f"{safe}-{channel.id}.txt",
    )
    html_file = discord.File(
        io.BytesIO(html_bytes),
        filename=f"{safe}-{channel.id}.html",
    )

    return txt_file, html_file, len(messages)


# ============================================================
# Channel / DB helpers
# ============================================================

async def _get_transcripts_channel(
    guild: discord.Guild,
) -> Optional[discord.TextChannel]:
    try:
        cid = int(str(TRANSCRIPTS_CHANNEL_ID or "0") or 0)
    except Exception:
        cid = 0

    if not cid:
        print("⚠️ TRANSCRIPTS_CHANNEL_ID missing.")
        return None

    try:
        ch = guild.get_channel(cid)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(cid)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception as e:
        print("⚠️ Failed to fetch transcripts channel:", repr(e))

    return None


async def _ticket_row(channel_id: int | str) -> Optional[Dict[str, Any]]:
    try:
        row = await get_ticket_by_any_channel_id(channel_id)
        if isinstance(row, dict):
            return row
    except Exception as e:
        print("⚠️ Failed loading ticket row for transcript flow:", repr(e))
    return None


def _row_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = str((row or {}).get("status") or "").strip().lower()
        if raw in {"open", "claimed", "closed", "deleted"}:
            return raw
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


def _row_transcript_payload(
    row: Optional[Dict[str, Any]],
    *,
    guild_id: Optional[int] = None,
) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    if not isinstance(row, dict):
        return None, None, None

    try:
        url = _safe_text(row.get("transcript_url") or "").strip() or None
    except Exception:
        url = None

    try:
        message_id = _safe_int(row.get("transcript_message_id"), 0) or None
    except Exception:
        message_id = None

    try:
        channel_id = _safe_int(row.get("transcript_channel_id"), 0) or None
    except Exception:
        channel_id = None

    if not url and guild_id and channel_id and message_id:
        url = _jump_url_from_ids(guild_id=int(guild_id), channel_id=channel_id, message_id=message_id)

    return url, message_id, channel_id


async def _attach_transcript_metadata_once(
    *,
    channel_id: int,
    transcript_url: Optional[str],
    transcript_message_id: Optional[int],
    transcript_channel_id: Optional[int],
    actor: Optional[discord.Member | discord.User] = None,
) -> bool:
    row_before = await _ticket_row(channel_id)
    existing_url, existing_message_id, existing_channel_id = _row_transcript_payload(
        row_before,
        guild_id=None,
    )

    if existing_url or existing_message_id or existing_channel_id:
        return True

    try:
        await attach_transcript_to_ticket(
            channel_id=channel_id,
            transcript_url=transcript_url,
            transcript_message_id=transcript_message_id,
            transcript_channel_id=transcript_channel_id,
            actor=actor,
        )
        return True
    except Exception as e:
        print("⚠️ Failed attaching transcript metadata:", repr(e))
        return False


async def _heal_existing_transcript_metadata_if_missing(
    *,
    channel_id: int,
    guild_id: int,
) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    row = await _ticket_row(channel_id)
    url, msg_id, ch_id = _row_transcript_payload(row, guild_id=guild_id)
    if url or msg_id or ch_id:
        return url, msg_id, ch_id
    return None, None, None


def _transcript_summary_embed(
    *,
    ticket_channel: discord.TextChannel,
    deleted_by: Optional[discord.Member | discord.User],
    reason: Optional[str],
    message_count: int,
    ticket_row: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    deleted_by_text = _safe_text(deleted_by) if deleted_by else "Unknown"
    reason_text = reason or "Ticket deleted"
    topic_text = _safe_topic_text(ticket_channel.topic, max_len=1000)

    ticket_number = None
    owner_id = None
    status = None
    category = None
    priority = None

    if isinstance(ticket_row, dict):
        ticket_number = ticket_row.get("ticket_number")
        owner_id = ticket_row.get("user_id") or ticket_row.get("owner_id") or ticket_row.get("requester_id")
        status = ticket_row.get("status")
        category = ticket_row.get("category")
        priority = ticket_row.get("priority")

    embed = discord.Embed(
        title=f"🧾 Transcript for #{ticket_channel.name}",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Channel ID", value=f"`{ticket_channel.id}`", inline=True)
    embed.add_field(name="Guild ID", value=f"`{ticket_channel.guild.id}`", inline=True)
    embed.add_field(name="Messages", value=f"`{message_count}`", inline=True)

    if ticket_number is not None:
        embed.add_field(name="Ticket #", value=f"`{ticket_number}`", inline=True)
    if owner_id:
        embed.add_field(name="Owner ID", value=f"`{owner_id}`", inline=True)
    if status:
        embed.add_field(name="Status", value=f"`{status}`", inline=True)
    if category:
        embed.add_field(name="Category", value=f"`{_truncate_for_discord(_safe_text(category), 200)}`", inline=True)
    if priority:
        embed.add_field(name="Priority", value=f"`{_truncate_for_discord(_safe_text(priority), 60)}`", inline=True)

    embed.add_field(name="Deleted/Finished By", value=f"`{deleted_by_text}`", inline=False)
    embed.add_field(name="Reason", value=f"`{_truncate_for_discord(reason_text, 900)}`", inline=False)
    embed.add_field(name="Topic", value=f"`{_truncate_for_discord(topic_text, 900)}`", inline=False)
    embed.set_footer(text=f"{_TRANSCRIPT_MARKER} • Generated at {_utc_iso(now_utc()) or ''}")
    return embed


# ============================================================
# Transcript posting
# ============================================================

async def post_transcript_to_channel(
    *,
    ticket_channel: discord.TextChannel,
    deleted_by: Optional[discord.Member | discord.User] = None,
    reason: Optional[str] = None,
) -> Tuple[Optional[discord.Message], Optional[str]]:
    lock = _channel_lock(_TRANSCRIPT_POST_LOCKS, ticket_channel.id)

    async with lock:
        row_before = await _ticket_row(ticket_channel.id)
        if _row_has_transcript(row_before):
            existing_url, existing_message_id, existing_channel_id = _row_transcript_payload(
                row_before,
                guild_id=ticket_channel.guild.id,
            )
            print(
                f"ℹ️ Transcript already attached for channel={ticket_channel.id} "
                f"url={existing_url!r} msg={existing_message_id!r} ch={existing_channel_id!r}"
            )
            return None, existing_url

        transcript_channel = await _get_transcripts_channel(ticket_channel.guild)
        if transcript_channel is None:
            print("⚠️ Transcript post skipped: transcripts channel unavailable.")
            return None, None

        txt_file, html_file, message_count = await generate_transcript_files(ticket_channel)
        row_before = await _ticket_row(ticket_channel.id)
        embed = _transcript_summary_embed(
            ticket_channel=ticket_channel,
            deleted_by=deleted_by,
            reason=reason,
            message_count=message_count,
            ticket_row=row_before,
        )

        try:
            posted = await transcript_channel.send(
                content=_TRANSCRIPT_MARKER,
                embed=embed,
                files=[txt_file, html_file],
                allowed_mentions=discord.AllowedMentions.none(),
            )
            jump_url = getattr(posted, "jump_url", None)

            await _attach_transcript_metadata_once(
                channel_id=int(ticket_channel.id),
                transcript_url=jump_url,
                transcript_message_id=int(getattr(posted, "id", 0) or 0) or None,
                transcript_channel_id=int(getattr(getattr(posted, "channel", None), "id", 0) or 0) or None,
                actor=deleted_by,
            )

            return posted, jump_url
        except Exception as e:
            print("❌ Failed posting transcript:", repr(e))
            return None, None


# ============================================================
# Delete flow
# ============================================================

async def delete_ticket_with_optional_transcript(
    *,
    channel: discord.TextChannel,
    deleted_by: Optional[discord.Member | discord.User] = None,
    is_ghost: bool = False,
    force_transcript_for_ghost: bool = False,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main deletion flow.

    Normal tickets:
      transcript MUST exist before delete

    Ghost tickets:
      transcript optional unless force_transcript_for_ghost=True

    Guarantees:
    - idempotent lock per channel
    - transcript is not reposted if one is already attached
    - DB is marked deleted before final channel delete
    """
    lock = _channel_lock(_DELETE_LOCKS, channel.id)

    async with lock:
        transcript_message: Optional[discord.Message] = None
        transcript_url: Optional[str] = None
        transcript_channel_id: Optional[int] = None
        transcript_message_id: Optional[int] = None

        row_before = await _ticket_row(channel.id)
        status_before = _row_status(row_before)

        if status_before == "deleted":
            existing_url, existing_message_id, existing_channel_id = _row_transcript_payload(
                row_before,
                guild_id=channel.guild.id,
            )
            return {
                "ok": False,
                "deleted": False,
                "db_marked_deleted": True,
                "transcript_required": False,
                "transcript_posted": bool(existing_url or existing_message_id or existing_channel_id),
                "transcript_already_existed": bool(existing_url or existing_message_id or existing_channel_id),
                "transcript_url": existing_url,
                "transcript_message_id": existing_message_id,
                "transcript_channel_id": existing_channel_id,
                "status_before": status_before,
                "reason": "Ticket is already marked deleted.",
            }

        already_had_transcript = _row_has_transcript(row_before)
        existing_url, existing_message_id, existing_channel_id = _row_transcript_payload(
            row_before,
            guild_id=channel.guild.id,
        )

        if already_had_transcript:
            transcript_url = existing_url
            transcript_message_id = existing_message_id
            transcript_channel_id = existing_channel_id

        should_post_transcript = (not is_ghost) or force_transcript_for_ghost

        if should_post_transcript and not already_had_transcript:
            transcript_message, transcript_url = await post_transcript_to_channel(
                ticket_channel=channel,
                deleted_by=deleted_by,
                reason=reason,
            )

            if transcript_message is None and not transcript_url and not is_ghost:
                return {
                    "ok": False,
                    "deleted": False,
                    "db_marked_deleted": False,
                    "transcript_required": True,
                    "transcript_posted": False,
                    "transcript_already_existed": False,
                    "transcript_url": None,
                    "transcript_message_id": None,
                    "transcript_channel_id": None,
                    "status_before": status_before,
                    "reason": "Transcript failed to post; ticket not deleted.",
                }

            if transcript_message is not None:
                try:
                    transcript_message_id = int(transcript_message.id)
                except Exception:
                    transcript_message_id = None

                try:
                    transcript_channel_id = int(transcript_message.channel.id)
                except Exception:
                    transcript_channel_id = None

        if should_post_transcript and not transcript_url and not transcript_message_id and not transcript_channel_id:
            healed_url, healed_message_id, healed_channel_id = await _heal_existing_transcript_metadata_if_missing(
                channel_id=int(channel.id),
                guild_id=int(channel.guild.id),
            )
            transcript_url = transcript_url or healed_url
            transcript_message_id = transcript_message_id or healed_message_id
            transcript_channel_id = transcript_channel_id or healed_channel_id

        db_marked_deleted = False
        try:
            db_marked_deleted = await mark_ticket_deleted(
                channel_id=channel.id,
                deleted_by=deleted_by,
                reason=reason or "Deleted",
            )
        except Exception as e:
            print("⚠️ Failed marking ticket deleted in DB before channel delete:", repr(e))
            db_marked_deleted = False

        try:
            await channel.delete(reason=reason or "Ticket deleted")
            return {
                "ok": True,
                "deleted": True,
                "db_marked_deleted": bool(db_marked_deleted),
                "transcript_required": bool(should_post_transcript),
                "transcript_posted": bool(transcript_message is not None or already_had_transcript or transcript_url),
                "transcript_already_existed": bool(already_had_transcript),
                "transcript_url": transcript_url,
                "transcript_message_id": transcript_message_id,
                "transcript_channel_id": transcript_channel_id,
                "status_before": status_before,
                "reason": None,
            }
        except discord.NotFound:
            return {
                "ok": True,
                "deleted": True,
                "db_marked_deleted": bool(db_marked_deleted),
                "transcript_required": bool(should_post_transcript),
                "transcript_posted": bool(transcript_message is not None or already_had_transcript or transcript_url),
                "transcript_already_existed": bool(already_had_transcript),
                "transcript_url": transcript_url,
                "transcript_message_id": transcript_message_id,
                "transcript_channel_id": transcript_channel_id,
                "status_before": status_before,
                "reason": None,
            }
        except Exception as e:
            print("❌ Failed deleting ticket channel:", repr(e))
            return {
                "ok": False,
                "deleted": False,
                "db_marked_deleted": bool(db_marked_deleted),
                "transcript_required": bool(should_post_transcript),
                "transcript_posted": bool(transcript_message is not None or already_had_transcript or transcript_url),
                "transcript_already_existed": bool(already_had_transcript),
                "transcript_url": transcript_url,
                "transcript_message_id": transcript_message_id,
                "transcript_channel_id": transcript_channel_id,
                "status_before": status_before,
                "reason": repr(e),
            }


async def staff_delete_closed_ticket(
    *,
    channel: discord.TextChannel,
    staff_member: discord.Member | discord.User,
    is_ghost: bool = False,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Use this when staff press the final Delete button inside a closed ticket.

    Hard guard:
    - refuses deletion if the ticket is no longer closed
    - blocks stale closed-panel clicks after a reopen
    """
    row = await _ticket_row(channel.id)
    status = _row_status(row)
    channel_name = _safe_text(getattr(channel, "name", "")).lower()

    if status == "deleted":
        existing_url, existing_message_id, existing_channel_id = _row_transcript_payload(
            row,
            guild_id=channel.guild.id,
        )
        return {
            "ok": False,
            "deleted": False,
            "db_marked_deleted": True,
            "transcript_required": False,
            "transcript_posted": bool(existing_url or existing_message_id or existing_channel_id),
            "transcript_already_existed": bool(existing_url or existing_message_id or existing_channel_id),
            "transcript_url": existing_url,
            "transcript_message_id": existing_message_id,
            "transcript_channel_id": existing_channel_id,
            "status_before": status,
            "reason": "Ticket is already deleted.",
        }

    looks_closed = bool(status == "closed" or channel_name.startswith("closed-"))
    if not looks_closed:
        existing_url, existing_message_id, existing_channel_id = _row_transcript_payload(
            row,
            guild_id=channel.guild.id,
        )
        return {
            "ok": False,
            "deleted": False,
            "db_marked_deleted": False,
            "transcript_required": True,
            "transcript_posted": _row_has_transcript(row),
            "transcript_already_existed": _row_has_transcript(row),
            "transcript_url": existing_url,
            "transcript_message_id": existing_message_id,
            "transcript_channel_id": existing_channel_id,
            "status_before": status,
            "reason": "Ticket is not closed anymore. Refusing stale closed-ticket delete action.",
        }

    return await delete_ticket_with_optional_transcript(
        channel=channel,
        deleted_by=staff_member,
        is_ghost=is_ghost,
        force_transcript_for_ghost=False,
        reason=reason or f"Deleted by staff: {staff_member}",
    )


__all__ = [
    "generate_transcript_files",
    "post_transcript_to_channel",
    "delete_ticket_with_optional_transcript",
    "staff_delete_closed_ticket",
]
