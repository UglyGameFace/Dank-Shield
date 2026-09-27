from __future__ import annotations

import asyncio
import math
import struct
from pathlib import Path

import aiohttp

import stoney_verify.community_voice_captions as captions_module
from stoney_verify.community_voice_captions import (
    CaptionEngine,
    CaptionSegment,
    CaptionTranscriptionError,
    GeminiLiveTranscriber,
    GeminiTextTranslator,
    _gemini_text_from_generate_content,
    _gemini_transcription_error,
    SpeechPreservingSegmenter,
    TranscriptResult,
    normalize_caption_output_mode,
    normalize_pcm16_lossless_timing,
    pcm16_rms_dbfs,
    pcm48_stereo_to_pcm16_mono,
)
from stoney_verify.community_voice_receive import SpeakerPCMFrame


ROOT = Path(__file__).resolve().parents[1]


def _pcm(value: int, samples: int = 1920) -> bytes:
    return struct.pack("<" + ("h" * samples), *([value] * samples))


def _frame(user_id: int, at: float, value: int) -> SpeakerPCMFrame:
    return SpeakerPCMFrame(
        user_id=user_id,
        ssrc=user_id * 10,
        sequence=1,
        rtp_timestamp=960,
        pcm=_pcm(value),
        received_at=at,
    )


def test_segmenter_never_combines_different_discord_users() -> None:
    segmenter = SpeechPreservingSegmenter(silence_gap_seconds=0.5, max_segment_seconds=8)
    assert segmenter.feed(_frame(10, 1.0, 100)) == []
    assert segmenter.feed(_frame(20, 1.1, 200)) == []
    segments = segmenter.flush_all()

    assert sorted(segment.user_id for segment in segments) == [10, 20]
    one = next(segment for segment in segments if segment.user_id == 10)
    two = next(segment for segment in segments if segment.user_id == 20)
    assert one.pcm == _pcm(100)
    assert two.pcm == _pcm(200)


def test_gap_flush_preserves_all_samples_from_previous_utterance() -> None:
    segmenter = SpeechPreservingSegmenter(silence_gap_seconds=0.5, max_segment_seconds=8)
    first = _frame(10, 1.0, 111)
    second = _frame(10, 1.1, 222)
    later = _frame(10, 2.0, 333)

    assert segmenter.feed(first) == []
    assert segmenter.feed(second) == []
    flushed = segmenter.feed(later)
    assert len(flushed) == 1
    assert flushed[0].pcm == first.pcm + second.pcm


def test_normalization_changes_gain_not_sample_count_or_timing() -> None:
    pcm = _pcm(1000)
    normalized = normalize_pcm16_lossless_timing(pcm)
    assert len(normalized) == len(pcm)
    assert normalized != pcm

    loud = _pcm(30000)
    assert normalize_pcm16_lossless_timing(loud) == loud


class _TwoPassTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        self.calls += 1
        if self.calls == 1:
            return TranscriptResult("meet at spawn", 0.30, "fake", "fake")
        return TranscriptResult("meet at spawn", 0.92, "fake", "fake")


def test_low_confidence_retry_prefers_agreeing_higher_confidence_result() -> None:
    published = []

    async def _run() -> None:
        transcriber = _TwoPassTranscriber()

        async def publish(user_id: int, text: str, confidence: float) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(transcriber, publish)
        await engine._process_segment(
            CaptionSegment(
                user_id=10,
                pcm=_pcm(800),
                started_at=1.0,
                ended_at=2.0,
            )
        )
        assert transcriber.calls == 2

    asyncio.run(_run())
    assert published == [(10, "meet at spawn", 0.92)]


class _DisagreeingTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        self.calls += 1
        if self.calls == 1:
            return TranscriptResult("meet at spawn", 0.31, "fake", "fake")
        return TranscriptResult("we need a spoon", 0.35, "fake", "fake")


def test_low_confidence_disagreement_becomes_unclear_not_invented_text() -> None:
    published = []

    async def _run() -> None:
        transcriber = _DisagreeingTranscriber()

        async def publish(user_id: int, text: str, confidence: float) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(transcriber, publish)
        await engine._process_segment(
            CaptionSegment(
                user_id=20,
                pcm=_pcm(700),
                started_at=1.0,
                ended_at=2.0,
            )
        )

    asyncio.run(_run())
    assert published[0][0] == 20
    assert published[0][1] == "[unclear audio]"


