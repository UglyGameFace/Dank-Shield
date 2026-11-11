"""End-to-end private local transcription/classification contracts without Discord audio."""
from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

from aiohttp import web

from stoney_verify.community_voice_captions import CaptionSegment
from stoney_verify import rival_voice_advisor, watch_voice_transcriber
from stoney_verify.watch_audio_wire import (
    response_body, response_signature, verify_audio_request,
)

SECRET = "watch-only-audio-token-with-ample-entropy-for-unit-tests"
VOICE_TOKEN = "standalone-voice-llm-token-with-sufficient-entropy-0001"
GUILD, USER, CHANNEL = 1517985071230095360, 1517985071230095361, 1517985071230095362
PCM_STEREO = b"\x00\x01\x00\x01" * 24_000
PCM_MONO = b"\x01\x02" * 8000


class TestWatchLocalVoice(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environ = patch.dict(os.environ, {
            "DANK_WATCH_AUDIO_SECRET": SECRET,
            "DANK_RIVAL_VOICE_TOKEN": VOICE_TOKEN,
        })
        self.environ.start()
        self.audio_requests = []
        self.classification_requests = []
        self.report = {
            "model": "LiquidAI/LFM3-1.7B-GGUF:Q4_K_M",
            "decision": "review", "category": "threat", "confidence": "high",
            "advisory_only": True, "calibrated": False,
            "enforcement_authorized": False,
        }

        async def audio(request):
            raw = await request.read()
            self.audio_requests.append(raw)
            _gid, uid, ch, nonce = verify_audio_request(
                SECRET, request.headers, raw,
            )
            self.assertEqual((uid, ch), (USER, CHANNEL))
            body = response_body("I am discussing my own identity.")
            return web.Response(
                body=body,
                headers={"X-WYM-Response-Signature": response_signature(
                    SECRET, nonce, raw, body,
                )},
            )

        async def classify(request):
            request_json = await request.json()
            self.classification_requests.append(request_json)
            if request.headers.get("Authorization") != "Bearer " + VOICE_TOKEN:
                return web.Response(status=401)
            return web.json_response(self.report)

        app = web.Application()
        app.router.add_post("/v1/voice-transcribe", audio)
        app.router.add_post("/v1/moderate", classify)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.patches = [
            patch.object(watch_voice_transcriber, "_URL", base + "/v1/voice-transcribe"),
            patch.object(watch_voice_transcriber, "pcm48_stereo_to_pcm16_mono",
                         return_value=PCM_MONO),
            patch.object(rival_voice_advisor, "_URL", base + "/v1/moderate"),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.runner.cleanup()
        self.environ.stop()

    async def test_private_audio_then_rival_advisory(self):
        transcriber = watch_voice_transcriber.WatchVoiceTranscriber(GUILD, CHANNEL)
        transcriber.allow_user(USER)
        segment = CaptionSegment(user_id=USER, pcm=PCM_STEREO,
                                 started_at=1.0, ended_at=1.5)
        transcript = await transcriber.transcribe(segment)
        self.assertEqual(transcript.text, "I am discussing my own identity.")
        self.assertEqual(transcript.provider, "watch-yo-mouf-local")
        self.assertIsNone(transcript.confidence)
        self.assertEqual(self.audio_requests, [PCM_MONO])
        result = await rival_voice_advisor.classify_local_voice(
            "A person is making a specific serious threat."
        )
        self.assertEqual(result.category, "threat")
        self.assertEqual(result.severity, "review")
        self.assertEqual(len(self.classification_requests), 1)
        self.assertEqual(set(self.classification_requests[0]),
                         {"version", "transcript", "context"})
        await transcriber.close()

    async def test_short_single_word_reaches_moderation_endpoint(self):
        # A lone word was dropped by the old 12-char floor before any request.
        result = await rival_voice_advisor.classify_local_voice("target")
        self.assertIsNotNone(result)
        self.assertEqual(result.category, "threat")
        self.assertEqual(len(self.classification_requests), 1)
        self.assertEqual(self.classification_requests[0]["transcript"], "target")
        # Two characters still never reach the endpoint.
        self.assertIsNone(await rival_voice_advisor.classify_local_voice("ok"))
        self.assertEqual(len(self.classification_requests), 1)

    async def test_revocation_during_conversion_never_forwards_audio(self):
        transcriber = watch_voice_transcriber.WatchVoiceTranscriber(GUILD, CHANNEL)
        transcriber.allow_user(USER)

        def slow_convert(pcm):
            time.sleep(0.15)
            return PCM_MONO

        with patch.object(watch_voice_transcriber,
                          "pcm48_stereo_to_pcm16_mono", side_effect=slow_convert):
            task = asyncio.create_task(transcriber.transcribe(
                CaptionSegment(USER, PCM_STEREO, 1.0, 1.5)
            ))
            await asyncio.sleep(0.02)
            await transcriber.close_user(USER)
            result = await task
        self.assertEqual(result.text, "")
        self.assertEqual(self.audio_requests, [])
        transcriber.allow_user(USER)
        self.assertEqual((await transcriber.transcribe(
            CaptionSegment(USER, PCM_STEREO, 1.0, 1.5)
        )).text, "I am discussing my own identity.")

    async def test_bad_signed_reply_fails_closed(self):
        async def forged(request):
            await request.read()
            return web.Response(
                body=response_body("false claim"),
                headers={"X-WYM-Response-Signature": "0" * 64},
            )
        # A new route on the same server with the wrong signature is rejected.
        app = web.Application()
        app.router.add_post("/bad", forged)
        other = web.AppRunner(app)
        await other.setup()
        site = web.TCPSite(other, "127.0.0.1", 0)
        await site.start()
        try:
            base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            with patch.object(watch_voice_transcriber, "_URL", base + "/bad"):
                client = watch_voice_transcriber.WatchVoiceTranscriber(GUILD, CHANNEL)
                client.allow_user(USER)
                result = await client.transcribe(CaptionSegment(USER, PCM_STEREO, 1.0, 1.5))
                self.assertEqual(result.text, "")
        finally:
            await other.cleanup()

    async def test_non_high_or_unsupported_model_output_does_not_accuse(self):
        for change in (
            {"confidence": "medium"},
            {"decision": "allow", "category": "none"},
            {"category": "unclear"},
            {"enforcement_authorized": True},
            {"model": "unknown-provider"},
        ):
            self.report = {
                "model": "LiquidAI/LFM3-1.7B-GGUF:Q4_K_M",
                "decision": "review", "category": "threat", "confidence": "high",
                "advisory_only": True, "calibrated": False,
                "enforcement_authorized": False,
                **change,
            }
            with self.subTest(change=change):
                result = await rival_voice_advisor.classify_local_voice(
                    "Specific transcript for contextual staff review."
                )
                self.assertIsNone(result)

    async def test_missing_secrets_do_not_invoke_network(self):
        os.environ["DANK_RIVAL_VOICE_TOKEN"] = ""
        os.environ["DANK_WATCH_AUDIO_SECRET"] = ""
        self.assertIsNone(await rival_voice_advisor.classify_local_voice("speech text"))
        client = watch_voice_transcriber.WatchVoiceTranscriber(GUILD, CHANNEL)
        client.allow_user(USER)
        result = await client.transcribe(CaptionSegment(USER, PCM_STEREO, 1.0, 1.5))
        self.assertEqual(result.text, "")
        self.assertEqual(self.audio_requests, [])
        self.assertEqual(self.classification_requests, [])


if __name__ == "__main__":
    unittest.main()
