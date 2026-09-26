from __future__ import annotations

"""Speech-preserving live caption engine for Community Hub.

Incoming Discord users are already isolated by community_voice_receive before
this module sees PCM. Segmentation never deletes samples based on a noise gate
or VAD decision. A low-confidence first transcription may be retried with
amplitude normalization, which scales samples but does not remove speech.

Audio exists only in bounded in-memory buffers and WAV request bodies.
"""

import asyncio
import base64
import io
import json
import logging
import os
import struct
import time
import wave
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from .community_voice_receive import (
    PCM_CHANNELS,
    PCM_SAMPLE_RATE,
    PCM_SAMPLE_WIDTH,
    SpeakerPCMFrame,
)


log = logging.getLogger(__name__)


class CaptionTranscriptionError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        safe_message: str,
        *,
        error_code: str = "",
        error_type: str = "",
        terminal: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.status_code = int(status_code)
        self.safe_message = str(safe_message)
        self.error_code = str(error_code or "").strip()
        self.error_type = str(error_type or "").strip()
        self.terminal = bool(terminal)


def _gemini_transcription_error(status_code: int, body: str) -> CaptionTranscriptionError:
    status = int(status_code)
    provider_status = ""
    provider_message = ""
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        provider_status = str(error.get("status") or "").strip()
        provider_message = str(error.get("message") or "").strip()

    code = provider_status or str(status)
    kind = provider_status.casefold()

    if status in {400, 422} or kind == "invalid_argument":
        message = "Gemini rejected the transcription request. Check the configured caption model and audio request."
    elif status in {401, 403} or kind in {"unauthenticated", "permission_denied"}:
        message = "Gemini rejected GEMINI_API_KEY or this AI Studio project is not permitted to use the caption model."
    elif status == 404 or kind == "not_found":
        message = "The configured Gemini caption model is unavailable to this AI Studio project."
    elif status == 429 or kind == "resource_exhausted":
        message = (
            "Gemini free-tier quota or rate limit was reached (HTTP 429 RESOURCE_EXHAUSTED). "
            "Check this AI Studio project's active model limits, then restart the caption session after quota is available."
        )
    elif 500 <= status <= 599 or kind in {"internal", "unavailable"}:
        message = f"Gemini transcription is temporarily unavailable (HTTP {status})."
    else:
        message = f"Gemini transcription request failed (HTTP {status})."

    terminal = bool(
        status in {401, 403, 404, 429}
        or kind in {"unauthenticated", "permission_denied", "not_found", "resource_exhausted"}
    )
    if provider_message:
        log.warning(
            "Gemini transcription provider error status=%s provider_status=%s message=%s",
            status,
            provider_status or "-",
            provider_message[:500],
        )
    return CaptionTranscriptionError(
        status,
        message,
        error_code=code,
        error_type=provider_status,
        terminal=terminal,
    )

def _safe_segment_failure(exc: Exception) -> str:
    if isinstance(exc, CaptionTranscriptionError):
        return exc.safe_message
    return f"Caption processing failed ({type(exc).__name__}). Check host logs for details."


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    user_id: int
    pcm: bytes
    started_at: float
    ended_at: float


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    text: str
    confidence: Optional[float]
    provider: str
    model: str


@dataclass(slots=True)
class _SpeakerBuffer:
    chunks: list[bytes] = field(default_factory=list)
    started_at: float = 0.0
    last_frame_at: float = 0.0
    total_bytes: int = 0


