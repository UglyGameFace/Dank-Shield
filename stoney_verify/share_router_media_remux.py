from __future__ import annotations

"""Bounded Share Router media remux/merge support.

This module turns resolver-classified provider media into a temporary MP4 when
stream-copy is possible. It never invokes a shell and deliberately performs no
general video/audio re-encoding.
"""

import asyncio
from dataclasses import replace
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from stoney_verify.share_router_manifest_proxy import (
    ManifestProxy,
    ManifestProxyError,
)
from stoney_verify.share_router_media_network import url_resolves_public
from stoney_verify.share_router_media_resolver import MediaResolution


_DEFAULT_REMUX_CONCURRENCY = 1
_DEFAULT_REMUX_TIMEOUT_SECONDS = 30.0
_REMUX_LOOP: asyncio.AbstractEventLoop | None = None
_REMUX_SEMAPHORE: asyncio.Semaphore | None = None
_REMUX_STATS: dict[str, int] = {
    "attempts": 0,
    "success": 0,
    "timeout": 0,
    "failed": 0,
    "oversize": 0,
    "unavailable": 0,
}


@dataclass(frozen=True)
class RemuxedMedia:
    path: Path
    size_bytes: int
    filename: str = "share-router-video.mp4"

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass


def _safe_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _remux_concurrency() -> int:
    return _safe_int_env(
        "DANK_SHARE_ROUTER_MEDIA_REMUX_CONCURRENCY",
        _DEFAULT_REMUX_CONCURRENCY,
        1,
        2,
    )


def _remux_timeout_seconds() -> float:
    try:
        raw = float(
            str(
                os.getenv(
                    "DANK_SHARE_ROUTER_MEDIA_REMUX_TIMEOUT_SECONDS",
                    str(_DEFAULT_REMUX_TIMEOUT_SECONDS),
                )
                or _DEFAULT_REMUX_TIMEOUT_SECONDS
            ).strip()
        )
    except Exception:
        raw = _DEFAULT_REMUX_TIMEOUT_SECONDS
    return max(8.0, min(raw, 60.0))


def _ensure_async_state() -> asyncio.Semaphore:
    global _REMUX_LOOP
    global _REMUX_SEMAPHORE

    loop = asyncio.get_running_loop()
    if _REMUX_LOOP is not loop:
        _REMUX_LOOP = loop
        _REMUX_SEMAPHORE = asyncio.Semaphore(_remux_concurrency())
    assert _REMUX_SEMAPHORE is not None
    return _REMUX_SEMAPHORE


def _safe_header_blob(headers: Mapping[str, str] | tuple[tuple[str, str], ...]) -> str:
    lines: list[str] = []
    for key, value in dict(headers or {}).items():
        name = str(key or "").strip()
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        if not name or not text:
            continue
        if name.lower() not in {
            "accept",
            "accept-language",
            "origin",
            "referer",
            "user-agent",
        }:
            continue
        lines.append(f"{name}: {text[:1000]}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def _append_input(
    command: list[str],
    *,
    url: str,
    headers: Mapping[str, str] | tuple[tuple[str, str], ...],
    protocol_whitelist: str = "http,https,tcp,tls,crypto",
) -> None:
    command.extend(
        [
            "-protocol_whitelist",
            protocol_whitelist,
            "-rw_timeout",
            "12000000",
        ]
    )
    header_blob = _safe_header_blob(headers)
    if header_blob:
        command.extend(["-headers", header_blob])
    command.extend(["-i", url])


def build_ffmpeg_remux_command(
    ffmpeg: str,
    resolution: MediaResolution,
    *,
    output_path: Path,
    max_bytes: int,
    manifest_via_proxy: bool = False,
) -> list[str]:
    if resolution.delivery not in {"manifest", "merge"}:
        raise ValueError("resolution is not remuxable")

    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
    ]
    _append_input(
        command,
        url=resolution.media_url,
        headers=resolution.request_headers,
        protocol_whitelist=(
            "http,tcp,crypto,data"
            if resolution.delivery == "manifest" and manifest_via_proxy
            else "http,https,tcp,tls,crypto"
        ),
    )

    if resolution.delivery == "merge":
        if not resolution.audio_url:
            raise ValueError("merge resolution is missing audio_url")
        _append_input(
            command,
            url=resolution.audio_url,
            headers=resolution.audio_request_headers,
        )
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-shortest",
            ]
        )
    else:
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
            ]
        )

    # Stream copy is deliberate. Re-encoding is too expensive for the current
    # production memory budget and belongs in a separately measured slice.
    command.extend(
        [
            "-sn",
            "-dn",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            "-fs",
            str(max(1, int(max_bytes)) + 1),
            str(output_path),
        ]
    )
    return command


