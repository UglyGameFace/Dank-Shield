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
        self.lock = asyncio.Lock()

    async def close(self) -> None:
        ws = self.ws
        self.ws = None
        self.connected_at = 0.0
        self.rotate_before_next = False
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass

    async def _connect(self) -> None:
        await self.close()
        http = await self.owner._http()
        url = f"{self.WS_URL}?key={self.owner.api_key}"
        try:
            ws = await http.ws_connect(
                url,
                heartbeat=20.0,
                autoping=True,
                receive_timeout=self.owner.timeout_seconds,
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
            transcription_config["customVocabulary"] = list(self.owner.custom_vocabulary)

        setup = {
            "setup": {
                "model": f"models/{self.owner.model}",
                "generationConfig": {"responseModalities": ["TEXT"]},
                # Dank Shield has already isolated and segmented one Discord
                # speaker before Gemini sees this audio. Use Google's documented
                # manual-VAD mode for this buffered utterance so finalization is
                # explicit and deterministic.
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
                if "setupComplete" in payload:
                    self.ws = ws
                    self.connected_at = time.monotonic()
                    self.rotate_before_next = False
                    self.owner.live_connections += 1
                    return
                self.owner._raise_ws_error(payload)
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
        expired = bool(
            self.connected_at
            and time.monotonic() - self.connected_at >= self.owner.session_refresh_seconds
        )
        if ws is None or ws.closed or expired or self.rotate_before_next:
            if ws is not None:
                self.owner.live_reconnects += 1
            await self._connect()
        assert self.ws is not None
        return self.ws

    async def transcribe(self, pcm: bytes) -> TranscriptResult:
        async with self.lock:
            ws = await self._ensure_connected()
            pcm16 = pcm48_stereo_to_pcm16_mono(pcm)
            if not pcm16:
                return TranscriptResult(
                    text="",
                    confidence=None,
                    provider="gemini-live",
                    model=self.owner.model,
                )

            try:
                # The local segmenter has already established a single-speaker
                # utterance boundary. In manual VAD mode Google requires
                # activityStart before audio and activityEnd when speech ends.
                await ws.send_json({"realtimeInput": {"activityStart": {}}})
                chunk_bytes = 1280  # 40 ms at 16 kHz mono signed-16 PCM.
                for offset in range(0, len(pcm16), chunk_bytes):
                    chunk = pcm16[offset : offset + chunk_bytes]
                    await ws.send_json(
                        {
                            "realtimeInput": {
                                "audio": {
                                    "data": base64.b64encode(chunk).decode("ascii"),
                                    "mimeType": "audio/pcm;rate=16000",
                                }
                            }
                        }
                    )
                await ws.send_json({"realtimeInput": {"activityEnd": {}}})

                while True:
                    msg = await asyncio.wait_for(
                        ws.receive(),
                        timeout=self.owner.timeout_seconds,
                    )
                    payload = self.owner._ws_payload(msg)
                    self.owner._raise_ws_error(payload)
                    if "goAway" in payload:
                        # Google may warn before a Live session is rotated. Keep
                        # reading this utterance, then reconnect before the next.
                        self.rotate_before_next = True
                    content = payload.get("serverContent")
                    if not isinstance(content, dict):
                        continue
                    final = content.get("inputTranscription")
                    if not isinstance(final, dict):
                        continue
                    text = str(final.get("text") or "").strip()
                    language_code = str(final.get("languageCode") or "").strip()
                    return TranscriptResult(
                        text=text,
                        confidence=None,
                        provider="gemini-live",
                        model=self.owner.model,
                        language_code=language_code,
                    )
            except CaptionTranscriptionError:
                await self.close()
                raise
            except asyncio.TimeoutError:
                await self.close()
                raise CaptionTranscriptionError(
                    0,
                    "Gemini Live transcription timed out waiting for a finalized transcript.",
                ) from None
            except Exception:
                await self.close()
                raise CaptionTranscriptionError(
                    0,
                    "Gemini Live transcription connection failed while processing audio.",
                ) from None


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

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        uid = int(segment.user_id)
        self._last_audio_rms_dbfs[uid] = pcm16_rms_dbfs(segment.pcm)
        session = self._sessions.get(uid)
        if session is None:
            session = _GeminiLiveSpeakerSession(self, uid)
            self._sessions[uid] = session
        result = await session.transcribe(segment.pcm)
        if result.language_code:
            self._last_detected_language_code[uid] = result.language_code

        expected_codes = self.language_codes_for_user(uid)
        if expected_codes and result.language_code:
            expected_families = {
                str(code).split("-", 1)[0].casefold()
                for code in expected_codes
                if str(code).strip()
            }
            detected_family = str(result.language_code).split("-", 1)[0].casefold()
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
