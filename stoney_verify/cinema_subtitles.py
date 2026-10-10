"""Safe standalone subtitle parsing and WebVTT normalization for Cinema.

External provider fetches are deliberately not part of this module.
"""
from __future__ import annotations

import re

_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})$")
_ARROW = re.compile(r"\s+-->\s+")


def _timestamp(value: str) -> str:
    match = _TIME.fullmatch(value.strip())
    if not match:
        raise ValueError("invalid subtitle timestamp")
    hours, minutes, seconds, millis = match.groups()
    hh = int(hours or 0)
    mm, ss = int(minutes), int(seconds)
    if mm > 59 or ss > 59:
        raise ValueError("invalid subtitle timestamp")
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{int(millis.ljust(3, '0')):03d}"


def srt_to_vtt(raw: bytes, *, max_bytes: int = 2_000_000) -> str:
    """Convert plain SRT text to browser WebVTT; reject HTML/script injection."""
    if len(raw) > max_bytes:
        raise ValueError("subtitle too large")
    source = raw.decode("utf-8-sig", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in source:
        raise ValueError("invalid subtitle data")
    blocks = []
    for block in re.split(r"\n\s*\n", source.strip()):
        lines = block.split("\n")
        if lines and lines[0].strip().isdigit():
            lines.pop(0)
        if len(lines) < 2:
            continue
        timing = _ARROW.split(lines[0].strip(), maxsplit=1)
        if len(timing) != 2:
            continue
        start = _timestamp(timing[0])
        end = _timestamp(timing[1])
        if end <= start:
            raise ValueError("invalid subtitle cue duration")
        text = "\n".join(lines[1:]).strip()
        if not text:
            continue
        # Display literal text rather than trusting remote subtitle markup.
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        blocks.append(f"{start} --> {end}\n{text}")
        if len(blocks) > 20_000:
            raise ValueError("too many subtitle cues")
    if not blocks:
        raise ValueError("no usable subtitle cues")
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"
