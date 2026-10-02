from __future__ import annotations

"""Verified media probing plus clearly-labelled release-name metadata."""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional

_RELEASE_SOURCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:HDCAM|CAMRIP|CAM)\b", "CAM"),
    (r"\b(?:TELESYNC|HDTS|TS)\b", "TS"),
    (r"\b(?:TELECINE|HDTC|TC)\b", "TC"),
    (r"\b(?:DVDSCR|DVD[-_. ]?SCR)\b", "DVDSCR"),
    (r"\b(?:DVDRIP|DVD[-_. ]?RIP)\b", "DVDRip"),
    (r"\b(?:WEB[-_. ]?DL|WEBDL)\b", "WEB-DL"),
    (r"\bWEB[-_. ]?RIP\b", "WEBRip"),
    (r"\bHDTV\b", "HDTV"),
    (r"\b(?:BLU[-_. ]?RAY|BLURAY)\b", "BluRay"),
    (r"\b(?:BDRIP|BRRIP)\b", "BRRip"),
    (r"\bREMUX\b", "REMUX"),
    (r"\bR5\b", "R5"),
)

_RESOLUTION_RE = re.compile(r"\b(4320p|2160p|1440p|1080p|1080i|720p|576p|480p)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_EPISODE_RE = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.IGNORECASE)
_ALT_EPISODE_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.IGNORECASE)
_RELEASE_GROUP_RE = re.compile(r"-([A-Za-z0-9][A-Za-z0-9._-]{1,40})$")
_TOKEN_SPLIT_RE = re.compile(r"[._]+|\s+")

_AUDIO_TAGS: tuple[tuple[str, str], ...] = (
    (r"\b(?:DDP|EAC3)[ ._-]?7[ ._-]?1\b", "DDP 7.1"),
    (r"\b(?:DDP|EAC3)[ ._-]?5[ ._-]?1\b", "DDP 5.1"),
    (r"\b(?:DD|AC3)[ ._-]?5[ ._-]?1\b", "DD 5.1"),
    (r"\bDTS[-_. ]?HD[-_. ]?MA\b", "DTS-HD MA"),
    (r"\bDTS[-_. ]?X\b", "DTS:X"),
    (r"\bTRUEHD\b", "TrueHD"),
    (r"\bATMOS\b", "Atmos"),
    (r"\bFLAC\b", "FLAC"),
    (r"\bAAC\b", "AAC"),
    (r"\bOPUS\b", "Opus"),
)

_VIDEO_TAGS: tuple[tuple[str, str], ...] = (
    (r"\b(?:X265|H[ ._-]?265|HEVC)\b", "HEVC"),
    (r"\b(?:X264|H[ ._-]?264|AVC)\b", "H.264"),
    (r"\bAV1\b", "AV1"),
    (r"\bVP9\b", "VP9"),
)

_HDR_TAGS: tuple[tuple[str, str], ...] = (
    (r"\b(?:DOLBY[-_. ]?VISION|DOVI|DV)\b", "Dolby Vision"),
    (r"\bHDR10\+\b", "HDR10+"),
    (r"\bHDR10\b", "HDR10"),
    (r"\bHDR\b", "HDR"),
    (r"\bHLG\b", "HLG"),
)

_STOP_MARKERS = {
    "4320p", "2160p", "1440p", "1080p", "1080i", "720p", "576p", "480p",
    "web", "webrip", "webdl", "bluray", "brrip", "bdrip", "dvdrip", "dvd",
    "hdtv", "remux", "cam", "hdcam", "ts", "hdts", "tc", "hdtc",
}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _first_tag(text: str, patterns: tuple[tuple[str, str], ...]) -> str:
    for pattern, label in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return ""


def _all_tags(text: str, patterns: tuple[tuple[str, str], ...]) -> list[str]:
    return _dedupe(
        [
            label
            for pattern, label in patterns
            if re.search(pattern, text, flags=re.IGNORECASE)
        ]
    )


