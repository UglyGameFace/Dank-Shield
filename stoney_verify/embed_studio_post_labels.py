"""Human-readable labels for Post Builder published posts.

Every picker that lists a tracked Post Builder post (Sticky Posts,
Edit Posted, Polls, Live Text) builds its options here, so a post reads
the same everywhere: the post's own words first (embed title, else the
first line of its text, else the poll question), then where and when it
was posted in the option description.

Published records store almost no content of their own, so identity is
resolved in this order: poll question on the record, the live Discord
message when the caller fetched it, the saved source project stored on
the record at publish time, a form's title, and finally a plain
"Untitled post". The posted time always comes from the Discord message
snowflake, which is exact and needs no stored timestamp.

A record whose message is known to be gone — flagged ``message_missing``
by the delete listener / reconcile, or proven gone by a caller fetch —
is labelled "message deleted" instead of being presented as a normal
choice. Nothing here deletes or mutates a record; this module only
labels, plus one read-only fetch helper whose failures degrade to
record-only labels (a failed check never blocks or closes a picker).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

from .ui.picker import DankChoice, make_choice

DISCORD_EPOCH_MS = 1420070400000
MAX_LABEL = 100
MAX_DESCRIPTION = 100


@dataclass(frozen=True)
class FetchedPostInfo:
    """What one caller-side fetch learned about a tracked message."""

    exists: Optional[bool] = None  # None = unknown (fetch failed / not tried)
    title: str = ""
    first_line: str = ""


def snowflake_posted_at(message_id: Any) -> Optional[datetime]:
    """The instant a Discord message was posted, from its snowflake id."""
    try:
        value = int(message_id or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(((value >> 22) + DISCORD_EPOCH_MS) / 1000, tz=timezone.utc)


def format_posted_at(value: Optional[datetime]) -> str:
    """Plain UTC wording used in every picker: "Nov 19, 2:26 AM UTC".

    Select-option text does not render Discord timestamp markup, so the
    rest of Post Builder's per-viewer ``<t:...>`` style cannot be used
    here; a labelled UTC time is the honest, consistent choice.
    """
    if value is None:
        return "unknown time"
    utc = value.astimezone(timezone.utc)
    hour = utc.strftime("%I").lstrip("0") or "0"
    return f"{utc.strftime('%b')} {utc.day}, {hour}:{utc.strftime('%M %p')} UTC"


def _clean_line(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _project_identity(project: Any) -> str:
    """First identifying words of a stored Studio project, if any."""
    if not isinstance(project, Mapping):
        return ""
    for embed in project.get("embeds") or []:
        if isinstance(embed, Mapping):
            title = _clean_line(embed.get("title"))
            if title:
                return title
    content = str(project.get("content") or "")
    for line in content.splitlines():
        if line.strip():
            return _clean_line(line)
    for embed in project.get("embeds") or []:
        if isinstance(embed, Mapping):
            description = str(embed.get("description") or "")
            for line in description.splitlines():
                if line.strip():
                    return _clean_line(line)
    return ""


def post_identity(
    item: Mapping[str, Any], *, fetched: Optional[FetchedPostInfo] = None,
) -> str:
    """The post's own identifying words, best source first."""
    poll = item.get("poll") if isinstance(item.get("poll"), Mapping) else None
    if poll:
        question = _clean_line(poll.get("question"))
        if question:
            return question
    if fetched is not None:
        if fetched.title:
            return fetched.title
        if fetched.first_line:
            return fetched.first_line
    for key in ("repost_source", "live_source"):
        identity = _project_identity(item.get(key))
        if identity:
            return identity
    form = item.get("form") if isinstance(item.get("form"), Mapping) else None
    if form:
        title = _clean_line(form.get("title"))
        if title:
            return title
    return "Untitled post"


def record_message_missing(
    item: Mapping[str, Any], *, fetched: Optional[FetchedPostInfo] = None,
) -> bool:
    """True when the tracked Discord message is known to be gone."""
    if bool(item.get("message_missing")):
        return True
    return fetched is not None and fetched.exists is False