class _NoConfidenceGeminiLikeTranscriber:
    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        return TranscriptResult("meet at spawn", None, "gemini", "gemini-3.5-transcribe")


def test_missing_provider_confidence_is_not_invented_or_treated_as_zero() -> None:
    published = []

    async def _run() -> None:
        async def publish(user_id: int, text: str, confidence) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(_NoConfidenceGeminiLikeTranscriber(), publish)
        await engine._process_segment(
            CaptionSegment(
                user_id=29,
                pcm=_pcm(30_000),
                started_at=1.0,
                ended_at=2.0,
            )
        )

    asyncio.run(_run())
    assert published == [(29, "meet at spawn", None)]


class _ZeroConfidenceTranscriber:
    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        return TranscriptResult("possibly wrong words", 0.0, "fake", "fake")


def test_zero_confidence_is_never_published_as_certain_text() -> None:
    published = []

    async def _run() -> None:
        async def publish(user_id: int, text: str, confidence: float) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(_ZeroConfidenceTranscriber(), publish)
        await engine._process_segment(
            CaptionSegment(
                user_id=30,
                pcm=_pcm(30_000),
                started_at=1.0,
                ended_at=2.0,
            )
        )

    asyncio.run(_run())
    assert published == [(30, "[unclear audio]", 0.0)]


class _BlockingTranscriber:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_gemini_resource_exhausted_is_terminal_for_current_session() -> None:
    exc = _gemini_transcription_error(
        429,
        '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"secret provider detail"}}',
    )
    assert exc.terminal is True
    assert exc.error_code == "RESOURCE_EXHAUSTED"
    assert "free-tier quota or rate limit" in exc.safe_message
    assert "secret provider detail" not in exc.safe_message


def test_gemini_server_error_is_retryable() -> None:
    exc = _gemini_transcription_error(
        503,
        '{"error":{"code":503,"status":"UNAVAILABLE","message":"backend detail"}}',
    )
    assert exc.terminal is False
    assert "temporarily unavailable" in exc.safe_message
    assert "backend detail" not in exc.safe_message


def test_gemini_generate_content_text_ignores_thought_parts() -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "internal reasoning"},
                        {"text": "hello from voice"},
                    ]
                }
            }
        ]
    }
    assert _gemini_text_from_generate_content(payload) == "hello from voice"


def test_gemini_live_parses_binary_setup_complete_json() -> None:
    msg = aiohttp.WSMessage(
        aiohttp.WSMsgType.BINARY,
        b'{"setupComplete":{}}',
        "",
    )
    payload = GeminiLiveTranscriber._ws_payload(msg)
    assert payload == {"setupComplete": {}}


def test_gemini_live_parses_binary_final_transcript_json() -> None:
    msg = aiohttp.WSMessage(
        aiohttp.WSMsgType.BINARY,
        (
            b'{"serverContent":{"inputTranscription":'
            b'{"text":"hola","languageCode":"es-ES"}}}'
        ),
        "",
    )
    payload = GeminiLiveTranscriber._ws_payload(msg)
    final = payload["serverContent"]["inputTranscription"]
    assert final["text"] == "hola"
    assert final["languageCode"] == "es-ES"


def test_gemini_live_rejects_non_utf8_binary_payload() -> None:
    msg = aiohttp.WSMessage(
        aiohttp.WSMsgType.BINARY,
        b"\xff\xfe",
        "",
    )
    try:
        GeminiLiveTranscriber._ws_payload(msg)
    except CaptionTranscriptionError as exc:
        assert "non-UTF-8" in exc.safe_message
    else:
        raise AssertionError("binary non-UTF-8 payload must fail closed")


def test_gemini_live_defaults_to_all_language_auto_detection() -> None:
    transcriber = GeminiLiveTranscriber("fake-key")
    assert transcriber.model == "gemini-3.5-transcribe-live"
    assert transcriber.language_codes == []
    assert transcriber.mode == "VERBATIM"


def test_pcm48_stereo_to_pcm16_mono_has_correct_rate_ratio() -> None:
    # 480 stereo frames = 10ms at 48kHz. The Live API wants 160 mono
    # samples for the same 10ms at 16kHz.
    stereo_samples = []
    for i in range(480):
        stereo_samples.extend([1000 + i, -1000 + i])
    pcm = struct.pack("<" + ("h" * len(stereo_samples)), *stereo_samples)
    converted = pcm48_stereo_to_pcm16_mono(pcm)
    assert len(converted) == 160 * 2