def parse_release_name(filename: str) -> dict[str, Any]:
    """Parse scene/release-name claims without presenting them as verified facts."""

    raw_name = Path(str(filename or "")).name
    stem = Path(raw_name).stem
    normalized = re.sub(r"[._]+", " ", stem)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    year_match = _YEAR_RE.search(normalized)
    episode = _EPISODE_RE.search(normalized) or _ALT_EPISODE_RE.search(normalized)
    resolution_match = _RESOLUTION_RE.search(normalized)
    release_group_match = _RELEASE_GROUP_RE.search(stem)

    source = _first_tag(normalized, _RELEASE_SOURCE_PATTERNS)
    all_sources = _all_tags(normalized, _RELEASE_SOURCE_PATTERNS)

    title_boundary = len(normalized)
    for match in (year_match, episode, resolution_match):
        if match is not None:
            title_boundary = min(title_boundary, match.start())

    # If no year/episode/resolution appears, stop title recovery at the first
    # obvious release-source token.
    if title_boundary == len(normalized):
        tokens = normalized.split()
        cut = len(tokens)
        for idx, token in enumerate(tokens):
            collapsed = re.sub(r"[^a-z0-9]+", "", token.lower())
            if collapsed in _STOP_MARKERS:
                cut = idx
                break
        title = " ".join(tokens[:cut]).strip()
    else:
        title = normalized[:title_boundary].strip(" -._")

    if not title:
        title = normalized

    result: dict[str, Any] = {
        "origin": "release_name",
        "raw_filename": raw_name,
        "title": title,
        "year": int(year_match.group(1)) if year_match else None,
        "season": int(episode.group(1)) if episode else None,
        "episode": int(episode.group(2)) if episode else None,
        "resolution": resolution_match.group(1).lower() if resolution_match else "",
        "source": source,
        "source_tags": all_sources,
        "video_tags": _all_tags(normalized, _VIDEO_TAGS),
        "audio_tags": _all_tags(normalized, _AUDIO_TAGS),
        "hdr_tags": _all_tags(normalized, _HDR_TAGS),
        "release_group": release_group_match.group(1) if release_group_match else "",
    }
    return result