def flag_record_missing(item: dict[str, Any]) -> bool:
    """Mark a published record's message as deleted. Returns True if changed.

    A post that was sticky keeps that fact as ``was_sticky`` while its
    live sticky state turns off — a deleted copy cannot keep moving —
    so a later Repost can restore stickiness in full. Pure record
    bookkeeping: callers own loading and saving the library.
    """
    changed = False
    sticky = item.get("sticky") if isinstance(item.get("sticky"), Mapping) else {}
    if sticky.get("enabled"):
        item["was_sticky"] = True
        item["sticky"] = {"enabled": False, "last_sent_at": None}
        changed = True
    if not item.get("message_missing"):
        item["message_missing"] = True
        item["message_missing_at"] = datetime.now(timezone.utc).isoformat()
        changed = True
    return changed


def clear_missing_flag(item: dict[str, Any]) -> bool:
    """Un-flag a record whose message is back (or was never gone)."""
    changed = False
    if item.get("message_missing"):
        item["message_missing"] = False
        changed = True
    if item.pop("message_missing_at", None) is not None:
        changed = True
    return changed


def channel_label(item: Mapping[str, Any], channel_names: Optional[Mapping[int, str]]) -> str:
    """'#channel-name', falling back to the id's last four digits."""
    try:
        channel_id = int(item.get("channel_id") or 0)
    except (TypeError, ValueError):
        channel_id = 0
    name = ""
    if channel_names:
        name = str(channel_names.get(channel_id) or "").strip()
    if name:
        return f"#{name.lstrip('#')}"
    if channel_id > 0:
        return f"#channel-{str(channel_id)[-4:]}"
    return "#unknown-channel"


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def build_published_post_choices(
    records: Any,
    *,
    channel_names: Optional[Mapping[int, str]] = None,
    fetched: Optional[Mapping[str, FetchedPostInfo]] = None,
    prefix_for: Any = None,
    extra_description_for: Any = None,
) -> list[DankChoice]:
    """Build picker options for ``(post_key, record)`` pairs.

    Label: the post's identity first, with any per-picker prefix (the
    📌 sticky marker, a poll state emoji) in front of it — never a raw
    channel id. Description: "#channel • posted <time> • msg …<tail>",
    plus per-picker extras and a plain "message deleted" note when the
    tracked message is known to be gone.

    No two options in the set ever render identically: posts that share
    an identity get their posted time appended to the label, and if
    that still collides, the message-id tail as well.
    """
    rows: list[dict[str, Any]] = []
    fetched = fetched or {}
    for key, item in list(records or []):
        if not isinstance(item, Mapping):
            continue
        post_key = str(key)
        info = fetched.get(post_key)
        missing = record_message_missing(item, fetched=info)
        identity = post_identity(item, fetched=info)
        if missing and info is not None and info.exists is False \
                and not item.get("poll") and not _project_identity(item.get("repost_source")) \
                and not _project_identity(item.get("live_source")):
            # Nothing trustworthy left to name it by.
            identity = "Deleted post"
        prefix = ""
        if prefix_for is not None:
            prefix = str(prefix_for(post_key, item) or "")
        label = f"{prefix} {identity}" if prefix and not identity.startswith(prefix) else identity
        if missing:
            label = f"{label} — message deleted"
        posted = snowflake_posted_at(item.get("message_id"))
        posted_text = format_posted_at(posted)
        description_parts = [
            channel_label(item, channel_names),
            f"posted {posted_text}",
        ]
        if extra_description_for is not None:
            extra = str(extra_description_for(post_key, item) or "").strip()
            if extra:
                description_parts.append(extra)
        if missing:
            description_parts.append("message deleted")
        rows.append({
            "key": post_key,
            "item": item,
            "label": label,
            "posted_text": posted_text,
            "description_parts": description_parts,
            "message_tail": str(item.get("message_id") or "")[-6:],
        })

    # Same-text disambiguation: identical labels in one picker are the
    # exact failure Felix screenshotted, so duplicates earn their
    # posted time in the label itself, then the message tail.
    label_counts: dict[str, int] = {}
    for row in rows:
        label_counts[row["label"]] = label_counts.get(row["label"], 0) + 1
    for row in rows:
        if label_counts[row["label"]] > 1:
            row["label"] = f"{row['label']} • {row['posted_text']}"
    pair_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        pair = (row["label"], " • ".join(row["description_parts"]))
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
    choices: list[DankChoice] = []
    for row in rows:
        description = " • ".join(row["description_parts"])
        if pair_counts[(row["label"], description)] > 1 and row["message_tail"]:
            row["label"] = f"{row['label']} • …{row['message_tail']}"
            description = f"{description} • msg …{row['message_tail']}"
        elif row["message_tail"]:
            description = f"{description} • msg …{row['message_tail']}"
        choices.append(make_choice(
            _clip(row["label"], MAX_LABEL), row["key"],
            description=_clip(description, MAX_DESCRIPTION),
        ))
    return choices