def _stereo_sine(frequency_hz: float, *, seconds: float = 0.20, peak: int = 12000) -> bytes:
    frames = int(48_000 * seconds)
    samples = []
    for index in range(frames):
        value = int(round(peak * math.sin(2.0 * math.pi * frequency_hz * index / 48_000.0)))
        samples.extend([value, value])
    return struct.pack("<" + ("h" * len(samples)), *samples)


def test_pcm_downsampler_preserves_voice_band_and_suppresses_alias_energy() -> None:
    voice = pcm48_stereo_to_pcm16_mono(_stereo_sine(1000.0))
    ultrasonic = pcm48_stereo_to_pcm16_mono(_stereo_sine(12000.0))

    voice_level = pcm16_rms_dbfs(voice)
    alias_level = pcm16_rms_dbfs(ultrasonic)

    # A 1kHz speech-band tone stays essentially intact while 12kHz energy,
    # which would fold into the 16kHz target band without a real low-pass,
    # is heavily attenuated before decimation.
    assert voice_level > -13.0
    assert alias_level < voice_level - 35.0


def test_gemini_live_supports_per_speaker_language_hints_without_affecting_others() -> None:
    async def _run() -> None:
        transcriber = GeminiLiveTranscriber("fake-key", language_codes=[])
        assert transcriber.language_codes_for_user(10) == []
        assert transcriber.language_codes_for_user(20) == []

        await transcriber.set_user_language_codes(10, ["en-US"])
        assert transcriber.language_codes_for_user(10) == ["en-US"]
        assert transcriber.language_codes_for_user(20) == []

        await transcriber.set_user_language_codes(10, [])
        assert transcriber.language_codes_for_user(10) == []

    asyncio.run(_run())


def test_explicit_english_hint_rejects_unrelated_detected_language() -> None:
    class FakeSession:
        async def transcribe_buffered(self, pcm: bytes) -> TranscriptResult:
            assert pcm
            return TranscriptResult(
                "तो बात",
                None,
                "gemini-live",
                "gemini-3.5-transcribe-live",
                "hi-IN",
            )

        async def close(self) -> None:
            return None

    async def _run() -> None:
        transcriber = GeminiLiveTranscriber("fake-key")
        await transcriber.set_user_language_codes(55, ["en-US"])
        transcriber._sessions[55] = FakeSession()
        result = await transcriber.transcribe(
            CaptionSegment(55, _pcm(900), 1.0, 2.0)
        )
        assert result.text == "[unclear audio]"
        assert result.language_code == "hi-IN"
        assert transcriber.language_hint_mismatches == 1
        assert transcriber.last_detected_language_code(55) == "hi-IN"

    asyncio.run(_run())


def test_streaming_resampler_converts_each_discord_20ms_frame_to_16khz() -> None:
    resampler = captions_module._StreamingPCM48To16Mono()
    # _pcm() is 1920 signed-16 samples = 960 stereo frames = 20ms at 48kHz.
    converted = resampler.feed(_pcm(1200))
    # 20ms at 16kHz mono = 320 signed-16 samples = 640 bytes.
    assert len(converted) == 640


def test_caption_engine_streams_frames_before_local_segment_finalization() -> None:
    events = []
    published = []

    class StreamingTranscriber:
        async def stream_frame(self, frame: SpeakerPCMFrame) -> None:
            events.append(("stream", frame.user_id, frame.pcm))

        async def finish_segment(self, segment: CaptionSegment) -> TranscriptResult:
            events.append(("finish", segment.user_id, segment.pcm))
            return TranscriptResult(
                "realtime speech",
                None,
                "gemini-live",
                "gemini-3.5-transcribe-live",
                "en-US",
            )

        async def close(self) -> None:
            return None

    class OneFrameSegmenter:
        def feed(self, frame: SpeakerPCMFrame):
            events.append(("segment", frame.user_id, frame.pcm))
            return [
                CaptionSegment(
                    frame.user_id,
                    frame.pcm,
                    frame.received_at,
                    frame.received_at,
                )
            ]

        def flush_idle(self):
            return []

        def flush_all(self):
            return []

        def discard_user(self, user_id: int):
            return None

    async def _run() -> None:
        async def publish(user_id: int, text: str, confidence) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(StreamingTranscriber(), publish)
        engine.segmenter = OneFrameSegmenter()
        engine.start()
        engine.submit(_frame(91, 1.0, 777))
        for _ in range(20):
            if published:
                break
            await asyncio.sleep(0.01)
        await engine.close()

    asyncio.run(_run())
    assert events[0][0] == "stream"
    assert events[1][0] == "segment"
    assert any(event[0] == "finish" for event in events)
    assert published == [(91, "realtime speech", None)]


