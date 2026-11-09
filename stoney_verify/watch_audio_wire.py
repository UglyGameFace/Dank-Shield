"""Body-bound private VLAN contract for consented 16 kHz mono PCM segments.

The only capture authority is Dank Shield's existing per-speaker DAVE receiver.
This wire format never establishes a second receiver or confers consent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

PATH = "/v1/voice-transcribe"
MAX_AUDIO_BYTES = 512_000
MAX_REPLY_BYTES = 2_048
MAX_CLOCK_SKEW = 30
MAX_SNOWFLAKE = (1 << 64) - 1


class AudioWireRejected(ValueError):
    """Audio transport was malformed, stale, replayed, or unauthenticated."""


def _secret(value: str) -> bytes:
    if type(value) is not str or value != value.strip():
        raise AudioWireRejected("missing private audio secret")
    data = value.encode("utf-8")
    if not 32 <= len(data) <= 512:
        raise AudioWireRejected("invalid private audio secret")
    return data


def _id(value: object) -> int:
    if type(value) is int:
        value = str(value)
    if (
        type(value) is not str or not value.isascii() or not value.isdecimal()
        or len(value) > 20 or value.startswith("0")
    ):
        raise AudioWireRejected("invalid speaker/guild/channel identifier")
    result = int(value)
    if not 0 < result <= MAX_SNOWFLAKE:
        raise AudioWireRejected("invalid identifier range")
    return result


def _nonce(value: object) -> str:
    if type(value) is not str or len(value) != 32 or any(
        letter not in "0123456789abcdef" for letter in value
    ):
        raise AudioWireRejected("invalid private audio nonce")
    return value


def _audio(data: object) -> bytes:
    if type(data) is not bytes or not 3_200 <= len(data) <= MAX_AUDIO_BYTES or len(data) % 2:
        raise AudioWireRejected("invalid bounded mono audio")
    return data


def _signature(secret: str, issued: int, nonce: str, gid: int, uid: int,
               channel: int, body: bytes) -> str:
    raw = (
        "wym-audio-v1\nPOST\n" + PATH + "\n" + str(issued) + "\n"
        + nonce + "\n" + str(gid) + "\n" + str(uid) + "\n"
        + str(channel) + "\n" + hashlib.sha256(body).hexdigest()
    ).encode("ascii")
    return hmac.new(_secret(secret), raw, hashlib.sha256).hexdigest()


def sign_audio_request(secret: str, body: bytes, guild_id: int,
                       user_id: int, channel_id: int, *,
                       now: int | None = None, nonce: str | None = None) -> dict[str, str]:
    _audio(body)
    issued = int(time.time()) if now is None else now
    if type(issued) is not int or issued <= 0:
        raise AudioWireRejected("invalid request time")
    challenge = _nonce(nonce if nonce is not None else secrets.token_hex(16))
    gid, uid, channel = map(_id, (guild_id, user_id, channel_id))
    return {
        "X-WYM-Time": str(issued), "X-WYM-Nonce": challenge,
        "X-WYM-Guild": str(gid), "X-WYM-Speaker": str(uid),
        "X-WYM-Channel": str(channel),
        "X-WYM-Signature": _signature(
            secret, issued, challenge, gid, uid, channel, body,
        ),
    }


def verify_audio_request(secret: str, headers: object, body: bytes, *,
                         now: int | None = None) -> tuple[int, int, int, str]:
    _audio(body)
    at = int(time.time()) if now is None else now
    get = getattr(headers, "get", None)
    if not callable(get):
        raise AudioWireRejected("invalid headers")
    stamp = get("X-WYM-Time", "")
    if type(stamp) is not str or not stamp.isascii() or not stamp.isdecimal() or len(stamp) > 15:
        raise AudioWireRejected("invalid time")
    issued = int(stamp)
    if issued <= 0 or abs(at - issued) > MAX_CLOCK_SKEW:
        raise AudioWireRejected("expired audio request")
    nonce = _nonce(get("X-WYM-Nonce", ""))
    gid, uid, channel = map(
        _id, (get("X-WYM-Guild", ""), get("X-WYM-Speaker", ""),
              get("X-WYM-Channel", "")),
    )
    received = get("X-WYM-Signature", "")
    if type(received) is not str or len(received) != 64:
        raise AudioWireRejected("invalid audio signature")
    expected = _signature(secret, issued, nonce, gid, uid, channel, body)
    if not hmac.compare_digest(expected, received):
        raise AudioWireRejected("invalid audio signature")
    return gid, uid, channel, nonce


def response_body(transcript: str) -> bytes:
    if (
        type(transcript) is not str or len(transcript) > 1_200
        or any(ord(char) < 32 and char not in "\n\t" for char in transcript)
    ):
        raise AudioWireRejected("invalid transcript output")
    return json.dumps(
        {"version": 1, "text": transcript.strip(), "language": "en-us",
         "model": "vosk-small-en-us-0.15"},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def response_signature(secret: str, nonce: str, request: bytes, response: bytes) -> str:
    _nonce(nonce)
    _audio(request)
    if type(response) is not bytes or not 1 <= len(response) <= MAX_REPLY_BYTES:
        raise AudioWireRejected("invalid audio reply")
    message = (b"wym-audio-response-v1\n" + nonce.encode("ascii") + b"\n"
               + hashlib.sha256(request).hexdigest().encode("ascii") + b"\n"
               + response)
    return hmac.new(_secret(secret), message, hashlib.sha256).hexdigest()


def verify_audio_response(secret: str, nonce: str, request: bytes,
                          reply: bytes, signature: str) -> str:
    if type(signature) is not str or len(signature) != 64:
        raise AudioWireRejected("invalid response signature")
    expected = response_signature(secret, nonce, request, reply)
    if not hmac.compare_digest(signature, expected):
        raise AudioWireRejected("invalid response signature")
    try:
        payload = json.loads(reply.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise AudioWireRejected("invalid reply JSON") from exc
    if (
        type(payload) is not dict
        or set(payload) != {"version", "text", "language", "model"}
        or type(payload["version"]) is not int or payload["version"] != 1
        or payload["language"] != "en-us"
        or payload["model"] != "vosk-small-en-us-0.15"
    ):
        raise AudioWireRejected("invalid reply schema")
    text = payload["text"]
    if type(text) is not str or not 0 <= len(text) <= 1200:
        raise AudioWireRejected("invalid reply text")
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise AudioWireRejected("invalid reply contents")
    return text
