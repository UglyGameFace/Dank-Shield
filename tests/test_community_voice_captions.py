from __future__ import annotations

import asyncio
import struct
from pathlib import Path

from stoney_verify.community_voice_captions import (
    CaptionEngine,
    CaptionSegment,
    CaptionTranscriptionError,
    SpeechPreservingSegmenter,
    TranscriptResult,
    normalize_pcm16_lossless_timing,
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


class _QuotaFailingTranscriber:
    async def transcribe(self, segment: CaptionSegment) -> TranscriptResult:
        raise CaptionTranscriptionError(
            429,
            "OpenAI transcription has no available quota or is rate-limited (HTTP 429). Check API billing/credits and project limits.",
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
        assert "billing/credits" in engine.last_failure
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
    assert "OpenAI's transcription API" in runtime
    assert "OpenAI's transcription API" in ui
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