async def _inputs_are_public(resolution: MediaResolution) -> bool:
    urls = [resolution.media_url]
    if resolution.delivery == "merge":
        urls.append(resolution.audio_url)
    for value in urls:
        if not value or not await url_resolves_public(value):
            return False
    return True


async def _run_ffmpeg(
    command: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int, str, bool]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        _stdout, stderr = await process.communicate()
        return int(process.returncode or -9), (
            stderr.decode("utf-8", "replace")[:400] if stderr else ""
        ), True
    except asyncio.CancelledError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.communicate(), timeout=5.0)
        except Exception:
            pass
        raise

    return int(process.returncode or 0), (
        stderr.decode("utf-8", "replace")[:400] if stderr else ""
    ), False


async def remux_media_for_discord(
    resolution: MediaResolution,
    *,
    max_bytes: int,
) -> Optional[RemuxedMedia]:
    if resolution.delivery not in {"manifest", "merge"}:
        return None

    if not await _inputs_are_public(resolution):
        _REMUX_STATS["unavailable"] += 1
        return None

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _REMUX_STATS["unavailable"] += 1
        return None

    limit = max(1, int(max_bytes or 0))
    semaphore = _ensure_async_state()

    async with semaphore:
        _REMUX_STATS["attempts"] += 1
        fd, raw_path = tempfile.mkstemp(
            prefix="dank-share-remux-",
            suffix=".mp4",
        )
        os.close(fd)
        output_path = Path(raw_path)
        keep_output = False
        proxy: Optional[ManifestProxy] = None
        active_resolution = resolution
        try:
            output_path.unlink(missing_ok=True)

            if resolution.delivery == "manifest":
                try:
                    proxy = ManifestProxy(
                        resolution.media_url,
                        headers=resolution.request_headers,
                        max_output_bytes=limit,
                    )
                    await proxy.start()
                    active_resolution = replace(
                        resolution,
                        media_url=proxy.entry_url,
                        request_headers=(),
                    )
                except (ManifestProxyError, OSError, aiohttp.ClientError):
                    _REMUX_STATS["unavailable"] += 1
                    return None

            command = build_ffmpeg_remux_command(
                ffmpeg,
                active_resolution,
                output_path=output_path,
                max_bytes=limit,
                manifest_via_proxy=proxy is not None,
            )
            try:
                returncode, _detail, timed_out = await _run_ffmpeg(
                    command,
                    timeout_seconds=_remux_timeout_seconds(),
                )
            except (OSError, ValueError):
                _REMUX_STATS["failed"] += 1
                return None

            if timed_out:
                _REMUX_STATS["timeout"] += 1
                return None
            if returncode != 0:
                _REMUX_STATS["failed"] += 1
                return None

            try:
                size = int(output_path.stat().st_size)
            except OSError:
                _REMUX_STATS["failed"] += 1
                return None

            if size <= 0:
                _REMUX_STATS["failed"] += 1
                return None
            if size > limit:
                _REMUX_STATS["oversize"] += 1
                return None

            _REMUX_STATS["success"] += 1
            keep_output = True
            return RemuxedMedia(
                path=output_path,
                size_bytes=size,
            )
        finally:
            if proxy is not None:
                try:
                    await proxy.close()
                except Exception:
                    pass
            # Ownership of a successful file transfers to RemuxedMedia. Every
            # failed/timeout/oversize attempt is removed regardless of history.
            if not keep_output:
                try:
                    output_path.unlink(missing_ok=True)
                except Exception:
                    pass


def media_remux_snapshot() -> dict[str, int | float]:
    return {
        **{key: max(0, int(value)) for key, value in _REMUX_STATS.items()},
        "concurrency": _remux_concurrency(),
        "timeout_seconds": _remux_timeout_seconds(),
    }


def reset_media_remux_state_for_tests() -> None:
    global _REMUX_LOOP
    global _REMUX_SEMAPHORE

    _REMUX_LOOP = None
    _REMUX_SEMAPHORE = None
    for key in list(_REMUX_STATS):
        _REMUX_STATS[key] = 0


__all__ = [
    "RemuxedMedia",
    "build_ffmpeg_remux_command",
    "media_remux_snapshot",
    "remux_media_for_discord",
    "reset_media_remux_state_for_tests",
]
