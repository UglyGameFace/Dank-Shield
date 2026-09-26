from __future__ import annotations

"""Speech-preserving live caption engine for Community Hub.

Incoming Discord users are already isolated by community_voice_receive before
this module sees PCM. Segmentation never deletes samples based on a noise gate
or VAD decision. A low-confidence first transcription may be retried with
amplitude normalization, which scales samples but does not remove speech.

Audio exists only in bounded in-memory buffers and WAV request bodies.
"""

import asyncio
import io
import json
import math
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


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    user_id: int
    pcm: bytes
    started_at: float
    ended_at: float


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    text: str
    confidence: float
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


def _confidence_from_logprobs(payload: dict[str, Any]) -> float:
    rows = payload.get("logprobs")
    if not isinstance(rows, list) or not rows:
        return 0.0
    values: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            values.append(float(row.get("logprob")))
        except (TypeError, ValueError):
            continue
    if not values:
        return 0.0
    mean_logprob = sum(values) / len(values)
    return max(0.0, min(1.0, math.exp(mean_logprob)))


class OpenAITranscriber:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-transcribe",
        language: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "gpt-4o-transcribe").strip()
        self.language = str(language or "").strip()
        self.timeout_seconds = max(5.0, min(60.0, float(timeout_seconds)))
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for Community Hub captions.")

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        form = aiohttp.FormData()
        form.add_field(
            "file",
            pcm16_to_wav(segment.pcm),
            filename=f"speaker-{segment.user_id}.wav",
            content_type="audio/wav",
        )
        form.add_field("model", self.model)
        form.add_field("response_format", "json")
        form.add_field("temperature", "0")
        form.add_field("include[]", "logprobs")
        if self.language:
            form.add_field("language", self.language)

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=form,
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    raise RuntimeError(
                        f"caption transcription failed HTTP {response.status}: {body[:240]}"
                    )
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("caption transcription returned invalid JSON") from exc

        text = str(payload.get("text") or "").strip()
        return TranscriptResult(
            text=text,
            confidence=_confidence_from_logprobs(payload),
            provider="openai",
            model=self.model,
        )


class CaptionEngine:
    def __init__(
        self,
        transcriber: Any,
        publish: Callable[[int, str, float], Awaitable[None]],
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
        self._closed = False
        self._transcribe_semaphore = asyncio.Semaphore(3)
        self._global_transcribe_semaphore = global_transcribe_semaphore
        self.segments_transcribed = 0
        self.segments_unclear = 0
        self.segment_failures = 0
        self.queue_overflow = 0

    def submit(self, frame: SpeakerPCMFrame) -> None:
        if self._closed:
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
                name="dank-community-live-captions",
            )

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

        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.segmenter.flush_all()

    def _spawn_segment(self, segment: CaptionSegment) -> None:
        if self._closed or not segment.pcm:
            return
        task = asyncio.create_task(
            self._process_segment_safely(segment),
            name=f"dank-community-caption-segment:{segment.user_id}",
        )
        self._segment_tasks.add(task)
        task.add_done_callback(self._segment_tasks.discard)

    async def _process_segment_safely(self, segment: CaptionSegment) -> None:
        try:
            await self._process_segment(segment)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.segment_failures += 1

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
        if not segment.pcm or self._closed:
            return
        async with self._transcribe_semaphore:
            if self._global_transcribe_semaphore is None:
                await self._transcribe_and_publish(segment)
            else:
                async with self._global_transcribe_semaphore:
                    await self._transcribe_and_publish(segment)

    async def _transcribe_and_publish(self, segment: CaptionSegment) -> None:
        if self._closed:
            return
        first = await self.transcriber.transcribe(segment)
        chosen = first

        if first.confidence < self.low_confidence_threshold:
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
                agreement = SequenceMatcher(
                    None,
                    first.text.casefold(),
                    second.text.casefold(),
                ).ratio()
                if (
                    agreement < 0.55
                    and max(first.confidence, second.confidence) < self.low_confidence_threshold
                ):
                    chosen = TranscriptResult(
                        text="[unclear audio]",
                        confidence=max(first.confidence, second.confidence),
                        provider=first.provider,
                        model=first.model,
                    )
                elif second.confidence > first.confidence:
                    chosen = second

        self.segments_transcribed += 1
        if self._closed or not chosen.text:
            return
        if chosen.confidence < self.unclear_threshold:
            self.segments_unclear += 1
            await self.publish(segment.user_id, "[unclear audio]", chosen.confidence)
            return
        if chosen.text == "[unclear audio]":
            self.segments_unclear += 1
        if self._closed:
            return
        await self.publish(segment.user_id, chosen.text[:1800], chosen.confidence)


def openai_transcriber_from_env() -> OpenAITranscriber:
    return OpenAITranscriber(
        os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("DANK_COMMUNITY_CAPTION_MODEL", "gpt-4o-transcribe"),
        language=os.getenv("DANK_COMMUNITY_CAPTION_LANGUAGE", ""),
    )


__all__ = [
    "CaptionEngine",
    "CaptionSegment",
    "OpenAITranscriber",
    "SpeechPreservingSegmenter",
    "TranscriptResult",
    "normalize_pcm16_lossless_timing",
    "openai_transcriber_from_env",
    "pcm16_to_wav",
]