def _safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except Exception:
        return None
    return result if result >= 0 else None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _rational(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            denominator = float(right)
            if denominator == 0:
                return None
            return float(left) / denominator
        except Exception:
            return None
    return _safe_float(text)


def _stream_language(stream: Mapping[str, Any]) -> str:
    tags = stream.get("tags")
    if not isinstance(tags, Mapping):
        return ""
    return str(tags.get("language") or "").strip()


def _stream_title(stream: Mapping[str, Any]) -> str:
    tags = stream.get("tags")
    if not isinstance(tags, Mapping):
        return ""
    return str(tags.get("title") or "").strip()


def _bit_depth(stream: Mapping[str, Any]) -> Optional[int]:
    direct = _safe_int(stream.get("bits_per_raw_sample"))
    if direct:
        return direct
    pix_fmt = str(stream.get("pix_fmt") or "").lower()
    match = re.search(r"(?:p|gbrp)(9|10|12|14|16)(?:le|be)?$", pix_fmt)
    if match:
        return int(match.group(1))
    return None


def _verified_hdr(video: Mapping[str, Any]) -> list[str]:
    tags: list[str] = []
    transfer = str(video.get("color_transfer") or "").lower()
    if transfer == "smpte2084":
        tags.append("HDR10/PQ")
    elif transfer in {"arib-std-b67", "arib_std_b67"}:
        tags.append("HLG")

    side_data = video.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if not isinstance(item, Mapping):
                continue
            text = " ".join(str(value or "") for value in item.values()).lower()
            if "dovi" in text or "dolby vision" in text:
                tags.append("Dolby Vision")
            if "hdr10+" in text or "dynamic hdr plus" in text:
                tags.append("HDR10+")
            elif "mastering display metadata" in text and "HDR10/PQ" not in tags:
                tags.append("HDR")
    return _dedupe(tags)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_ffprobe_payload(
    payload: Mapping[str, Any],
    *,
    filename: str = "",
    file_size: Optional[int] = None,
) -> dict[str, Any]:
    streams = payload.get("streams")
    stream_rows = [item for item in streams if isinstance(item, Mapping)] if isinstance(streams, list) else []
    videos = [item for item in stream_rows if str(item.get("codec_type") or "") == "video"]
    audios = [item for item in stream_rows if str(item.get("codec_type") or "") == "audio"]
    subtitles = [item for item in stream_rows if str(item.get("codec_type") or "") == "subtitle"]
    video = videos[0] if videos else {}

    fmt = payload.get("format")
    format_row = fmt if isinstance(fmt, Mapping) else {}
    format_tags = format_row.get("tags")
    tags = format_tags if isinstance(format_tags, Mapping) else {}

    duration = _safe_float(format_row.get("duration"))
    if duration is None:
        candidates = [_safe_float(item.get("duration")) for item in stream_rows]
        duration = max((item for item in candidates if item is not None), default=None)

    fps = _rational(video.get("avg_frame_rate")) or _rational(video.get("r_frame_rate"))
    bitrate = _safe_int(format_row.get("bit_rate"))
    width = _safe_int(video.get("width"))
    height = _safe_int(video.get("height"))

    audio_rows = [
        {
            "codec": str(item.get("codec_name") or ""),
            "profile": str(item.get("profile") or ""),
            "channels": _safe_int(item.get("channels")),
            "layout": str(item.get("channel_layout") or ""),
            "sample_rate": _safe_int(item.get("sample_rate")),
            "language": _stream_language(item),
            "title": _stream_title(item),
        }
        for item in audios
    ]
    subtitle_rows = [
        {
            "codec": str(item.get("codec_name") or ""),
            "language": _stream_language(item),
            "title": _stream_title(item),
        }
        for item in subtitles
    ]

    embedded_year: Optional[int] = None
    for candidate in (tags.get("date"), tags.get("year"), tags.get("creation_time")):
        match = _YEAR_RE.search(str(candidate or ""))
        if match:
            embedded_year = int(match.group(1))
            break

    return {
        "origin": "verified_file",
        "filename": str(filename or ""),
        "file_size": int(file_size) if file_size is not None else None,
        "container": str(format_row.get("format_name") or ""),
        "container_long_name": str(format_row.get("format_long_name") or ""),
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "duration": format_duration(duration),
        "bitrate": bitrate,
        "embedded_title": str(tags.get("title") or "").strip(),
        "embedded_year": embedded_year,
        "video": {
            "codec": str(video.get("codec_name") or ""),
            "codec_long_name": str(video.get("codec_long_name") or ""),
            "profile": str(video.get("profile") or ""),
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}" if width and height else "",
            "fps": round(fps, 3) if fps is not None else None,
            "pixel_format": str(video.get("pix_fmt") or ""),
            "bit_depth": _bit_depth(video),
            "color_space": str(video.get("color_space") or ""),
            "color_transfer": str(video.get("color_transfer") or ""),
            "color_primaries": str(video.get("color_primaries") or ""),
            "field_order": str(video.get("field_order") or ""),
            "hdr": _verified_hdr(video),
        },
        "audio_tracks": audio_rows,
        "subtitle_tracks": subtitle_rows,
        "audio_languages": _dedupe([row["language"] for row in audio_rows]),
        "subtitle_languages": _dedupe([row["language"] for row in subtitle_rows]),
        "chapters": len(payload.get("chapters") or []) if isinstance(payload.get("chapters"), list) else 0,
    }


def _pyav_stream_tags(stream: Any) -> dict[str, str]:
    raw = getattr(stream, "metadata", None)
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key)[:80]: str(value)[:500]
        for key, value in list(raw.items())[:64]
        if str(key).strip() and str(value).strip()
    }