def channel_names_from_guild(guild: Any) -> dict[int, str]:
    """Resolve channel names from a guild object (cache only, no fetch)."""
    names: dict[int, str] = {}
    if guild is None:
        return names
    for channel in list(getattr(guild, "text_channels", None) or []):
        try:
            names[int(channel.id)] = str(channel.name or "")
        except (TypeError, ValueError, AttributeError):
            continue
    return names


async def collect_published_post_info(
    guild: Any, records: Any, *, timeout: float = 6.0,
) -> dict[str, FetchedPostInfo]:
    """Best-effort fetch of each tracked message's title / first line.

    Runs the fetches together and never raises: a message that cannot
    be checked comes back as ``exists=None`` (unknown), so the picker
    still opens with record-only labels. Only a proven ``NotFound``
    marks a post as gone. Flagged records are not fetched at all —
    the flag already knows, and Repost re-checks before acting.
    """
    results: dict[str, FetchedPostInfo] = {}
    if guild is None:
        return results
    from .startup_guards.discord_api_safety import fetch_message_with_api_safety

    async def fetch_one(post_key: str, item: Mapping[str, Any]) -> None:
        if bool(item.get("message_missing")):
            results[post_key] = FetchedPostInfo(exists=False)
            return
        try:
            channel_id = int(item.get("channel_id") or 0)
            message_id = int(item.get("message_id") or 0)
        except (TypeError, ValueError):
            return
        if channel_id <= 0 or message_id <= 0:
            return
        channel = guild.get_channel(channel_id) if hasattr(guild, "get_channel") else None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await asyncio.wait_for(
                fetch_message_with_api_safety(
                    channel, message_id, label="studio picker labels"),
                timeout=timeout,
            )
        except discord.NotFound:
            results[post_key] = FetchedPostInfo(exists=False)
            return
        except Exception:
            return
        title = ""
        for embed in list(getattr(message, "embeds", None) or []):
            data = embed.to_dict() if hasattr(embed, "to_dict") else {}
            title = _clean_line(data.get("title"))
            if title:
                break
        first_line = ""
        for line in str(getattr(message, "content", "") or "").splitlines():
            if line.strip():
                first_line = _clean_line(line)
                break
        results[post_key] = FetchedPostInfo(exists=True, title=title, first_line=first_line)

    await asyncio.gather(*[
        fetch_one(str(key), item)
        for key, item in list(records or [])
        if isinstance(item, Mapping)
    ], return_exceptions=True)
    return results


__all__ = [
    "FetchedPostInfo",
    "build_published_post_choices",
    "channel_label",
    "channel_names_from_guild",
    "clear_missing_flag",
    "collect_published_post_info",
    "flag_record_missing",
    "format_posted_at",
    "post_identity",
    "record_message_missing",
    "snowflake_posted_at",
]
