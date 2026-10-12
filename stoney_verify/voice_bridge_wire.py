"""Private Discloud VLAN bridge handshake (version 1).

Same wire contract is intentionally vendored into the two separate bot deployments.
No voice, member IDs, transcripts or policy mutation flows through this handshake.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

PATH = "/v1/status"
MAX_SKEW = 30
VERSION = 1


class BridgeRejected(ValueError):
    """No authenticated or timely bridge handshake."""


def secret_bytes(value: str) -> bytes:
    if not isinstance(value, str) or len(value.encode("utf-8")) < 32 or len(value) > 512 or value != value.strip():
        raise BridgeRejected("missing or weak bridge credential")
    return value.encode("utf-8")


def request_headers(secret: str, *, now: int | None = None, nonce: str | None = None) -> dict[str, str]:
    at = int(time.time()) if now is None else now
    proof = nonce if nonce is not None else secrets.token_hex(16)
    if type(at) is not int or at <= 0 or type(proof) is not str or len(proof) != 32 or any(
        c not in "0123456789abcdef" for c in proof
    ):
        raise BridgeRejected("invalid request metadata")
    message = "\n".join(("wym-bridge-v1", "GET", PATH, str(at), proof)).encode("ascii")
    signature = hmac.new(secret_bytes(secret), message, hashlib.sha256).hexdigest()
    return {"X-WYM-Time": str(at), "X-WYM-Nonce": proof, "X-WYM-Signature": signature}


def verify_request(headers: Any, secret: str, *, now: int | None = None) -> str:
    at = int(time.time()) if now is None else now
    t, nonce, signature = (
        headers.get("X-WYM-Time", ""),
        headers.get("X-WYM-Nonce", ""),
        headers.get("X-WYM-Signature", ""),
    )
    if type(t) is not str or not t.isascii() or not t.isdigit() or len(t) > 15:
        raise BridgeRejected("invalid request time")
    issued = int(t)
    if abs(at - issued) > MAX_SKEW:
        raise BridgeRejected("stale request")
    if type(nonce) is not str or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise BridgeRejected("invalid request nonce")
    if type(signature) is not str or len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
        raise BridgeRejected("invalid signature")
    expected = request_headers(secret, now=issued, nonce=nonce)["X-WYM-Signature"]
    if not hmac.compare_digest(expected, signature):
        raise BridgeRejected("invalid signature")
    return nonce


def response_body() -> bytes:
    return json.dumps(
        {
            "version": VERSION,
            "bridge": "reachable",
            "policy_authority": "dank_shield",
            "receiver_owner": "dank_shield",
            "voice_capture_authorized": False,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def response_signature(secret: str, nonce: str, body: bytes) -> str:
    if not isinstance(body, bytes) or len(body) > 1024:
        raise BridgeRejected("invalid response body")
    if type(nonce) is not str or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise BridgeRejected("invalid response nonce")
    signed = b"\n".join((b"wym-bridge-v1", b"response", nonce.encode("ascii"), body))
    return hmac.new(secret_bytes(secret), signed, hashlib.sha256).hexdigest()


def verify_response(secret: str, nonce: str, body: bytes, signature: str) -> bool:
    if type(signature) is not str or len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
        return False
    try:
        if not hmac.compare_digest(response_signature(secret, nonce, body), signature):
            return False
        payload = json.loads(body)
    except (ValueError, TypeError, UnicodeError):
        return False
    return (
        type(payload) is dict
        and set(payload) == {"version", "bridge", "policy_authority", "receiver_owner", "voice_capture_authorized"}
        and type(payload["version"]) is int and payload["version"] == VERSION
        and payload["bridge"] == "reachable"
        and payload["policy_authority"] == "dank_shield"
        and payload["receiver_owner"] == "dank_shield"
        and payload["voice_capture_authorized"] is False
    )
