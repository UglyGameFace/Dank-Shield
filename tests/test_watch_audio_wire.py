"""Signed PCM wire interoperability, privacy and request-freshness tests."""
import unittest
from stoney_verify.watch_audio_wire import (
    AudioWireRejected, MAX_AUDIO_BYTES, response_body, response_signature,
    sign_audio_request, verify_audio_request, verify_audio_response,
)

SECRET = "watch-only-audio-token-with-ample-entropy-for-unit-tests"
NOW = 1_800_000_000
GUILD, USER, CHANNEL = 1517985071230095360, 1517985071230095361, 1517985071230095362
AUDIO = (b"\x00\x01" * 5000)


class AudioWireTests(unittest.TestCase):
    def test_authenticated_audio_and_reply_are_bound_to_same_bytes_and_ids(self):
        headers = sign_audio_request(
            SECRET, AUDIO, GUILD, USER, CHANNEL, now=NOW, nonce="a" * 32
        )
        self.assertEqual(
            verify_audio_request(SECRET, headers, AUDIO, now=NOW),
            (GUILD, USER, CHANNEL, "a" * 32),
        )
        body = response_body("a harmless sentence")
        signature = response_signature(SECRET, "a" * 32, AUDIO, body)
        self.assertEqual(
            verify_audio_response(SECRET, "a" * 32, AUDIO, body, signature),
            "a harmless sentence",
        )
        for corrupted in (AUDIO[:-2] + b"\x02\x03", AUDIO + b"\0\0"):
            with self.assertRaises(AudioWireRejected):
                verify_audio_request(SECRET, headers, corrupted, now=NOW)
        with self.assertRaises(AudioWireRejected):
            verify_audio_response(SECRET, "a" * 32, AUDIO, body + b" ", signature)

    def test_tamper_wrong_key_speaker_and_stale_time_fail_closed(self):
        h = sign_audio_request(SECRET, AUDIO, GUILD, USER, CHANNEL, now=NOW, nonce="c" * 32)
        for headers, token, date in (
            ({**h, "X-WYM-Speaker": str(USER + 1)}, SECRET, NOW),
            (h, "different-private-secret-long-enough-1", NOW),
            (h, SECRET, NOW + 31),
            (h, SECRET, NOW - 31),
            ({**h, "X-WYM-Nonce": "x" * 32}, SECRET, NOW),
        ):
            with self.subTest(headers=headers, date=date):
                with self.assertRaises(AudioWireRejected):
                    verify_audio_request(token, headers, AUDIO, now=date)

    def test_refuse_oversized_odd_bytes_and_untrusted_keys(self):
        for audio in (b"", b"a", b"b" * 3201, b"x" * (MAX_AUDIO_BYTES + 2)):
            with self.subTest(length=len(audio)):
                with self.assertRaises(AudioWireRejected):
                    sign_audio_request(SECRET, audio, GUILD, USER, CHANNEL, now=NOW)
        with self.assertRaises(AudioWireRejected):
            sign_audio_request("weak", AUDIO, GUILD, USER, CHANNEL, now=NOW)
        with self.assertRaises(AudioWireRejected):
            response_body("x" * 1201)
        with self.assertRaises(AudioWireRejected):
            response_body("hello" + chr(0))


if __name__ == "__main__":
    unittest.main()