def test_gemini_live_finalize_sends_stream_end_without_resending_audio() -> None:
    async def _run() -> None:
        owner = GeminiLiveTranscriber("fake-key")
        session = captions_module._GeminiLiveSpeakerSession(owner, 92)

        class FakeWS:
            closed = False

            def __init__(self) -> None:
                self.sent = []

            async def send_json(self, payload) -> None:
                self.sent.append(payload)

        ws = FakeWS()
        session.ws = ws
        session.connected_at = 1.0
        session.utterance_active = True
        session._final_queue.put_nowait(
            TranscriptResult(
                "done",
                None,
                "gemini-live",
                owner.model,
                "en-US",
            )
        )

        result = await session.finalize()
        assert result.text == "done"
        assert ws.sent == [{"realtimeInput": {"audioStreamEnd": True}}]
        assert owner.audio_stream_ends == 1

    asyncio.run(_run())


def test_production_live_path_does_not_buffer_whole_utterance_before_send() -> None:
    source = (
        ROOT / "stoney_verify" / "community_voice_captions.py"
    ).read_text(encoding="utf-8")
    engine_block = source.split("class CaptionEngine", 1)[1]
    assert "await stream_frame(frame)" in engine_block
    assert "await finish_segment(segment)" in engine_block
    assert "Production CaptionEngine streams frames as they arrive." in source


def test_gemini_live_uses_hybrid_vad_and_audio_stream_end() -> None:
    source = (ROOT / "stoney_verify" / "community_voice_captions.py").read_text(encoding="utf-8")
    session_block = source.split("class _GeminiLiveSpeakerSession", 1)[1].split("class GeminiLiveTranscriber", 1)[0]

    assert '"audioStreamEnd": True' in session_block
    assert '"activityStart"' not in session_block
    assert '"activityEnd"' not in session_block
    assert '"automaticActivityDetection": {"disabled": True}' not in session_block


def test_gemini_live_goaway_forces_reconnect_before_next_utterance() -> None:
    async def _run() -> None:
        owner = GeminiLiveTranscriber("fake-key")
        session = captions_module._GeminiLiveSpeakerSession(owner, 77)
        session.ws = type("FakeWS", (), {"closed": False})()
        session.connected_at = 100.0
        session.rotate_before_next = True
        replacement = type("FakeWS", (), {"closed": False})()

        async def fake_connect() -> None:
            session.ws = replacement
            session.connected_at = 200.0
            session.rotate_before_next = False

        session._connect = fake_connect
        resolved = await session._ensure_connected()
        assert resolved is replacement
        assert owner.live_reconnects == 1
        assert session.rotate_before_next is False

    asyncio.run(_run())


def test_caption_output_mode_normalization() -> None:
    assert normalize_caption_output_mode("original") == "original"
    assert normalize_caption_output_mode("english") == "english"
    assert normalize_caption_output_mode("Original + English") == "bilingual"
    assert normalize_caption_output_mode("both") == "bilingual"
    assert normalize_caption_output_mode("anything-else") == "original"


def test_text_translation_skips_detected_english_without_api_request() -> None:
    async def _run() -> None:
        translator = GeminiTextTranslator("fake-key")
        result = await translator.translate_to_english(
            "meet at spawn",
            language_code="en-US",
        )
        assert result == "meet at spawn"
        assert translator.requests == 0
        assert translator.skipped == 1

    asyncio.run(_run())


class _SpanishLiveTranscriber:
    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        return TranscriptResult(
            "nos vemos en el punto de aparición",
            None,
            "gemini-live",
            "gemini-3.5-transcribe-live",
            "es-ES",
        )


class _FakeEnglishTranslator:
    def __init__(self) -> None:
        self.calls = 0

    async def translate_to_english(self, text: str, *, language_code: str = "") -> str:
        self.calls += 1
        assert language_code == "es-ES"
        assert "aparición" in text
        return "see you at spawn"


def test_bilingual_mode_translates_finalized_text_only() -> None:
    published = []

    async def _run() -> None:
        translator = _FakeEnglishTranslator()

        async def publish(user_id: int, text: str, confidence) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(
            _SpanishLiveTranscriber(),
            publish,
            translator=translator,
            output_mode="bilingual",
        )
        await engine._process_segment(
            CaptionSegment(31, _pcm(900), 1.0, 2.0)
        )
        assert translator.calls == 1

    asyncio.run(_run())
    assert published == [
        (
            31,
            "nos vemos en el punto de aparición\n🌐 **English:** see you at spawn",
            None,
        )
    ]


