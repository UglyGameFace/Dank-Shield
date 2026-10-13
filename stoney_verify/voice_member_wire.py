"""Versioned, body-bound member eligibility check on the existing private VLAN.

Vendored identically by both bots. This is a READ-ONLY consent/config probe,
never a lease, revocation stream, or permission to capture speech.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

CHECK_PATH = "/v1/member-eligibility"
MAX_CHECK_BYTES = 256
MAX_REPLY_BYTES = 256
MAX_SKEW_SECONDS = 30
VERSION = 1
_SNOWFLAKE_MAX = (1 << 64) - 1
_FIELDS = {"version", "guild_id", "member_id", "channel_id"}
_REPLY_FIELDS = {"version", "consent_current", "policy_matches", "receiver_owner", "voice_capture_authorized"}


class MemberCheckRejected(ValueError):
    """Bad authentication, unexpected payload, or invalid member check."""


def _key(secret: str) -> bytes:
    if not isinstance(secret, str) or secret != secret.strip():
        raise MemberCheckRejected("invalid bridge secret")
    raw = secret.encode("utf-8")
    if not 32 <= len(raw) <= 512:
        raise MemberCheckRejected("invalid bridge secret")
    return raw


def _id(value: Any) -> int:
    if type(value) is int:
        value = str(value)
    if (type(value) is not str or not value.isascii() or not value.isdecimal()
            or value.startswith("0") or len(value) > 20):
        raise MemberCheckRejected("invalid Discord snowflake")
    number = int(value)
    if not 0 < number <= _SNOWFLAKE_MAX:
        raise MemberCheckRejected("invalid Discord snowflake")
    return number


def _nonce(value: Any) -> str:
    if type(value) is not str or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise MemberCheckRejected("invalid nonce")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MemberCheckRejected("duplicate JSON key")
        result[key] = value
    return result


def _canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _object(raw: bytes, size: int) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= size:
        raise MemberCheckRejected("invalid JSON size")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise MemberCheckRejected("invalid JSON") from exc
    if type(data) is not dict:
        raise MemberCheckRejected("invalid JSON object")
    return data


def check_request_body(guild_id: int, member_id: int, channel_id: int) -> bytes:
    data = {"version": VERSION, "guild_id": str(_id(guild_id)),
            "member_id": str(_id(member_id)), "channel_id": str(_id(channel_id))}
    return _canonical(data)


def _parse_request(body: bytes) -> tuple[int, int, int]:
    data = _object(body, MAX_CHECK_BYTES)
    if set(data) != _FIELDS or type(data["version"]) is not int or data["version"] != VERSION:
        raise MemberCheckRejected("invalid request schema")
    return _id(data["guild_id"]), _id(data["member_id"]), _id(data["channel_id"])


def _time(headers: Any, now: int) -> tuple[int, str, str]:
    t, nonce, signature = (headers.get("X-WYM-Time", ""),
                            headers.get("X-WYM-Nonce", ""),
                            headers.get("X-WYM-Signature", ""))
    if type(t) is not str or not t.isascii() or not t.isdecimal() or len(t) > 15:
        raise MemberCheckRejected("invalid timestamp")
    issued = int(t)
    if issued <= 0 or abs(now - issued) > MAX_SKEW_SECONDS:
        raise MemberCheckRejected("expired request")
    _nonce(nonce)
    if type(signature) is not str or len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
        raise MemberCheckRejected("invalid signature")
    return issued, nonce, signature


def _request_signature(secret: str, issued: int, nonce: str, body: bytes) -> str:
    content_hash = hashlib.sha256(body).hexdigest()
    signed = ("wym-member-check-v1\nPOST\n" + CHECK_PATH + "\n" + str(issued)
              + "\n" + nonce + "\n" + content_hash).encode("ascii")
    return hmac.new(_key(secret), signed, hashlib.sha256).hexdigest()


def sign_check_request(secret: str, body: bytes, *, now: int | None = None,
                       nonce: str | None = None) -> dict[str, str]:
    _parse_request(body)
    issued = int(time.time()) if now is None else now
    if type(issued) is not int or issued <= 0:
        raise MemberCheckRejected("invalid timestamp")
    proof = _nonce(nonce if nonce is not None else secrets.token_hex(16))
    return {"X-WYM-Time": str(issued), "X-WYM-Nonce": proof,
            "X-WYM-Signature": _request_signature(secret, issued, proof, body)}


def verify_check_request(headers: Any, secret: str, body: bytes, *,
                         now: int | None = None) -> tuple[int, int, int, str]:
    at = int(time.time()) if now is None else now
    issued, nonce, signature = _time(headers, at)
    expected = _request_signature(secret, issued, nonce, body)
    if not hmac.compare_digest(expected, signature):
        raise MemberCheckRejected("invalid signature")
    return (*_parse_request(body), nonce)


def check_response_body(*, consent_current: bool, policy_matches: bool) -> bytes:
    if type(consent_current) is not bool or type(policy_matches) is not bool:
        raise MemberCheckRejected("invalid eligibility flags")
    return _canonical({"version": VERSION, "consent_current": consent_current,
                       "policy_matches": policy_matches, "receiver_owner": "dank_shield",
                       "voice_capture_authorized": False})


def _reply_signature(secret: str, nonce: str, request_body: bytes, response: bytes) -> str:
    _nonce(nonce)
    _parse_request(request_body)
    if type(response) is not bytes or len(response) > MAX_REPLY_BYTES:
        raise MemberCheckRejected("invalid reply")
    signed = (b"wym-member-check-v1\nresponse\n" + nonce.encode("ascii") + b"\n"
              + hashlib.sha256(request_body).hexdigest().encode("ascii") + b"\n" + response)
    return hmac.new(_key(secret), signed, hashlib.sha256).hexdigest()


def sign_check_response(secret: str, nonce: str, request_body: bytes, response: bytes) -> str:
    return _reply_signature(secret, nonce, request_body, response)


def verify_check_response(secret: str, nonce: str, request_body: bytes,
                          response: bytes, signature: str) -> dict[str, bool]:
    if type(signature) is not str or len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
        raise MemberCheckRejected("invalid reply signature")
    expected = _reply_signature(secret, nonce, request_body, response)
    if not hmac.compare_digest(expected, signature):
        raise MemberCheckRejected("invalid reply signature")
    result = _object(response, MAX_REPLY_BYTES)
    if (set(result) != _REPLY_FIELDS or type(result["version"]) is not int
            or result["version"] != VERSION or result["receiver_owner"] != "dank_shield"
            or result["voice_capture_authorized"] is not False
            or type(result["consent_current"]) is not bool
            or type(result["policy_matches"]) is not bool):
        raise MemberCheckRejected("invalid reply authority or schema")
    return {"consent_current": result["consent_current"],
            "policy_matches": result["policy_matches"]}