def _pyav_codec_names(stream: Any) -> tuple[str, str]:
    context = getattr(stream, "codec_context", None)
    codec = getattr(context, "codec", None)
    short = str(
        getattr(codec, "name", None)
        or getattr(context, "name", None)
        or ""
    )
    long_name = str(getattr(codec, "long_name", None) or "")
    return short, long_name


def _pyav_duration_seconds(stream: Any) -> Optional[float]:
    duration = getattr(stream, "duration", None)
    time_base = getattr(stream, "time_base", None)
    if duration is None or time_base is None:
        return None
    try:
        return round(float(duration * time_base), 3)
    except Exception:
        return None


def _pyav_hdr_signals(stream: Any) -> list[str]:
    context = getattr(stream, "codec_context", None)
    transfer = str(getattr(context, "color_trc", "") or "").lower()
    primaries = str(getattr(context, "color_primaries", "") or "").lower()
    pixel_format = str(getattr(context, "pix_fmt", "") or "").lower()
    tags: list[str] = []

    if "2084" in transfer or transfer == "pq":
        tags.append("HDR10/PQ")
    if "b67" in transfer or "arib" in transfer:
        tags.append("HLG")
    if "bt2020" in primaries and not tags:
        tags.append("BT.2020")

    for item in list(getattr(context, "coded_side_data", None) or []):
        text = str(getattr(item, "type", item) or "").lower()
        if "dovi" in text or "dolby" in text:
            tags.append("Dolby Vision")
        if "dynamic hdr" in text or "hdr10+" in text:
            tags.append("HDR10+")
        if "mastering display" in text:
            tags.append("HDR mastering metadata")

    if re.search(r"(?:10|12)(?:le|be)?$", pixel_format):
        tags.append(f"{pixel_format} pixel format")

    return _dedupe(tags)