class SpeechPreservingSegmenter:
    """Split isolated speakers on packet gaps/max duration without audio gating."""

    def __init__(
        self,
        *,
        silence_gap_seconds: float = 0.75,
        max_segment_seconds: float = 8.0,
    ) -> None:
        self.silence_gap_seconds = max(0.35, min(2.0, float(silence_gap_seconds)))
        self.max_segment_seconds = max(2.0, min(15.0, float(max_segment_seconds)))
        self._buffers: dict[int, _SpeakerBuffer] = {}

    @property
    def max_segment_bytes(self) -> int:
        return int(
            PCM_SAMPLE_RATE
            * PCM_CHANNELS
            * PCM_SAMPLE_WIDTH
            * self.max_segment_seconds
        )

    def feed(self, frame: SpeakerPCMFrame) -> list[CaptionSegment]:
        ready: list[CaptionSegment] = []
        buf = self._buffers.get(frame.user_id)

        if buf is not None and buf.chunks:
            gap = max(0.0, frame.received_at - buf.last_frame_at)
            if gap >= self.silence_gap_seconds:
                segment = self._flush_user(frame.user_id)
                if segment is not None:
                    ready.append(segment)
                buf = None

        if buf is None:
            buf = _SpeakerBuffer(
                started_at=frame.received_at,
                last_frame_at=frame.received_at,
            )
            self._buffers[frame.user_id] = buf

        buf.chunks.append(frame.pcm)
        buf.total_bytes += len(frame.pcm)
        buf.last_frame_at = frame.received_at

        if buf.total_bytes >= self.max_segment_bytes:
            segment = self._flush_user(frame.user_id)
            if segment is not None:
                ready.append(segment)

        return ready

    def flush_idle(self, now: Optional[float] = None) -> list[CaptionSegment]:
        current = time.monotonic() if now is None else float(now)
        ready: list[CaptionSegment] = []
        for user_id, buf in list(self._buffers.items()):
            if not buf.chunks:
                continue
            if current - buf.last_frame_at >= self.silence_gap_seconds:
                segment = self._flush_user(user_id)
                if segment is not None:
                    ready.append(segment)
        return ready

    def flush_all(self) -> list[CaptionSegment]:
        ready: list[CaptionSegment] = []
        for user_id in list(self._buffers):
            segment = self._flush_user(user_id)
            if segment is not None:
                ready.append(segment)
        return ready

    def discard_user(self, user_id: int) -> None:
        """Drop one speaker's buffered PCM without producing a segment."""

        self._buffers.pop(int(user_id), None)

    def _flush_user(self, user_id: int) -> Optional[CaptionSegment]:
        buf = self._buffers.pop(int(user_id), None)
        if buf is None or not buf.chunks:
            return None
        return CaptionSegment(
            user_id=int(user_id),
            pcm=b"".join(buf.chunks),
            started_at=buf.started_at,
            ended_at=buf.last_frame_at,
        )


def pcm16_to_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(PCM_CHANNELS)
        wav.setsampwidth(PCM_SAMPLE_WIDTH)
        wav.setframerate(PCM_SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()


def normalize_pcm16_lossless_timing(pcm: bytes, *, target_peak: int = 26_000) -> bytes:
    """Amplitude-normalize PCM while preserving every sample and its timing."""

    if not pcm or len(pcm) % 2:
        return pcm

    count = len(pcm) // 2
    samples = struct.unpack("<" + ("h" * count), pcm)
    peak = max((abs(value) for value in samples), default=0)
    if peak <= 0 or peak >= target_peak:
        return pcm

    gain = min(4.0, float(target_peak) / float(peak))
    if gain <= 1.05:
        return pcm

    normalized = [
        max(-32768, min(32767, int(round(value * gain))))
        for value in samples
    ]
    return struct.pack("<" + ("h" * count), *normalized)



def _gemini_text_from_generate_content(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return ""
    chunks: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or bool(part.get("thought")):
                continue
            text = str(part.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


class GeminiTranscriber:
    API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.5-transcribe",
        fallback_model: str = "gemini-3.5-flash-lite",
        language: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "gemini-3.5-transcribe").strip()
        self.fallback_model = str(fallback_model or "").strip()
        self.language = str(language or "").strip()
        self.timeout_seconds = max(5.0, min(60.0, float(timeout_seconds)))
        self.fallback_count = 0
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Live Captions.")

    def _request_body(self, wav_data: bytes, *, model: str) -> dict[str, Any]:
        encoded = base64.b64encode(wav_data).decode("ascii")
        audio_part = {
            "inlineData": {
                "mimeType": "audio/wav",
                "data": encoded,
            }
        }
        if model == self.model:
            transcription_config: dict[str, Any] = {"mode": "VERBATIM"}
            if self.language:
                transcription_config["languageCodes"] = [self.language]
            return {
                "contents": [{"role": "user", "parts": [audio_part]}],
                "generationConfig": {
                    "audioTranscriptionConfig": transcription_config,
                },
            }

        prompt = (
            "Transcribe only the spoken words in this audio. "
            "Return only the transcript text, with natural punctuation. "
            "Do not describe sounds, identify the speaker, add labels, summarize, or answer the speech."
        )
        if self.language:
            prompt += f" The expected spoken language is {self.language}."
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        audio_part,
                    ],
                }
            ],
            "generationConfig": {
                "thinkingConfig": {"thinkingLevel": "minimal"},
                "responseMimeType": "text/plain",
                "maxOutputTokens": 1024,
            },
        }

    async def _generate(self, wav_data: bytes, *, model: str) -> str:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        url = f"{self.API_ROOT}/{model}:generateContent"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=self._request_body(wav_data, model=model),
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    raise _gemini_transcription_error(int(response.status), body)
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Gemini caption transcription returned invalid JSON") from exc
        return _gemini_text_from_generate_content(payload)

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        wav_data = pcm16_to_wav(segment.pcm)
        text = await self._generate(wav_data, model=self.model)
        used_model = self.model

        if not text and self.fallback_model and self.fallback_model != self.model:
            self.fallback_count += 1
            text = await self._generate(wav_data, model=self.fallback_model)
            used_model = self.fallback_model

        return TranscriptResult(
            text=text,
            confidence=None,
            provider="gemini",
            model=used_model,
        )


