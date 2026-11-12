from __future__ import annotations

"""Bounded, private audio preparation for native Discord Soundboard sounds."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile

import discord

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOUND_BYTES = 512 * 1024
MAX_CLIP_SECONDS = 5.0  # Discord accepts up to 5.2 seconds.
MAX_START_SECONDS = 300
MAX_BATCH = 3
_CONVERSION_SLOTS = asyncio.Semaphore(2)
_ALLOWED_EXTENSIONS = {
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus",
    ".mp4", ".mov", ".webm",
}


class SoundboardInputError(ValueError):
    """Expected, actionable user upload error."""


@dataclass
class PreparedSound:
    filename: str
    name: str
    data: bytes
    emoji: str | None = None
    volume: float = 1.0


def clean_sound_name(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[\x00-\x1f\x7f]", "", stem)
    stem = re.sub(r"\s+", " ", stem.replace("_", " ").replace("-", " ")).strip()
    return (stem or "New sound")[:32].strip() or "New sound"


def validate_sound_name(name: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()
    if not 2 <= len(value) <= 32:
        raise SoundboardInputError("Sound names must be 2–32 characters.")
    return value


def validate_upload(attachment: discord.Attachment) -> None:
    filename = str(getattr(attachment, "filename", "") or "")
    if Path(filename).suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise SoundboardInputError(
            "Use MP3, OGG, WAV, M4A, AAC, FLAC, OPUS, MP4, MOV, or WEBM."
        )
    size = int(getattr(attachment, "size", 0) or 0)
    if not 0 < size <= MAX_SOURCE_BYTES:
        raise SoundboardInputError("Each original file must be under 8 MB.")


async def _convert_bytes(source: bytes, *, start: float) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SoundboardInputError("Audio converter unavailable on the bot host.")
    async with _CONVERSION_SLOTS:
        with tempfile.TemporaryDirectory(prefix="dank_sound_") as temporary:
            dst = Path(temporary) / "clip.mp3"
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-y", "-protocol_whitelist", "pipe",
                "-i", "pipe:0", "-ss", str(start),
                "-t", str(MAX_CLIP_SECONDS), "-vn", "-sn", "-dn",
                "-threads", "1", "-ac", "1", "-ar", "32000",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3",
                str(dst),
            ]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.communicate(input=source), timeout=20)
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.communicate()
                raise SoundboardInputError("Audio processing timed out. Try a smaller file.") from exc
            except asyncio.CancelledError:
                if proc.returncode is None:
                    proc.kill()
                    await proc.communicate()
                raise
            if proc.returncode != 0:
                raise SoundboardInputError(
                    "This file has no usable audio at the chosen trim point."
                )
            output = await asyncio.to_thread(dst.read_bytes) if dst.exists() else b""
            if not 100 < len(output) <= MAX_SOUND_BYTES:
                raise SoundboardInputError(
                    "The sound is empty or exceeds Discord's 512 KB limit."
                )
            return output


async def prepare_attachment(
    attachment: discord.Attachment, *, start_seconds: float = 0
) -> PreparedSound:
    validate_upload(attachment)
    try:
        start = float(start_seconds)
    except (ValueError, TypeError) as exc:
        raise SoundboardInputError("Trim start must be a number of seconds.") from exc
    if not 0 <= start <= MAX_START_SECONDS:
        raise SoundboardInputError("Trim start must be between 0 and 300 seconds.")
    data = await attachment.read()
    if not 0 < len(data) <= MAX_SOURCE_BYTES:
        raise SoundboardInputError("File download exceeded the 8 MB input limit.")
    encoded = await _convert_bytes(data, start=start)
    name = validate_sound_name(clean_sound_name(attachment.filename))
    return PreparedSound(filename=attachment.filename, name=name, data=encoded)