def parse_pyav_container(
    container: Any,
    *,
    filename: str = "",
    file_size: Optional[int] = None,
    av_time_base: int = 1_000_000,
) -> dict[str, Any]:
    streams = list(getattr(container, "streams", None) or [])
    videos = [item for item in streams if str(getattr(item, "type", "") or "") == "video"]
    audios = [item for item in streams if str(getattr(item, "type", "") or "") == "audio"]
    subtitles = [item for item in streams if str(getattr(item, "type", "") or "") == "subtitle"]

    format_obj = getattr(container, "format", None)
    raw_tags = getattr(container, "metadata", None)
    tags = (
        {
            str(key)[:80]: str(value)[:500]
            for key, value in list(raw_tags.items())[:64]
            if str(key).strip() and str(value).strip()
        }
        if isinstance(raw_tags, Mapping)
        else {}
    )

    duration_raw = getattr(container, "duration", None)
    duration: Optional[float] = None
    if duration_raw is not None:
        try:
            duration = round(float(duration_raw) / float(av_time_base), 3)
        except Exception:
            duration = None
    if duration is None:
        stream_durations = [
            value
            for value in (_pyav_duration_seconds(item) for item in streams)
            if value is not None
        ]
        duration = max(stream_durations, default=None)

    def _language(stream: Any) -> str:
        stream_tags = _pyav_stream_tags(stream)
        return str(
            getattr(stream, "language", None)
            or stream_tags.get("language")
            or stream_tags.get("LANGUAGE")
            or ""
        ).strip()

    def _title(stream: Any) -> str:
        stream_tags = _pyav_stream_tags(stream)
        return str(stream_tags.get("title") or stream_tags.get("TITLE") or "").strip()

    audio_rows: list[dict[str, Any]] = []
    for stream in audios:
        context = getattr(stream, "codec_context", None)
        codec, codec_long = _pyav_codec_names(stream)
        layout = getattr(context, "layout", None)
        audio_rows.append(
            {
                "codec": codec,
                "codec_long_name": codec_long,
                "profile": str(
                    getattr(stream, "profile", None)
                    or getattr(context, "profile", None)
                    or ""
                ),
                "channels": _safe_int(getattr(context, "channels", None)),
                "layout": str(getattr(layout, "name", None) or ""),
                "sample_rate": _safe_int(getattr(context, "sample_rate", None)),
                "bitrate": _safe_int(getattr(context, "bit_rate", None)),
                "language": _language(stream),
                "title": _title(stream),
                "duration_seconds": _pyav_duration_seconds(stream),
            }
        )

    subtitle_rows: list[dict[str, Any]] = []
    for stream in subtitles:
        codec, codec_long = _pyav_codec_names(stream)
        subtitle_rows.append(
            {
                "codec": codec,
                "codec_long_name": codec_long,
                "language": _language(stream),
                "title": _title(stream),
            }
        )

    video: dict[str, Any] = {}
    if videos:
        stream = videos[0]
        context = getattr(stream, "codec_context", None)
        codec, codec_long = _pyav_codec_names(stream)
        width = _safe_int(getattr(context, "width", None))
        height = _safe_int(getattr(context, "height", None))
        average_rate = getattr(stream, "average_rate", None)
        fps = None
        try:
            if average_rate:
                fps = round(float(average_rate), 3)
        except Exception:
            fps = None

        pixel_format = str(getattr(context, "pix_fmt", None) or "")
        bit_depth = None
        match = re.search(r"(?:p|gbrp)(9|10|12|14|16)(?:le|be)?$", pixel_format.lower())
        if match:
            bit_depth = int(match.group(1))

        video = {
            "codec": codec,
            "codec_long_name": codec_long,
            "profile": str(
                getattr(stream, "profile", None)
                or getattr(context, "profile", None)
                or ""
            ),
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}" if width and height else "",
            "fps": fps,
            "pixel_format": pixel_format,
            "bit_depth": bit_depth,
            "color_space": str(getattr(context, "colorspace", None) or ""),
            "color_transfer": str(getattr(context, "color_trc", None) or ""),
            "color_primaries": str(getattr(context, "color_primaries", None) or ""),
            "field_order": str(getattr(context, "field_order", None) or ""),
            "hdr": _pyav_hdr_signals(stream),
            "bitrate": _safe_int(getattr(context, "bit_rate", None)),
            "duration_seconds": _pyav_duration_seconds(stream),
        }

    embedded_year: Optional[int] = None
    for candidate in (
        tags.get("date"),
        tags.get("year"),
        tags.get("creation_time"),
        tags.get("DATE"),
        tags.get("YEAR"),
    ):
        match = _YEAR_RE.search(str(candidate or ""))
        if match:
            embedded_year = int(match.group(1))
            break

    chapters_raw = list(getattr(container, "chapters", None) or [])
    chapters: list[dict[str, Any]] = []
    for index, chapter in enumerate(chapters_raw[:200]):
        chapter_tags = getattr(chapter, "metadata", None)
        chapter_title = ""
        if isinstance(chapter_tags, Mapping):
            chapter_title = str(
                chapter_tags.get("title")
                or chapter_tags.get("TITLE")
                or ""
            )[:180]
        start = None
        end = None
        try:
            start = round(float(chapter.start * chapter.time_base), 3)
        except Exception:
            pass
        try:
            end = round(float(chapter.end * chapter.time_base), 3)
        except Exception:
            pass
        chapters.append(
            {
                "index": index,
                "title": chapter_title,
                "start_seconds": start,
                "end_seconds": end,
            }
        )

    return {
        "origin": "verified_file",
        "probe_engine": "pyav",
        "filename": str(filename or ""),
        "file_size": (
            int(file_size)
            if file_size is not None
            else _safe_int(getattr(container, "size", None))
        ),
        "container": str(getattr(format_obj, "name", None) or ""),
        "container_long_name": str(getattr(format_obj, "long_name", None) or ""),
        "duration_seconds": duration,
        "duration": format_duration(duration),
        "bitrate": _safe_int(getattr(container, "bit_rate", None)),
        "embedded_title": str(
            tags.get("title")
            or tags.get("TITLE")
            or tags.get("name")
            or tags.get("NAME")
            or ""
        ).strip(),
        "embedded_year": embedded_year,
        "container_tags": tags,
        "video": video,
        "audio_tracks": audio_rows,
        "subtitle_tracks": subtitle_rows,
        "audio_languages": _dedupe([row["language"] for row in audio_rows]),
        "subtitle_languages": _dedupe([row["language"] for row in subtitle_rows]),
        "chapters": chapters,
        "chapter_count": len(chapters_raw),
    }