def test_original_mode_never_calls_translation() -> None:
    class _ExplodingTranslator:
        async def translate_to_english(self, text: str, *, language_code: str = "") -> str:
            raise AssertionError("original mode must not translate")

    published = []

    async def _run() -> None:
        async def publish(user_id: int, text: str, confidence) -> None:
            published.append(text)

        engine = CaptionEngine(
            _SpanishLiveTranscriber(),
            publish,
            translator=_ExplodingTranslator(),
            output_mode="original",
        )
        await engine._process_segment(CaptionSegment(32, _pcm(900), 1.0, 2.0))

    asyncio.run(_run())
    assert published == ["nos vemos en el punto de aparición"]


class _TerminalQuotaTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        self.calls += 1
        raise CaptionTranscriptionError(
            429,
            "Gemini free-tier quota or rate limit was reached (HTTP 429 RESOURCE_EXHAUSTED). Check this AI Studio project's active model limits, then restart the caption session after quota is available.",
            error_code="RESOURCE_EXHAUSTED",
            error_type="RESOURCE_EXHAUSTED",
            terminal=True,
        )


def test_terminal_provider_error_blocks_repeat_requests_for_session() -> None:
    async def _run() -> None:
        async def publish(user_id: int, text: str, confidence: float) -> None:
            raise AssertionError("quota failure must not publish")

        transcriber = _TerminalQuotaTranscriber()
        engine = CaptionEngine(transcriber, publish)
        segment = CaptionSegment(user_id=34, pcm=_pcm(900), started_at=1.0, ended_at=2.0)

        await engine._process_segment_safely(segment)
        await engine._process_segment_safely(segment)

        assert transcriber.calls == 1
        assert engine.segment_failures == 1
        assert engine.provider_skipped == 1
        assert engine.provider_blocked_code == "RESOURCE_EXHAUSTED"
        assert "free-tier quota or rate limit" in engine.provider_blocked_reason

    asyncio.run(_run())


class _QuotaFailingTranscriber:
    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        raise CaptionTranscriptionError(
            429,
            "Gemini free-tier quota or rate limit was reached (HTTP 429 RESOURCE_EXHAUSTED).",
        )


def test_segment_failure_records_safe_provider_diagnostic() -> None:
    async def _run() -> None:
        async def publish(user_id: int, text: str, confidence: float) -> None:
            raise AssertionError("failed transcription must not publish")

        engine = CaptionEngine(_QuotaFailingTranscriber(), publish)
        await engine._process_segment_safely(
            CaptionSegment(
                user_id=35,
                pcm=_pcm(900),
                started_at=1.0,
                ended_at=2.0,
            )
        )
        assert engine.segment_failures == 1
        assert "HTTP 429" in engine.last_failure
        assert "RESOURCE_EXHAUSTED" in engine.last_failure
        assert engine.segments_published == 0

    asyncio.run(_run())


def test_empty_transcription_is_counted_separately_from_publish() -> None:
    class _EmptyTranscriber:
        async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
            return TranscriptResult("", 0.99, "fake", "fake")

    async def _run() -> None:
        published = []

        async def publish(user_id: int, text: str, confidence: float) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(_EmptyTranscriber(), publish)
        await engine._process_segment(
            CaptionSegment(
                user_id=36,
                pcm=_pcm(900),
                started_at=1.0,
                ended_at=2.0,
            )
        )
        assert engine.segments_transcribed == 1
        assert engine.segments_empty == 1
        assert engine.segments_published == 0
        assert published == []

    asyncio.run(_run())


def test_close_cancels_inflight_transcription_before_it_can_publish() -> None:
    published = []

    async def _run() -> None:
        transcriber = _BlockingTranscriber()

        async def publish(user_id: int, text: str, confidence: float) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(transcriber, publish)
        engine._spawn_segment(
            CaptionSegment(
                user_id=40,
                pcm=_pcm(900),
                started_at=1.0,
                ended_at=2.0,
            )
        )
        await asyncio.wait_for(transcriber.started.wait(), timeout=1.0)
        await asyncio.wait_for(engine.close(), timeout=1.0)
        await asyncio.sleep(0)
        assert not engine._segment_tasks

    asyncio.run(_run())
    assert published == []