class CaptionEngine:
    def __init__(
        self,
        transcriber: Any,
        publish: Callable[[int, str, Optional[float]], Awaitable[None]],
        *,
        queue_size: int = 400,
        low_confidence_threshold: float = 0.72,
        unclear_threshold: float = 0.48,
        global_transcribe_semaphore: Optional[asyncio.Semaphore] = None,
    ) -> None:
        self.transcriber = transcriber
        self.publish = publish
        self.queue: asyncio.Queue[SpeakerPCMFrame] = asyncio.Queue(
            maxsize=max(50, min(2000, int(queue_size)))
        )
        self.segmenter = SpeechPreservingSegmenter()
        self.low_confidence_threshold = max(0.0, min(1.0, float(low_confidence_threshold)))
        self.unclear_threshold = max(0.0, min(1.0, float(unclear_threshold)))
        self._task: Optional[asyncio.Task[None]] = None
        self._segment_tasks: set[asyncio.Task[None]] = set()
        self._segment_tasks_by_user: dict[int, set[asyncio.Task[None]]] = {}
        self._blocked_user_ids: set[int] = set()
        self._closed = False
        self._transcribe_semaphore = asyncio.Semaphore(3)
        self._global_transcribe_semaphore = global_transcribe_semaphore
        self.segments_transcribed = 0
        self.segments_published = 0
        self.segments_empty = 0
        self.segments_unclear = 0
        self.segment_failures = 0
        self.queue_overflow = 0
        self.provider_skipped = 0
        self.last_failure = ""
        self.provider_blocked_reason = ""
        self.provider_blocked_code = ""

    def submit(self, frame: SpeakerPCMFrame) -> None:
        if self._closed or int(frame.user_id) in self._blocked_user_ids:
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.queue_overflow += 1
            raise

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(),
                name="dank-live-captions",
            )

    def allow_user(self, user_id: int) -> None:
        self._blocked_user_ids.discard(int(user_id))

    async def revoke_user(self, user_id: int) -> None:
        """Revoke one speaker and guarantee their buffered audio cannot publish later."""

        uid = int(user_id)
        self._blocked_user_ids.add(uid)
        self.segmenter.discard_user(uid)

        retained: list[SpeakerPCMFrame] = []
        while True:
            try:
                frame = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if int(frame.user_id) != uid:
                retained.append(frame)
        for frame in retained:
            self.queue.put_nowait(frame)

        pending = list(self._segment_tasks_by_user.get(uid, set()))
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._segment_tasks_by_user.pop(uid, None)

    async def close(self) -> None:
        """Stop immediately without allowing buffered speech to publish later."""

        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        pending = list(self._segment_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._segment_tasks.clear()
        self._segment_tasks_by_user.clear()
        self._blocked_user_ids.clear()

        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.segmenter.flush_all()

    def _spawn_segment(self, segment: CaptionSegment) -> None:
        uid = int(segment.user_id)
        if self._closed or not segment.pcm or uid in self._blocked_user_ids:
            return
        task = asyncio.create_task(
            self._process_segment_safely(segment),
            name=f"dank-caption-segment:{uid}",
        )
        self._segment_tasks.add(task)
        self._segment_tasks_by_user.setdefault(uid, set()).add(task)

        def _done(completed: asyncio.Task[None]) -> None:
            self._segment_tasks.discard(completed)
            bucket = self._segment_tasks_by_user.get(uid)
            if bucket is None:
                return
            bucket.discard(completed)
            if not bucket:
                self._segment_tasks_by_user.pop(uid, None)

        task.add_done_callback(_done)

    async def _process_segment_safely(self, segment: CaptionSegment) -> None:
        try:
            await self._process_segment(segment)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.segment_failures += 1
            self.last_failure = _safe_segment_failure(exc)[:300]
            if isinstance(exc, CaptionTranscriptionError) and exc.terminal:
                self.provider_blocked_reason = self.last_failure
                self.provider_blocked_code = exc.error_code or exc.error_type or str(exc.status_code)
            log.exception(
                "Live Captions segment failure user=%s failure=%s",
                int(segment.user_id),
                self.last_failure,
            )

    async def _run(self) -> None:
        while not self._closed:
            try:
                frame = await asyncio.wait_for(self.queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                for segment in self.segmenter.flush_idle():
                    self._spawn_segment(segment)
                continue

            for segment in self.segmenter.feed(frame):
                self._spawn_segment(segment)

    async def _process_segment(self, segment: CaptionSegment) -> None:
        if (
            not segment.pcm
            or self._closed
            or int(segment.user_id) in self._blocked_user_ids
        ):
            return
        if self.provider_blocked_reason:
            self.provider_skipped += 1
            return
        async with self._transcribe_semaphore:
            if self._global_transcribe_semaphore is None:
                await self._transcribe_and_publish(segment)
            else:
                async with self._global_transcribe_semaphore:
                    await self._transcribe_and_publish(segment)

    async def _transcribe_and_publish(self, segment: CaptionSegment) -> None:
        uid = int(segment.user_id)
        if self._closed or uid in self._blocked_user_ids:
            return
        first = await self.transcriber.transcribe(segment)
        if self._closed or uid in self._blocked_user_ids:
            return
        chosen = first

        if first.confidence is not None and first.confidence < self.low_confidence_threshold:
            normalized_pcm = normalize_pcm16_lossless_timing(segment.pcm)
            if normalized_pcm != segment.pcm:
                second = await self.transcriber.transcribe(
                    CaptionSegment(
                        user_id=segment.user_id,
                        pcm=normalized_pcm,
                        started_at=segment.started_at,
                        ended_at=segment.ended_at,
                    )
                )
                if self._closed or uid in self._blocked_user_ids:
                    return
                agreement = SequenceMatcher(
                    None,
                    first.text.casefold(),
                    second.text.casefold(),
                ).ratio()
                if (
                    agreement < 0.55
                    and second.confidence is not None
                    and max(first.confidence, second.confidence) < self.low_confidence_threshold
                ):
                    chosen = TranscriptResult(
                        text="[unclear audio]",
                        confidence=max(first.confidence, second.confidence),
                        provider=first.provider,
                        model=first.model,
                    )
                elif second.confidence is not None and second.confidence > first.confidence:
                    chosen = second

        self.segments_transcribed += 1
        if self._closed or uid in self._blocked_user_ids:
            return
        if not chosen.text:
            self.segments_empty += 1
            return
        if chosen.confidence is not None and chosen.confidence < self.unclear_threshold:
            self.segments_unclear += 1
            await self.publish(segment.user_id, "[unclear audio]", chosen.confidence)
            self.segments_published += 1
            return
        if chosen.text == "[unclear audio]":
            self.segments_unclear += 1
        if self._closed or uid in self._blocked_user_ids:
            return
        await self.publish(segment.user_id, chosen.text[:1800], chosen.confidence)
        self.segments_published += 1


def gemini_transcriber_from_env() -> GeminiTranscriber:
    return GeminiTranscriber(
        os.getenv("GEMINI_API_KEY", ""),
        model=os.getenv("DANK_COMMUNITY_CAPTION_MODEL", "gemini-3.5-transcribe"),
        fallback_model=os.getenv(
            "DANK_COMMUNITY_CAPTION_FALLBACK_MODEL",
            "gemini-3.5-flash-lite",
        ),
        language=os.getenv("DANK_COMMUNITY_CAPTION_LANGUAGE", ""),
    )


__all__ = [
    "CaptionEngine",
    "CaptionSegment",
    "CaptionTranscriptionError",
    "GeminiTranscriber",
    "SpeechPreservingSegmenter",
    "TranscriptResult",
    "_gemini_text_from_generate_content",
    "_gemini_transcription_error",
    "gemini_transcriber_from_env",
    "normalize_pcm16_lossless_timing",
    "pcm16_to_wav",
]
