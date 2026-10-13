from __future__ import annotations

"""One safe video-title formatter shared by Share Router routes and private DMs.

Titles are optional provider metadata, never a reason to refetch a video.
Do not echo provider URLs, fake a title, or allow injected Discord mentions.
"""

import html
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

_EMPTY_TITLES = {
    "video", "reel", "reels", "clip", "download", "media",
    "untitled", "facebook", "instagram", "tiktok", "youtube",
    "reddit", "twitter", "x", "streamable", "vimeo",
}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".gif", ".m4v"}
_MEDIA_LINK_RE = re.compile(r"https?://[^\s<>]+", re.I)
_MARKDOWN_RE = re.compile(r"([\\\x60*_~|\[\]<>])")


def clean_video_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    # Drop bidi overrides and invisible control chars; retain emoji ZWJ.
    normalized = html.unescape(value)
    normalized = "".join(
        " " if (unicodedata.category(char) in {"Cc", "Cs"} or
                char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
        else char for char in normalized
    )
    normalized = " ".join(normalized.split()).strip()
    if (not normalized or normalized.casefold() in _EMPTY_TITLES or
            _MEDIA_LINK_RE.search(normalized)):
        return ""
    if normalized.isdecimal():
        return ""
    return normalized[:160].rstrip()


def title_from_extraction(info: Any) -> str:
    if not isinstance(info, Mapping):
        return ""
    values = [info]
    entries = info.get("entries")
    if isinstance(entries, (list, tuple)) and len(entries) == 1 and isinstance(entries[0], Mapping):
        values = [entries[0], info]
    for entry in values:
        for key in ("title", "fulltitle"):
            title = clean_video_title(entry.get(key))
            if title:
                return title
    return ""


def title_from_filename(filename: Any) -> str:
    if not isinstance(filename, str) or not filename:
        return ""
    # Filename is not a URL; basename only, with no query or path leaking.
    name = PurePosixPath(unquote(urlsplit(filename).path).replace("\\", "/")).name
    stem, dot, suffix = name.rpartition(".")
    if not dot or ("." + suffix.casefold()) not in _VIDEO_SUFFIXES:
        return ""
    stem = stem.replace("_", " ").replace("-", " ").strip()
    if (not stem or re.fullmatch(r"[a-f0-9-]{16,}", stem, re.I) or
            re.fullmatch(r"\d{5,}", stem)):
        return ""
    return clean_video_title(stem)


def title_from_embeds(message: Any, source_url: str) -> str:
    """Only use Discord's metadata for a single posted media link.

    An unrelated embed in a multi-link post must not rename a downloaded file.
    Embeds without their own source URL qualify only when Discord included a
    video, as in a Facebook Reel preview.
    """
    content = str(getattr(message, "content", "") or "")
    links = _MEDIA_LINK_RE.findall(content)
    embeds = tuple(getattr(message, "embeds", ()) or ())
    if len(links) != 1 or not embeds:
        return ""
    source = urlsplit(source_url)
    for embed in embeds:
        embed_url = str(getattr(embed, "url", "") or "").strip()
        if embed_url:
            try:
                parsed = urlsplit(embed_url)
            except ValueError:
                continue
            # Exact permalink wins. For a short-link redirect, Discord may
            # point at the expanded video, but only when the embed has a video.
            same_link = (
                parsed.hostname == source.hostname and
                parsed.path.rstrip("/") == source.path.rstrip("/")
            )
            if not same_link and getattr(embed, "video", None) is None:
                continue
        elif getattr(embed, "video", None) is None:
            continue
        title = clean_video_title(getattr(embed, "title", None))
        if title:
            return title
    return ""


def title_prefix(value: Any) -> str:
    title = clean_video_title(value)
    if not title:
        return ""
    # Discord allowed_mentions.none() must still be used at send time.
    escaped = _MARKDOWN_RE.sub(r"\\\1", title).replace("@", "@\u200b")
    return f"🎬 **{escaped}**\n"
