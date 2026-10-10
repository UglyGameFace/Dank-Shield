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


# Browser WebVTT requires text cues. Bitmap formats (PGS/VobSub) are not
# advertised as selectable until a safe OCR pipeline exists.
_TEXT_CODECS = frozenset({
    "subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text",
})
_MAX_TRACKS = 8
_MAX_VTT_BYTES = 1_000_000
_CACHE_SECONDS = 3600.0
_CACHE_SIZE = 8
_VTT_CACHE: dict[tuple[str, int], tuple[float, bytes]] = {}
_WORK_LOOP = None
_WORK_SEMAPHORE = None


def embedded_text_tracks(verified: object) -> list[dict[str, object]]:
    """List only trustworthy, decodable streams with real FFmpeg indexes."""
    from collections.abc import Mapping

    if not isinstance(verified, Mapping) or not verified.get("available"):
        return []
    rows = verified.get("subtitle_tracks")
    if not isinstance(rows, list):
        return []
    result: list[dict[str, object]] = []
    for index, item in enumerate(rows[:_MAX_TRACKS]):
        if not isinstance(item, Mapping):
            continue
        codec = str(item.get("codec") or "").strip().lower()
        if codec not in _TEXT_CODECS:
            continue
        lang = str(item.get("language") or "und").strip().lower()[:24]
        title = str(item.get("title") or "").strip()[:80]
        label = " • ".join(x for x in (lang, title or codec) if x)
        result.append({
            "index": index,
            "language": lang,
            "label": label or f"Subtitle {index + 1}",
            "codec": codec,
        })
    return result


def _work_semaphore():
    import asyncio

    global _WORK_LOOP, _WORK_SEMAPHORE
    loop = asyncio.get_running_loop()
    if loop is not _WORK_LOOP:
        _WORK_LOOP = loop
        _WORK_SEMAPHORE = asyncio.Semaphore(2)
    return _WORK_SEMAPHORE


async def _run_ffmpeg_srt(input_url: str, track: int) -> bytes:
    """Extract text only, bounded in time and memory. URL is internal only."""
    import asyncio
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("Subtitle extraction requires FFmpeg.")
    if not 0 <= track < _MAX_TRACKS:
        raise ValueError("invalid subtitle stream")
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", input_url, "-map", f"0:s:{track}",
        "-vn", "-an", "-f", "srt", "pipe:1",
    ]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        chunks: list[bytes] = []
        count = 0
        async with asyncio.timeout(35):
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > _MAX_VTT_BYTES:
                    raise ValueError("subtitle too large")
                chunks.append(chunk)
            status = await process.wait()
        if status != 0:
            raise RuntimeError("Subtitle track could not be extracted from this source.")
        return b"".join(chunks)
    finally:
        if process.returncode is None:
            process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass


async def embedded_vtt_for_session(
    *, token: str, track: int, signed_stream_url: str,
) -> bytes:
    """Deduplicate text extraction on this process, without exposing the source.

    The caller must authenticate the user and verify the requested track
    against the currently active torrent BEFORE calling this helper.
    """
    import asyncio
    import time
    from urllib.parse import urlsplit
    import os

    if not 0 <= track < _MAX_TRACKS or not token:
        raise ValueError("invalid subtitle request")
    key = (token, track)
    cached = _VTT_CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    parsed = urlsplit(signed_stream_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.path.startswith(f"/media/torrent/stream/{token}/")
        or not parsed.query
    ):
        raise ValueError("invalid signed Cinema stream URL")
    try:
        port = int(os.getenv("DANK_MEDIA_PORT", "8080"))
    except (TypeError, ValueError):
        port = 8080
    if not 0 < port < 65536:
        port = 8080
    # Never forward the public origin to FFmpeg: only our local signed
    # torrent HTTP endpoint can be the source, eliminating arbitrary fetches.
    source = f"http://127.0.0.1:{port}{parsed.path}?{parsed.query}"
    semaphore = _work_semaphore()
    async with asyncio.timeout(50):
        async with semaphore:
            cached = _VTT_CACHE.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            raw = await _run_ffmpeg_srt(source, track)
            result = srt_to_vtt(raw, max_bytes=_MAX_VTT_BYTES).encode("utf-8")
            if len(result) > _MAX_VTT_BYTES:
                raise ValueError("subtitle too large")
            # One media token can have multiple languages, but cache is bounded.
            if len(_VTT_CACHE) >= _CACHE_SIZE:
                expired = [key for key, (until, _v) in _VTT_CACHE.items()
                           if until <= time.monotonic()]
                for old in expired:
                    _VTT_CACHE.pop(old, None)
                if len(_VTT_CACHE) >= _CACHE_SIZE:
                    oldest = min(_VTT_CACHE, key=lambda k: _VTT_CACHE[k][0])
                    _VTT_CACHE.pop(oldest, None)
            _VTT_CACHE[key] = (time.monotonic() + _CACHE_SECONDS, result)
            return result
