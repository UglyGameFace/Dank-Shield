"""Single-receiver, fail-closed Watch Yo Mouf Vosk transcription adapter.

Dank Shield remains the sole Discord DAVE/Opus receiver, per-speaker identity
and voice-specific consent authority. This adapter sends already-admitted,
bounded 16 kHz mono PCM across the authenticated Discloud VLAN. It never
connects to voice or stores audio. No cloud speech provider is used.
"""
from __future__ import annotations

import asyncio
import json
import os

import aiohttp

from .community_voice_captions import (
    CaptionSegment, TranscriptResult, pcm48_stereo_to_pcm16_mono,
)
from .watch_audio_wire import (
    AudioWireRejected, MAX_REPLY_BYTES, PATH,
    sign_audio_request, verify_audio_response,
)

_URL = "http://watchyomouf:8098" + PATH
_TRUE = {"true", "1", "on", "yes"}
_MAX_SPEECH_SECONDS = 10.0


def local_voice_enabled() -> bool:
    return os.getenv("DANK_VOICE_SAFETY_BACKEND", "").strip().lower() == "watch"


class WatchVoiceTranscriber:
    """Opt-in, short-lived client. Each segment is a separate signed request."""

    def __init__(self, guild_id: int, channel_id: int) -> None:
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.model = "vosk-small-en-us-0.15"
        self._closed = False
        self._revoked: set[int] = set()
        self._generation: dict[int, int] = {}

    async def set_user_language_codes(self, user_id: int, codes: object) -> None:
        # The initial bounded model is English-only; do not claim translation.
        del user_id, codes

    def last_audio_rms_dbfs(self, user_id: int) -> None:
        del user_id
        return None

    def last_detected_language_code(self, user_id: int) -> str:
        del user_id
        return ""

    def allow_user(self, user_id: int) -> None:
        uid = int(user_id)
        self._revoked.discard(uid)
        self._generation[uid] = self._generation.get(uid, 0) + 1

    async def close_user(self, user_id: int) -> None:
        uid = int(user_id)
        self._revoked.add(uid)
        self._generation[uid] = self._generation.get(uid, 0) + 1

    async def close(self) -> None:
        self._closed = True
        self._revoked.clear()
        self._generation.clear()

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        uid = int(segment.user_id)
        token = self._generation.get(uid, 0)
        if self._closed or uid in self._revoked or not segment.pcm:
            return self._empty()
        seconds = len(segment.pcm) / (48_000 * 4)
        if not 0.1 <= seconds <= _MAX_SPEECH_SECONDS:
            return self._empty()
        secret = os.getenv("DANK_WATCH_AUDIO_SECRET", "")
        try:
            mono_pcm = await asyncio.to_thread(
                pcm48_stereo_to_pcm16_mono, segment.pcm,
            )
            headers = sign_audio_request(
                secret, mono_pcm, self.guild_id, uid, self.channel_id,
            )
        except (AudioWireRejected, TypeError, ValueError):
            return self._empty()

        # Disconnect, explicit opt-out or session destruction after conversion
        # invalidates this operation before any network audio is sent.
        if self._closed or uid in self._revoked or self._generation.get(uid, 0) != token:
            return self._empty()
        try:
            timeout = aiohttp.ClientTimeout(total=23.0, connect=1.5)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
                async with client.post(
                    _URL, data=mono_pcm,
                    headers={**headers, "Content-Type": "application/octet-stream"},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        return self._empty()
                    if response.content_length is not None and response.content_length > MAX_REPLY_BYTES:
                        return self._empty()
                    content = bytearray()
                    while len(content) <= MAX_REPLY_BYTES:
                        part = await response.content.read(MAX_REPLY_BYTES + 1 - len(content))
                        if not part:
                            break
                        content.extend(part)
                    if len(content) > MAX_REPLY_BYTES:
                        return self._empty()
                    transcript = verify_audio_response(
                        secret, headers["X-WYM-Nonce"], mono_pcm,
                        bytes(content), response.headers.get("X-WYM-Response-Signature", ""),
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError,
                AudioWireRejected, OSError, ValueError):
            return self._empty()
        finally:
            # No member speech is logged or stored. Best effort zero only for
            # mutable receive buffer; immutable bytes are freed at function exit.
            del mono_pcm
        if self._closed or uid in self._revoked or self._generation.get(uid, 0) != token:
            return self._empty()
        return TranscriptResult(
            text=transcript, confidence=None,
            provider="watch-yo-mouf-local", model=self.model, language_code="en-us",
        )

    def _empty(self) -> TranscriptResult:
        return TranscriptResult(
            text="", confidence=None, provider="watch-yo-mouf-local",
            model=self.model, language_code="en-us",
        )