def _probe_pyav_sync(file_path: Path, filename: str) -> dict[str, Any]:
    import av

    with av.open(
        str(file_path),
        mode="r",
        options={
            "probesize": str(8 * 1024 * 1024),
            "analyzeduration": "5000000",
        },
    ) as container:
        try:
            size = file_path.stat().st_size
        except OSError:
            size = None
        result = parse_pyav_container(
            container,
            filename=filename or file_path.name,
            file_size=size,
            av_time_base=int(getattr(av, "time_base", 1_000_000) or 1_000_000),
        )
        result["available"] = bool(
            result.get("video", {}).get("codec")
            or result.get("duration_seconds")
        )
        return result


async def _probe_with_ffprobe(
    file_path: Path,
    *,
    filename: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "origin": "verified_file",
            "available": False,
            "reason": "ffprobe_not_installed",
        }

    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(file_path),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=max(2.0, min(float(timeout_seconds), 15.0)),
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return {
            "origin": "verified_file",
            "available": False,
            "reason": "ffprobe_timeout",
        }

    if process.returncode != 0:
        return {
            "origin": "verified_file",
            "available": False,
            "reason": "ffprobe_incomplete",
            "detail": (stderr.decode("utf-8", "replace")[:240] if stderr else ""),
        }

    try:
        payload = json.loads(stdout.decode("utf-8", "replace"))
    except Exception:
        return {
            "origin": "verified_file",
            "available": False,
            "reason": "ffprobe_invalid_json",
        }

    try:
        size = file_path.stat().st_size
    except OSError:
        size = None

    result = parse_ffprobe_payload(
        payload if isinstance(payload, Mapping) else {},
        filename=filename or file_path.name,
        file_size=size,
    )
    result["probe_engine"] = "ffprobe"
    result["available"] = bool(
        result.get("video", {}).get("codec")
        or result.get("duration_seconds")
    )
    return result


async def probe_media_file(
    path: Path,
    *,
    filename: str = "",
    timeout_seconds: float = 6.0,
) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {
            "origin": "verified_file",
            "available": False,
            "reason": "file_not_ready",
        }

    pyav_error = ""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _probe_pyav_sync,
                file_path,
                filename or file_path.name,
            ),
            timeout=max(2.0, min(float(timeout_seconds), 15.0)),
        )
        if result.get("available"):
            return result
        pyav_error = str(result.get("reason") or "pyav_incomplete")
    except asyncio.TimeoutError:
        pyav_error = "pyav_timeout"
    except Exception as exc:
        pyav_error = f"pyav_{type(exc).__name__}"

    # Compatibility fallback for hosts that already provide ffprobe. Production
    # does not depend on it because PyAV 17.1.0 carries bundled FFmpeg libraries.
    fallback = await _probe_with_ffprobe(
        file_path,
        filename=filename or file_path.name,
        timeout_seconds=timeout_seconds,
    )
    if fallback.get("available"):
        return fallback

    fallback["pyav_reason"] = pyav_error
    return fallback


__all__ = [
    "format_duration",
    "parse_ffprobe_payload",
    "parse_pyav_container",
    "parse_release_name",
    "probe_media_file",
]
