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
from collections import deque
import io
import json
import logging
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
    language_code: str = ""


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


def _design_downsample_fir(
    *,
    taps: int = 31,
    cutoff_hz: float = 7000.0,
    sample_rate: float = 48000.0,
) -> tuple[float, ...]:
    """Design a small Hamming-windowed sinc low-pass for 48kHz -> 16kHz.

    The old three-frame box average only weakly suppressed energy above the new
    8kHz Nyquist frequency, so high-frequency speech/noise could alias back into
    the recognition band. This deterministic FIR keeps ordinary speech intact
    while strongly reducing fold-back before decimation by three.
    """

    count = int(taps)
    if count < 7 or count % 2 == 0:
        raise ValueError("downsample FIR tap count must be odd and >= 7")
    fc = float(cutoff_hz) / float(sample_rate)
    midpoint = (count - 1) / 2.0
    coeffs: list[float] = []
    for index in range(count):
        distance = float(index) - midpoint
        if abs(distance) < 1e-12:
            ideal = 2.0 * fc
        else:
            ideal = math.sin(2.0 * math.pi * fc * distance) / (
                math.pi * distance
            )
        window = 0.54 - 0.46 * math.cos(
            2.0 * math.pi * float(index) / float(count - 1)
        )
        coeffs.append(ideal * window)
    total = sum(coeffs)
    if abs(total) < 1e-12:
        raise RuntimeError("downsample FIR normalization failed")
    return tuple(value / total for value in coeffs)


_PCM48_TO_16_FIR = _design_downsample_fir()