def test_revoke_user_purges_buffered_queued_and_inflight_audio() -> None:
    published = []

    async def _run() -> None:
        transcriber = _BlockingTranscriber()

        async def publish(user_id: int, text: str, confidence: float) -> None:
            published.append((user_id, text, confidence))

        engine = CaptionEngine(transcriber, publish)
        engine.segmenter.feed(_frame(50, 1.0, 500))
        engine.submit(_frame(50, 1.1, 600))
        engine._spawn_segment(
            CaptionSegment(
                user_id=50,
                pcm=_pcm(700),
                started_at=1.0,
                ended_at=2.0,
            )
        )
        await asyncio.wait_for(transcriber.started.wait(), timeout=1.0)

        await asyncio.wait_for(engine.revoke_user(50), timeout=1.0)
        assert engine.segmenter.flush_all() == []
        assert engine.queue.empty()
        assert not engine._segment_tasks_by_user.get(50)
        assert published == []

        # A frame already scheduled onto the event loop after consent revocation
        # must still be rejected by the engine-side block.
        engine.submit(_frame(50, 2.0, 800))
        assert engine.queue.empty()

        # A later explicit opt-in opens only future audio again.
        engine.allow_user(50)
        engine.submit(_frame(50, 2.1, 900))
        assert engine.queue.qsize() == 1
        await engine.close()

    asyncio.run(_run())
    assert published == []


def test_live_caption_privacy_disclosure_and_soak_gate_are_contractual() -> None:
    runtime = (ROOT / "stoney_verify" / "community_voice_caption_runtime.py").read_text(encoding="utf-8")
    ui = (ROOT / "stoney_verify" / "commands_ext" / "public_community_hub.py").read_text(encoding="utf-8")

    assert "DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED" in runtime
    assert "DANK_COMMUNITY_CAPTION_MAX_ACTIVE_GUILDS" in runtime
    assert "DANK_COMMUNITY_CAPTION_MAX_SPEAKERS" in runtime
    assert "DANK_COMMUNITY_CAPTION_MAX_CONCURRENT_TRANSCRIPTIONS" in runtime
    assert "until the DAVE receive soak test is completed" in runtime
    assert "Google Gemini's transcription API" in runtime
    assert "Google Gemini's transcription API" in ui
    assert "Gemini's **Free Tier**" in runtime
    assert "used to improve its products" in runtime
    assert "used to improve its products" in ui
    assert "gemini-3.5-transcribe-live" in (ROOT / "stoney_verify" / "community_voice_captions.py").read_text(encoding="utf-8")
    assert "languageCodes" in (ROOT / "stoney_verify" / "community_voice_captions.py").read_text(encoding="utf-8")
    assert "audio/pcm;rate=16000" in (ROOT / "stoney_verify" / "community_voice_captions.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in runtime
    assert "api.openai.com" not in (ROOT / "stoney_verify" / "community_voice_captions.py").read_text(encoding="utf-8")
    assert "Caption My Voice" in ui

    # The privacy notice must succeed before the runtime is registered active.
    assert runtime.index("await destination.send(") < runtime.index("self._sessions[sid] = state")
    assert "captions were not started" in runtime


class _ConcurrencyProbeTranscriber:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            await self.release.wait()
            return TranscriptResult("ok", 0.99, "fake", "fake")
        finally:
            self.active -= 1


def test_shared_transcription_semaphore_bounds_engines_across_sessions() -> None:
    async def _run() -> None:
        transcriber = _ConcurrencyProbeTranscriber()
        shared = asyncio.Semaphore(1)

        async def publish(user_id: int, text: str, confidence: float) -> None:
            return None

        first = CaptionEngine(
            transcriber,
            publish,
            global_transcribe_semaphore=shared,
        )
        second = CaptionEngine(
            transcriber,
            publish,
            global_transcribe_semaphore=shared,
        )
        one = asyncio.create_task(
            first._process_segment(
                CaptionSegment(1, _pcm(30_000), 1.0, 2.0)
            )
        )
        two = asyncio.create_task(
            second._process_segment(
                CaptionSegment(2, _pcm(30_000), 1.0, 2.0)
            )
        )
        await asyncio.wait_for(transcriber.started.wait(), timeout=1.0)
        await asyncio.sleep(0)
        assert transcriber.peak == 1
        transcriber.release.set()
        await asyncio.gather(one, two)

    asyncio.run(_run())