class _StreamingPCM48To16Mono:
    """Stateful 48 kHz stereo -> 16 kHz mono FIR decimator.

    Live Transcribe expects audio as it is produced, not a several-second burst.
    Keeping FIR history per Discord speaker avoids introducing a filter edge at
    every 20 ms Discord PCM frame.
    """

    def __init__(self) -> None:
        self._coeffs = _PCM48_TO_16_FIR
        self._history = deque(
            [0.0] * len(self._coeffs),
            maxlen=len(self._coeffs),
        )
        self._phase = 0

    def reset(self) -> None:
        self._history.clear()
        self._history.extend([0.0] * len(self._coeffs))
        self._phase = 0

    def feed(self, pcm: bytes) -> bytes:
        usable = len(pcm) - (len(pcm) % 4)
        if usable <= 0:
            return b""

        samples = struct.unpack("<" + ("h" * (usable // 2)), pcm[:usable])
        out: list[int] = []
        for offset in range(0, len(samples), 2):
            mono = (int(samples[offset]) + int(samples[offset + 1])) / 2.0
            self._history.append(mono)
            if self._phase == 0:
                # FIR coefficients are symmetric, so oldest->newest history can
                # be multiplied directly by the symmetric kernel.
                acc = sum(
                    coefficient * sample
                    for coefficient, sample in zip(self._coeffs, self._history)
                )
                out.append(max(-32768, min(32767, int(round(acc)))))
            self._phase = (self._phase + 1) % 3

        return struct.pack("<" + ("h" * len(out)), *out) if out else b""


def pcm48_stereo_to_pcm16_mono(pcm: bytes) -> bytes:
    """Convert Discord 48kHz stereo s16le PCM to Gemini's 16kHz mono s16le.

    Discord/discord-ext-voice-recv yields decoded 48kHz stereo signed 16-bit
    PCM. Google Live Transcribe expects raw 16kHz mono signed 16-bit PCM. Downmix
    first, low-pass below the 8kHz target Nyquist, then decimate by three.
    """

    usable = len(pcm) - (len(pcm) % 4)
    if usable <= 0:
        return b""

    samples = struct.unpack("<" + ("h" * (usable // 2)), pcm[:usable])
    mono = [
        int(round((int(samples[offset]) + int(samples[offset + 1])) / 2.0))
        for offset in range(0, len(samples), 2)
    ]
    if not mono:
        return b""

    coeffs = _PCM48_TO_16_FIR
    half = len(coeffs) // 2
    out_count = (len(mono) + 2) // 3
    out: list[int] = []
    last = len(mono) - 1

    for out_index in range(out_count):
        center = out_index * 3
        acc = 0.0
        for tap_index, coefficient in enumerate(coeffs):
            source_index = center + tap_index - half
            if source_index < 0:
                sample = mono[0]
            elif source_index > last:
                sample = mono[last]
            else:
                sample = mono[source_index]
            acc += coefficient * float(sample)
        out.append(max(-32768, min(32767, int(round(acc)))))

    return struct.pack("<" + ("h" * len(out)), *out) if out else b""


def pcm16_rms_dbfs(pcm: bytes) -> float:
    """Return RMS level for signed-16 PCM without retaining audio."""

    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return -120.0
    samples = struct.unpack("<" + ("h" * (usable // 2)), pcm[:usable])
    if not samples:
        return -120.0
    mean_square = sum(float(value) * float(value) for value in samples) / float(len(samples))
    if mean_square <= 0.0:
        return -120.0
    rms = math.sqrt(mean_square)
    return max(-120.0, min(0.0, 20.0 * math.log10(rms / 32768.0)))


def _language_codes(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        raw = [str(item).strip() for item in value]
    else:
        raw = [
            part.strip()
            for part in str(value or "").replace(";", ",").split(",")
        ]
    seen: set[str] = set()
    out: list[str] = []
    for code in raw:
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code[:35])
        if len(out) >= 8:
            break
    return out


def normalize_caption_output_mode(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("+", " ").replace("_", " ")
    mode = "_".join(raw.split())
    if mode in {"english", "english_only"}:
        return "english"
    if mode in {"bilingual", "original_english", "original_and_english", "both"}:
        return "bilingual"
    return "original"


class _GeminiLiveSpeakerSession:
    WS_URL = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )

    def __init__(
        self,
        owner: "GeminiLiveTranscriber",
        user_id: int,
    ) -> None:
        self.owner = owner
        self.user_id = int(user_id)
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.connected_at = 0.0
        self.rotate_before_next = False
        self.utterance_active = False
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._receiver_task: Optional[asyncio.Task[None]] = None
        self._final_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=16)
        self._pending_finals: deque[asyncio.Future[Any]] = deque()
        self._current_interims: deque[TranscriptResult] = deque(maxlen=4)
        self._sealed_interim_fallbacks: dict[
            asyncio.Future[Any],
            TranscriptResult,
        ] = {}
        self._resampler = _StreamingPCM48To16Mono()

    def _clear_final_queue(self) -> None:
        while True:
            try:
                self._final_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _cancel_pending_finals(self) -> None:
        while self._pending_finals:
            waiter = self._pending_finals.popleft()
            self._sealed_interim_fallbacks.pop(waiter, None)
            if not waiter.done():
                waiter.cancel()
        self._sealed_interim_fallbacks.clear()

    @staticmethod
    def _normalized_interim_text(text: str) -> str:
        return " ".join(str(text or "").casefold().split())

    def _record_interim(self, interim: dict[str, Any]) -> None:
        text = str(interim.get("text") or "").strip()
        if not text:
            return
        self._current_interims.append(
            TranscriptResult(
                text=text,
                confidence=None,
                provider="gemini-live-interim",
                model=self.owner.model,
                language_code=str(interim.get("languageCode") or "").strip(),
            )
        )

    def _stable_interim_candidate(self) -> Optional[TranscriptResult]:
        if len(self._current_interims) < 2:
            return None

        previous = self._current_interims[-2]
        latest = self._current_interims[-1]
        a = self._normalized_interim_text(previous.text)
        b = self._normalized_interim_text(latest.text)
        if not a or not b:
            return None

        previous_family = str(previous.language_code or "").split("-", 1)[0].casefold()
        latest_family = str(latest.language_code or "").split("-", 1)[0].casefold()
        if previous_family and latest_family and previous_family != latest_family:
            return None

        if len(b) < 8:
            stable = a == b
        else:
            similarity = SequenceMatcher(None, a, b).ratio()
            shorter, longer = sorted((a, b), key=len)
            prefix_stable = bool(
                longer.startswith(shorter)
                and len(shorter) >= 8
                and len(shorter) / max(1, len(longer)) >= 0.80
            )
            stable = similarity >= 0.90 or prefix_stable
        if not stable:
            return None

        return TranscriptResult(
            text=latest.text,
            confidence=None,
            provider="gemini-live-interim-timeout",
            model=self.owner.model,
            language_code=latest.language_code or previous.language_code,
        )

    def _resolve_pending_final(self, value: Any) -> bool:
        while self._pending_finals:
            waiter = self._pending_finals.popleft()
            self._sealed_interim_fallbacks.pop(waiter, None)
            if waiter.done():
                continue
            if isinstance(value, Exception):
                waiter.set_exception(value)
            else:
                waiter.set_result(value)
            return True
        return False

    def _fail_pending_finals(self, exc: Exception) -> bool:
        delivered = False
        while self._pending_finals:
            waiter = self._pending_finals.popleft()
            self._sealed_interim_fallbacks.pop(waiter, None)
            if waiter.done():
                continue
            waiter.set_exception(exc)
            delivered = True
        return delivered

    async def close(self) -> None:
        receiver = self._receiver_task
        self._receiver_task = None
        if receiver is not None and not receiver.done():
            receiver.cancel()
            try:
                await receiver
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        ws = self.ws
        self.ws = None
        self.connected_at = 0.0
        self.rotate_before_next = False
        self.utterance_active = False
        self._resampler.reset()
        self._cancel_pending_finals()
        self._current_interims.clear()
        self._clear_final_queue()
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass

    async def _push_final_event(self, value: Any) -> None:
        # Production streaming seals each local utterance before the next turn
        # starts, so finalized events can be mapped FIFO to an exact waiter.
        # The bounded queue remains as a compatibility fallback for unexpected
        # provider events that arrive without an active sealed turn.
        if self._resolve_pending_final(value):
            return
        if self._final_queue.full():
            try:
                self._final_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._final_queue.put_nowait(value)
        except asyncio.QueueFull:
            pass

    async def _receive_loop(
        self,
        ws: aiohttp.ClientWebSocketResponse,
    ) -> None:
        try:
            while self.ws is ws and not ws.closed:
                msg = await ws.receive()
                payload = self.owner._ws_payload(msg)
                self.owner._raise_ws_error(payload)

                if "goAway" in payload:
                    # Finish the current utterance on this connection, then
                    # rotate before the next speaker turn.
                    self.rotate_before_next = True

                content = payload.get("serverContent")
                if not isinstance(content, dict):
                    continue

                interim = content.get("interimInputTranscription")
                if isinstance(interim, dict):
                    self.owner.interim_transcript_events += 1
                    self._record_interim(interim)

                final = content.get("inputTranscription")
                if not isinstance(final, dict):
                    continue

                text = str(final.get("text") or "").strip()
                language_code = str(final.get("languageCode") or "").strip()
                self.owner.final_transcript_events += 1
                await self._push_final_event(
                    TranscriptResult(
                        text=text,
                        confidence=None,
                        provider="gemini-live",
                        model=self.owner.model,
                        language_code=language_code,
                    )
                )
        except asyncio.CancelledError:
            raise
        except CaptionTranscriptionError as exc:
            if self.ws is ws and not self._fail_pending_finals(exc):
                await self._push_final_event(exc)
        except Exception:
            if self.ws is ws:
                exc = CaptionTranscriptionError(
                    0,
                    "Gemini Live transcription receive loop failed.",
                )
                if not self._fail_pending_finals(exc):
                    await self._push_final_event(exc)

    async def _connect(self) -> None:
        async with self._connect_lock:
            current = self.ws
            pending_finals = bool(self._pending_finals)
            receiver_healthy = bool(
                self._receiver_task is not None
                and not self._receiver_task.done()
            )
            expired = bool(
                self.connected_at
                and time.monotonic() - self.connected_at
                >= self.owner.session_refresh_seconds
                and not self.utterance_active
                and not pending_finals
            )
            if (
                current is not None
                and not current.closed
                and receiver_healthy
                and not expired
                and not (
                    self.rotate_before_next
                    and not self.utterance_active
                    and not pending_finals
                )
            ):
                return

            await self.close()
            http = await self.owner._http()
            url = f"{self.WS_URL}?key={self.owner.api_key}"
            try:
                ws = await http.ws_connect(
                    url,
                    heartbeat=20.0,
                    autoping=True,
                    receive_timeout=None,
                    max_msg_size=2 * 1024 * 1024,
                )
            except Exception:
                raise CaptionTranscriptionError(
                    0,
                    "Gemini Live transcription could not open its WebSocket session.",
                ) from None

            language_codes = self.owner.language_codes_for_user(self.user_id)
            transcription_config: dict[str, Any] = {
                "languageCodes": list(language_codes),
                "mode": self.owner.mode,
            }
            if self.owner.custom_vocabulary:
                transcription_config["customVocabulary"] = list(
                    self.owner.custom_vocabulary
                )

            setup = {
                "setup": {
                    "model": f"models/{self.owner.model}",
                    "generationConfig": {"responseModalities": ["TEXT"]},
                    # Discord has already isolated this speaker and the local
                    # segmenter owns the end-of-utterance boundary. Keep Gemini
                    # in documented manual-VAD mode while still streaming every
                    # PCM frame as it arrives.
                    "realtimeInputConfig": {
                        "automaticActivityDetection": {"disabled": True}
                    },
                    "inputAudioTranscription": transcription_config,
                }
            }
            try:
                await ws.send_json(setup)
                while True:
                    msg = await asyncio.wait_for(
                        ws.receive(),
                        timeout=self.owner.timeout_seconds,
                    )
                    payload = self.owner._ws_payload(msg)
                    self.owner._raise_ws_error(payload)
                    if "setupComplete" in payload:
                        self.ws = ws
                        self.connected_at = time.monotonic()
                        self.rotate_before_next = False
                        self.utterance_active = False
                        self._resampler.reset()
                        self._clear_final_queue()
                        self.owner.live_connections += 1
                        self._receiver_task = asyncio.create_task(
                            self._receive_loop(ws),
                            name=f"gemini-live-recv:{self.user_id}",
                        )
                        return
            except CaptionTranscriptionError:
                await ws.close()
                raise
            except asyncio.TimeoutError:
                await ws.close()
                raise CaptionTranscriptionError(
                    0,
                    "Gemini Live transcription timed out waiting for setupComplete.",
                ) from None
            except Exception:
                await ws.close()
                raise CaptionTranscriptionError(
                    0,
                    "Gemini Live transcription failed while processing its session setup response.",
                ) from None

    async def _ensure_connected(self) -> aiohttp.ClientWebSocketResponse:
        ws = self.ws
        pending_finals = bool(self._pending_finals)
        receiver_healthy = bool(
            self._receiver_task is not None
            and not self._receiver_task.done()
        )
        expired = bool(
            self.connected_at
            and time.monotonic() - self.connected_at
            >= self.owner.session_refresh_seconds
            and not self.utterance_active
            and not pending_finals
        )
        rotate_now = bool(
            self.rotate_before_next
            and not self.utterance_active
            and not pending_finals
        )
        receiver_dead = bool(ws is not None and not ws.closed and not receiver_healthy)
        if ws is None or ws.closed or expired or rotate_now or receiver_dead:
            if ws is not None:
                self.owner.live_reconnects += 1
            await self._connect()
        assert self.ws is not None
        return self.ws

    async def stream_pcm(self, pcm48_stereo: bytes) -> None:
        if not pcm48_stereo:
            return
        async with self._send_lock:
            ws = await self._ensure_connected()
            pcm16 = self._resampler.feed(pcm48_stereo)
            if not pcm16:
                return
            if not self.utterance_active:
                # Manual VAD has no server-side pre-speech buffer. Signal the
                # turn before the first streamed PCM chunk so the first syllable
                # is part of the same activity window.
                self._current_interims.clear()
                self._clear_final_queue()
                await ws.send_json({"realtimeInput": {"activityStart": {}}})
                self.owner.activity_starts += 1
                self.utterance_active = True
            await ws.send_json(
                {
                    "realtimeInput": {
                        "audio": {
                            "data": base64.b64encode(pcm16).decode("ascii"),
                            "mimeType": "audio/pcm;rate=16000",
                        }
                    }
                }
            )
            self.owner.audio_chunks_sent += 1
            self.owner.audio_bytes_sent += len(pcm16)

    async def seal_utterance(self) -> asyncio.Future[Any]:
        """End the current manual-VAD activity without waiting for its transcript.

        This lets the next Discord speech burst start streaming immediately after
        activityEnd while the receive loop resolves the previous turn's dedicated
        future. Per-turn futures prevent concurrent finalized transcripts from
        being consumed by the wrong segment task.
        """

        loop = asyncio.get_running_loop()
        async with self._send_lock:
            ws = await self._ensure_connected()
            waiter: asyncio.Future[Any] = loop.create_future()
            if not self.utterance_active:
                waiter.set_result(
                    TranscriptResult(
                        text="",
                        confidence=None,
                        provider="gemini-live",
                        model=self.owner.model,
                    )
                )
                return waiter

            fallback = self._stable_interim_candidate()
            self._current_interims.clear()
            if fallback is not None:
                self._sealed_interim_fallbacks[waiter] = fallback

            self._pending_finals.append(waiter)
            try:
                await ws.send_json({"realtimeInput": {"activityEnd": {}}})
            except Exception:
                try:
                    self._pending_finals.remove(waiter)
                except ValueError:
                    pass
                self._sealed_interim_fallbacks.pop(waiter, None)
                waiter.cancel()
                raise

            self.owner.activity_ends += 1
            self.utterance_active = False
            self._resampler.reset()
            return waiter

    async def wait_for_final(
        self,
        waiter: asyncio.Future[Any],
    ) -> TranscriptResult:
        try:
            result = await asyncio.wait_for(
                asyncio.shield(waiter),
                timeout=self.owner.timeout_seconds,
            )
        except asyncio.TimeoutError:
            fallback = self._sealed_interim_fallbacks.pop(waiter, None)
            try:
                self._pending_finals.remove(waiter)
            except ValueError:
                pass
            if not waiter.done():
                waiter.cancel()
            # A connected socket that accepted audio but stopped producing final
            # events is not trustworthy for the next turn. Reconnect cleanly
            # even when a stable interim can rescue this exact sealed utterance.
            await self.close()
            if fallback is not None:
                self.owner.interim_timeout_fallbacks += 1
                self.owner.fallback_count += 1
                return fallback
            raise CaptionTranscriptionError(
                0,
                "Gemini Live transcription timed out waiting for a finalized transcript.",
            ) from None
        except CaptionTranscriptionError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise

        if isinstance(result, Exception):
            await self.close()
            raise result
        if not isinstance(result, TranscriptResult):
            await self.close()
            raise CaptionTranscriptionError(
                0,
                "Gemini Live transcription returned an invalid finalized event.",
            )
        return result

    async def finalize(self) -> TranscriptResult:
        """Compatibility path that seals and then waits for one finalized turn."""

        waiter = await self.seal_utterance()
        return await self.wait_for_final(waiter)

    async def transcribe_buffered(self, pcm48_stereo: bytes) -> TranscriptResult:
        """Compatibility path for tests/repair tooling, not the production path."""

        # Replay at 20 ms cadence instead of blasting buffered audio into a Live
        # endpoint. Production CaptionEngine streams frames as they arrive and
        # uses the same activityStart/activityEnd manual-VAD contract.
        frame_bytes = int(PCM_SAMPLE_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH * 0.02)
        for offset in range(0, len(pcm48_stereo), frame_bytes):
            await self.stream_pcm(pcm48_stereo[offset : offset + frame_bytes])
            await asyncio.sleep(0.02)
        return await self.finalize()


class GeminiLiveTranscriber:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.5-transcribe-live",
        language_codes: Any = None,
        mode: str = "VERBATIM",
        custom_vocabulary: Any = None,
        timeout_seconds: float = 20.0,
        session_refresh_seconds: float = 510.0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "gemini-3.5-transcribe-live").strip()
        self.language_codes = _language_codes(language_codes)
        self.mode = "SMART" if str(mode or "").strip().upper() == "SMART" else "VERBATIM"
        raw_vocab = (
            list(custom_vocabulary)
            if isinstance(custom_vocabulary, (list, tuple, set, frozenset))
            else [part.strip() for part in str(custom_vocabulary or "").split(",")]
        )
        self.custom_vocabulary = [
            str(value).strip()[:80]
            for value in raw_vocab
            if str(value).strip()
        ][:100]
        self.timeout_seconds = max(5.0, min(45.0, float(timeout_seconds)))
        # Google documents a 10 minute max Live Transcribe session. Rotate
        # proactively so an utterance is not stranded at the hard boundary.
        self.session_refresh_seconds = max(
            120.0,
            min(540.0, float(session_refresh_seconds)),
        )
        self._sessions: dict[int, _GeminiLiveSpeakerSession] = {}
        self._user_language_codes: dict[int, list[str]] = {}
        self._last_audio_rms_dbfs: dict[int, float] = {}
        self._last_detected_language_code: dict[int, str] = {}
        self._http_session: Optional[aiohttp.ClientSession] = None
        self.live_connections = 0
        self.live_reconnects = 0
        self.audio_chunks_sent = 0
        self.audio_bytes_sent = 0
        self.activity_starts = 0
        self.activity_ends = 0
        self.interim_transcript_events = 0
        self.final_transcript_events = 0
        self.interim_timeout_fallbacks = 0
        self.language_hint_mismatches = 0
        self.fallback_count = 0
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Live Captions.")

    def language_codes_for_user(self, user_id: int) -> list[str]:
        uid = int(user_id)
        override = self._user_language_codes.get(uid)
        return list(override) if override is not None else list(self.language_codes)

    async def set_user_language_codes(self, user_id: int, values: Any) -> list[str]:
        uid = int(user_id)
        normalized = _language_codes(values)
        if normalized:
            self._user_language_codes[uid] = normalized
        else:
            self._user_language_codes.pop(uid, None)

        # Gemini Live transcription config is fixed at session setup. Close only
        # this speaker's provider session so the next utterance reconnects with
        # the new hint; no other Discord speaker is affected.
        session = self._sessions.pop(uid, None)
        if session is not None:
            await session.close()
        return list(normalized)

    def user_language_codes(self, user_id: int) -> list[str]:
        return list(self._user_language_codes.get(int(user_id), []))

    def last_audio_rms_dbfs(self, user_id: int) -> Optional[float]:
        return self._last_audio_rms_dbfs.get(int(user_id))

    def last_detected_language_code(self, user_id: int) -> str:
        return str(self._last_detected_language_code.get(int(user_id), "") or "")

    async def _http(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=None)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    @staticmethod
    def _ws_payload(msg: aiohttp.WSMessage) -> dict[str, Any]:
        # Gemini Live JSON may arrive in either a text frame or a binary frame.
        # Google's Python SDK deliberately reads raw websocket bytes before
        # json-decoding the setup response, so treating BINARY as "no payload"
        # can discard setupComplete/final transcript messages and cause a false
        # timeout even though the server replied correctly.
        if msg.type in {aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY}:
            raw = msg.data
            if isinstance(raw, (bytes, bytearray, memoryview)):
                try:
                    raw = bytes(raw).decode("utf-8")
                except UnicodeDecodeError:
                    raise CaptionTranscriptionError(
                        0,
                        "Gemini Live transcription returned non-UTF-8 WebSocket data.",
                    ) from None
            try:
                payload = json.loads(str(raw))
            except (TypeError, json.JSONDecodeError):
                raise CaptionTranscriptionError(
                    0,
                    "Gemini Live transcription returned invalid JSON.",
                ) from None
            return payload if isinstance(payload, dict) else {}
        if msg.type in {
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSING,
        }:
            close_code = int(msg.data or 0) if isinstance(msg.data, int) else 0
            suffix = f" (close code {close_code})" if close_code else ""
            raise CaptionTranscriptionError(
                close_code,
                f"Gemini Live transcription closed its WebSocket session{suffix}.",
            )
        if msg.type == aiohttp.WSMsgType.ERROR:
            raise CaptionTranscriptionError(
                0,
                "Gemini Live transcription WebSocket reported a connection error.",
            )
        # PING/PONG and other control frames contain no Gemini JSON payload.
        return {}

    @staticmethod
    def _raise_ws_error(payload: dict[str, Any]) -> None:
        error = payload.get("error")
        if isinstance(error, dict):
            status = int(error.get("code") or 0)
            raise _gemini_transcription_error(status, json.dumps({"error": error}))

    def _session_for_user(self, user_id: int) -> _GeminiLiveSpeakerSession:
        uid = int(user_id)
        session = self._sessions.get(uid)
        if session is None:
            session = _GeminiLiveSpeakerSession(self, uid)
            self._sessions[uid] = session
        return session

    def _validate_result(
        self,
        user_id: int,
        result: TranscriptResult,
    ) -> TranscriptResult:
        uid = int(user_id)
        if result.language_code:
            self._last_detected_language_code[uid] = result.language_code

        expected_codes = self.language_codes_for_user(uid)
        if expected_codes and result.language_code:
            expected_families = {
                str(code).split("-", 1)[0].casefold()
                for code in expected_codes
                if str(code).strip()
            }
            detected_family = (
                str(result.language_code).split("-", 1)[0].casefold()
            )
            if detected_family and detected_family not in expected_families:
                self.language_hint_mismatches += 1
                return TranscriptResult(
                    text="[unclear audio]",
                    confidence=None,
                    provider=result.provider,
                    model=result.model,
                    language_code=result.language_code,
                )
        return result

    async def stream_frame(self, frame: SpeakerPCMFrame) -> None:
        uid = int(frame.user_id)
        self._last_audio_rms_dbfs[uid] = pcm16_rms_dbfs(frame.pcm)
        session = self._session_for_user(uid)
        await session.stream_pcm(frame.pcm)

    async def seal_segment(
        self,
        segment: CaptionSegment,
    ) -> tuple[_GeminiLiveSpeakerSession, asyncio.Future[Any]]:
        """Send activityEnd now and return an exact turn token for later await."""

        uid = int(segment.user_id)
        self._last_audio_rms_dbfs[uid] = pcm16_rms_dbfs(segment.pcm)
        session = self._session_for_user(uid)
        waiter = await session.seal_utterance()
        return session, waiter

    async def finish_sealed_segment(
        self,
        segment: CaptionSegment,
        token: tuple[_GeminiLiveSpeakerSession, asyncio.Future[Any]],
    ) -> TranscriptResult:
        uid = int(segment.user_id)
        session, waiter = token
        result = await session.wait_for_final(waiter)
        return self._validate_result(uid, result)

    async def finish_segment(self, segment: CaptionSegment) -> TranscriptResult:
        """Compatibility path for callers that do not pre-seal the turn."""

        token = await self.seal_segment(segment)
        return await self.finish_sealed_segment(segment, token)

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        """Compatibility path for tests/repair callers.

        Production CaptionEngine streams each Discord PCM frame immediately and
        calls finish_segment() only when the local speech boundary closes.
        """

        uid = int(segment.user_id)
        self._last_audio_rms_dbfs[uid] = pcm16_rms_dbfs(segment.pcm)
        session = self._session_for_user(uid)
        result = await session.transcribe_buffered(segment.pcm)
        return self._validate_result(uid, result)


    async def prepare_user(self, user_id: int) -> None:
        """Establish the speaker's Live socket before PCM is admitted."""

        session = self._session_for_user(int(user_id))
        await session._ensure_connected()

    async def close_user(self, user_id: int) -> None:
        uid = int(user_id)
        session = self._sessions.pop(uid, None)
        if session is not None:
            await session.close()
        self._last_audio_rms_dbfs.pop(uid, None)
        self._last_detected_language_code.pop(uid, None)

    async def close(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None


class GeminiTextTranslator:
    API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.1-flash-lite",
        timeout_seconds: float = 15.0,
        cache_size: int = 256,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "gemini-3.1-flash-lite").strip()
        self.timeout_seconds = max(5.0, min(30.0, float(timeout_seconds)))
        self.cache_size = max(16, min(2000, int(cache_size)))
        self._cache: dict[tuple[str, str], str] = {}
        self.requests = 0
        self.cache_hits = 0
        self.failures = 0
        self.skipped = 0
        self.blocked_reason = ""
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Live Captions translation.")

    async def translate_to_english(
        self,
        text: str,
        *,
        language_code: str = "",
    ) -> Optional[str]:
        source = str(text or "").strip()
        lang = str(language_code or "").strip()
        if not source:
            return ""
        if lang.lower().startswith("en"):
            self.skipped += 1
            return source
        cache_key = (lang.casefold(), source)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if self.blocked_reason:
            self.skipped += 1
            return None

        prompt = (
            "Translate this finalized live caption into natural English. "
            "Preserve meaning, names, numbers, profanity, slang, and tone. "
            "Do not summarize, censor, explain, label the language, or add commentary. "
            "If the caption is already English, return it unchanged. "
            "Return only the translated caption text.\n\n"
            f"Caption:\n{source}"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "thinkingConfig": {"thinkingLevel": "minimal"},
                "responseMimeType": "text/plain",
                "maxOutputTokens": 1024,
            },
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        url = f"{self.API_ROOT}/{self.model}:generateContent"
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    headers={
                        "x-goog-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=body,
                ) as response:
                    raw = await response.text()
                    if response.status >= 400:
                        exc = _gemini_transcription_error(int(response.status), raw)
                        if exc.terminal:
                            self.blocked_reason = exc.safe_message
                        self.failures += 1
                        return None
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        self.failures += 1
                        return None
        except Exception:
            self.failures += 1
            return None

        translated = _gemini_text_from_generate_content(payload)
        self.requests += 1
        if not translated:
            self.failures += 1
            return None
        if len(self._cache) >= self.cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = translated
        return translated


class CaptionEngine:
    def __init__(
        self,
        transcriber: Any,
        publish: Callable[[int, str, Optional[float]], Awaitable[None]],
        *,
        translator: Optional[GeminiTextTranslator] = None,
        output_mode: str = "original",
        queue_size: int = 400,
        low_confidence_threshold: float = 0.72,
        unclear_threshold: float = 0.48,
        global_transcribe_semaphore: Optional[asyncio.Semaphore] = None,
    ) -> None:
        self.transcriber = transcriber
        self.translator = translator
        self.output_mode = normalize_caption_output_mode(output_mode)
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
        self._stream_failures_by_user: dict[int, Exception] = {}
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
        self._stream_failures_by_user.pop(uid, None)
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

        close_user = getattr(self.transcriber, "close_user", None)
        if callable(close_user):
            try:
                await close_user(uid)
            except Exception:
                log.debug("Live Captions provider speaker cleanup failed user=%s", uid, exc_info=True)

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
        self._stream_failures_by_user.clear()

        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.segmenter.flush_all()

        close_provider = getattr(self.transcriber, "close", None)
        if callable(close_provider):
            try:
                await close_provider()
            except Exception:
                log.debug("Live Captions transcription provider cleanup failed", exc_info=True)

    def _spawn_segment(
        self,
        segment: CaptionSegment,
        *,
        sealed_token: Any = None,
    ) -> None:
        uid = int(segment.user_id)
        if self._closed or not segment.pcm or uid in self._blocked_user_ids:
            return
        task = asyncio.create_task(
            self._process_segment_safely(segment, sealed_token),
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

    async def _process_segment_safely(
        self,
        segment: CaptionSegment,
        sealed_token: Any = None,
    ) -> None:
        try:
            await self._process_segment(segment, sealed_token)
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

    async def _stream_frame_safely(
        self,
        frame: SpeakerPCMFrame,
        stream_frame: Any,
    ) -> bool:
        uid = int(frame.user_id)
        if (
            not callable(stream_frame)
            or uid in self._blocked_user_ids
            or self.provider_blocked_reason
        ):
            return False
        try:
            # Production sends each isolated Discord PCM frame at arrival time.
            # Segment bookkeeping may run first only to discover whether a prior
            # activity must be sealed before this frame can begin a new turn.
            await stream_frame(frame)
            return True
        except Exception as exc:
            if uid not in self._stream_failures_by_user:
                self._stream_failures_by_user[uid] = exc
                self.last_failure = _safe_segment_failure(exc)[:300]
                log.warning(
                    "Live Captions realtime stream failed user=%s failure=%s",
                    uid,
                    self.last_failure,
                )
            if isinstance(exc, CaptionTranscriptionError) and exc.terminal:
                self.provider_blocked_reason = self.last_failure
                self.provider_blocked_code = (
                    exc.error_code
                    or exc.error_type
                    or str(exc.status_code)
                )
            return False

    async def _seal_and_spawn(self, segment: CaptionSegment) -> bool:
        uid = int(segment.user_id)
        if self._closed or not segment.pcm or uid in self._blocked_user_ids:
            return False
        if self.provider_blocked_reason:
            self._spawn_segment(segment)
            return True

        seal_segment = getattr(self.transcriber, "seal_segment", None)
        finish_sealed_segment = getattr(
            self.transcriber,
            "finish_sealed_segment",
            None,
        )
        if not callable(seal_segment) or not callable(finish_sealed_segment):
            self._spawn_segment(segment)
            return True

        try:
            # activityEnd must be on the wire before a post-gap frame is allowed
            # to open the next activity. Waiting for inputTranscription happens
            # later in the segment task, so realtime streaming never stalls here.
            token = await seal_segment(segment)
        except Exception as exc:
            if uid not in self._stream_failures_by_user:
                self._stream_failures_by_user[uid] = exc
            self.last_failure = _safe_segment_failure(exc)[:300]
            if isinstance(exc, CaptionTranscriptionError) and exc.terminal:
                self.provider_blocked_reason = self.last_failure
                self.provider_blocked_code = (
                    exc.error_code
                    or exc.error_type
                    or str(exc.status_code)
                )
            # feed(frame) may already have opened the next local buffer while
            # discovering a gap. Do not later publish audio that was never sent.
            self.segmenter.discard_user(uid)
            close_user = getattr(self.transcriber, "close_user", None)
            if callable(close_user):
                try:
                    await close_user(uid)
                except Exception:
                    pass
            self._spawn_segment(segment)
            return False

        self._spawn_segment(segment, sealed_token=token)
        return True

    async def _run(self) -> None:
        stream_frame = getattr(self.transcriber, "stream_frame", None)
        seal_segment = getattr(self.transcriber, "seal_segment", None)
        finish_sealed_segment = getattr(
            self.transcriber,
            "finish_sealed_segment",
            None,
        )
        ordered_live_stream = bool(
            callable(stream_frame)
            and callable(seal_segment)
            and callable(finish_sealed_segment)
        )

        while not self._closed:
            try:
                frame = await asyncio.wait_for(self.queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                for segment in self.segmenter.flush_idle():
                    if ordered_live_stream:
                        await self._seal_and_spawn(segment)
                    else:
                        self._spawn_segment(segment)
                continue

            if not ordered_live_stream:
                await self._stream_frame_safely(frame, stream_frame)
                for segment in self.segmenter.feed(frame):
                    self._spawn_segment(segment)
                continue

            # Feed first only to classify boundaries. A segment ending before
            # this frame is the previous utterance after a packet/speech gap and
            # must be sealed before this frame reaches Gemini. A segment ending
            # on this frame hit the max-duration boundary and is sealed after the
            # frame is streamed.
            ready = self.segmenter.feed(frame)
            before_frame = [
                segment
                for segment in ready
                if float(segment.ended_at) < float(frame.received_at)
            ]
            after_frame = [
                segment
                for segment in ready
                if float(segment.ended_at) >= float(frame.received_at)
            ]

            boundary_ok = True
            for segment in before_frame:
                if not await self._seal_and_spawn(segment):
                    boundary_ok = False
            if not boundary_ok:
                continue

            await self._stream_frame_safely(frame, stream_frame)

            for segment in after_frame:
                await self._seal_and_spawn(segment)

    async def _process_segment(
        self,
        segment: CaptionSegment,
        sealed_token: Any = None,
    ) -> None:
        if (
            not segment.pcm
            or self._closed
            or int(segment.user_id) in self._blocked_user_ids
        ):
            return
        uid = int(segment.user_id)
        stream_failure = self._stream_failures_by_user.pop(uid, None)
        if stream_failure is not None:
            close_user = getattr(self.transcriber, "close_user", None)
            if callable(close_user):
                try:
                    await close_user(uid)
                except Exception:
                    pass
            raise stream_failure
        if self.provider_blocked_reason:
            self.provider_skipped += 1
            return
        async with self._transcribe_semaphore:
            if self._global_transcribe_semaphore is None:
                await self._transcribe_and_publish(segment, sealed_token)
            else:
                async with self._global_transcribe_semaphore:
                    await self._transcribe_and_publish(segment, sealed_token)

    async def _transcribe_and_publish(
        self,
        segment: CaptionSegment,
        sealed_token: Any = None,
    ) -> None:
        uid = int(segment.user_id)
        if self._closed or uid in self._blocked_user_ids:
            return
        finish_sealed_segment = getattr(
            self.transcriber,
            "finish_sealed_segment",
            None,
        )
        finish_segment = getattr(self.transcriber, "finish_segment", None)
        if sealed_token is not None and callable(finish_sealed_segment):
            first = await finish_sealed_segment(segment, sealed_token)
        elif callable(finish_segment):
            first = await finish_segment(segment)
        else:
            first = await self.transcriber.transcribe(segment)
        if self._closed or uid in self._blocked_user_ids:
            return
        chosen = first

        if (
            sealed_token is None
            and not callable(finish_segment)
            and first.confidence is not None
            and first.confidence < self.low_confidence_threshold
        ):
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

        rendered = chosen.text
        if (
            chosen.text != "[unclear audio]"
            and self.output_mode in {"english", "bilingual"}
            and self.translator is not None
        ):
            translated = await self.translator.translate_to_english(
                chosen.text,
                language_code=chosen.language_code,
            )
            if self._closed or uid in self._blocked_user_ids:
                return
            if self.output_mode == "english":
                rendered = (
                    translated
                    if translated
                    else f"⚠️ English translation unavailable · Original: {chosen.text}"
                )
            elif translated and translated.casefold() != chosen.text.casefold():
                rendered = f"{chosen.text}\n🌐 **English:** {translated}"
            elif translated:
                rendered = chosen.text
            else:
                rendered = f"{chosen.text}\n🌐 **English:** [translation unavailable]"

        await self.publish(segment.user_id, rendered[:1800], chosen.confidence)
        self.segments_published += 1


def gemini_live_transcriber_from_env(
    *,
    language_codes: Any = None,
) -> GeminiLiveTranscriber:
    configured = (
        language_codes
        if language_codes is not None
        else os.getenv("DANK_COMMUNITY_CAPTION_LANGUAGE_CODES", "")
    )
    return GeminiLiveTranscriber(
        os.getenv("GEMINI_API_KEY", ""),
        model=os.getenv(
            "DANK_COMMUNITY_CAPTION_LIVE_MODEL",
            "gemini-3.5-transcribe-live",
        ),
        language_codes=configured,
        mode=os.getenv("DANK_COMMUNITY_CAPTION_TRANSCRIPTION_MODE", "VERBATIM"),
        custom_vocabulary=os.getenv("DANK_COMMUNITY_CAPTION_CUSTOM_VOCABULARY", ""),
    )


def gemini_text_translator_from_env() -> GeminiTextTranslator:
    return GeminiTextTranslator(
        os.getenv("GEMINI_API_KEY", ""),
        model=os.getenv(
            "DANK_COMMUNITY_CAPTION_TRANSLATION_MODEL",
            "gemini-3.1-flash-lite",
        ),
    )


__all__ = [
    "CaptionEngine",
    "CaptionSegment",
    "CaptionTranscriptionError",
    "GeminiLiveTranscriber",
    "GeminiTextTranslator",
    "SpeechPreservingSegmenter",
    "TranscriptResult",
    "_gemini_text_from_generate_content",
    "_gemini_transcription_error",
    "gemini_live_transcriber_from_env",
    "gemini_text_translator_from_env",
    "normalize_caption_output_mode",
    "normalize_pcm16_lossless_timing",
    "pcm16_rms_dbfs",
    "pcm16_to_wav",
    "pcm48_stereo_to_pcm16_mono",
]
