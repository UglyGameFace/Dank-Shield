from __future__ import annotations

"""Signed Discord-linked identity for the Dank Cinema website.

Discord interactions can issue a short-lived signed deep link. A validated deep
link or Discord OAuth login is then exchanged for an HttpOnly browser session so
the full Cinema site does not need to keep bearer credentials in every API URL.
"""

import hashlib
import hmac
import os
import time
from typing import Optional
from urllib.parse import urlencode

CINEMA_SESSION_COOKIE = "dank_cinema_session"
CINEMA_IDENTITY_COOKIE = "dank_cinema_identity"
CINEMA_GUILDS_COOKIE = "dank_cinema_guilds"
CINEMA_OAUTH_STATE_COOKIE = "dank_cinema_oauth_state"
CINEMA_SESSION_TTL_SECONDS = 6 * 60 * 60
CINEMA_IDENTITY_TTL_SECONDS = 7 * 24 * 60 * 60
CINEMA_GUILDS_TTL_SECONDS = 15 * 60


def _secret() -> str:
    return str(os.getenv("DANK_TORRENT_STREAM_SECRET", "") or "").strip()


def _public_base() -> str:
    return str(os.getenv("DANK_MEDIA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")


def cinema_public_base() -> str:
    return _public_base()


def _signature(guild_id: int, user_id: int, expires: int) -> str:
    payload = f"dank-cinema-site:{int(guild_id)}:{int(user_id)}:{int(expires)}".encode(
        "utf-8"
    )
    return hmac.new(_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _session_signature(guild_id: int, user_id: int, expires: int) -> str:
    payload = (
        f"dank-cinema-session:{int(guild_id)}:{int(user_id)}:{int(expires)}"
    ).encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _identity_signature(user_id: int, expires: int) -> str:
    payload = f"dank-cinema-identity:{int(user_id)}:{int(expires)}".encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _guilds_signature(user_id: int, expires: int, guild_ids: str) -> str:
    payload = (
        f"dank-cinema-guilds:{int(user_id)}:{int(expires)}:{guild_ids}"
    ).encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def cinema_site_url(
    guild_id: int,
    user_id: int,
    *,
    ttl_seconds: int = 6 * 60 * 60,
) -> str:
    base = _public_base()
    secret = _secret()
    gid = int(guild_id)
    uid = int(user_id)
    if not base or not secret or gid <= 0 or uid <= 0:
        return ""
    expires = int(time.time()) + max(300, min(int(ttl_seconds), 21600))
    query = urlencode(
        {
            "uid": uid,
            "exp": expires,
            "sig": _signature(gid, uid, expires),
        }
    )
    return f"{base}/cinema/{gid}?{query}"


def validate_cinema_site_access(
    guild_id: int,
    user_id: str,
    expires: str,
    signature: str,
) -> Optional[int]:
    if not _secret():
        return None
    try:
        gid = int(guild_id)
        uid = int(str(user_id or "").strip())
        exp = int(str(expires or "").strip())
    except Exception:
        return None
    now = int(time.time())
    if gid <= 0 or uid <= 0 or exp < now or exp > now + 21660:
        return None
    expected = _signature(gid, uid, exp)
    if not signature or not hmac.compare_digest(expected, str(signature)):
        return None
    return uid


def cinema_identity_value(
    user_id: int,
    *,
    ttl_seconds: int = CINEMA_IDENTITY_TTL_SECONDS,
) -> str:
    secret = _secret()
    uid = int(user_id)
    if not secret or uid <= 0:
        return ""
    ttl = max(300, min(int(ttl_seconds), CINEMA_SESSION_TTL_SECONDS))
    expires = int(time.time()) + ttl
    signature = _identity_signature(uid, expires)
    return f"{uid}.{expires}.{signature}"


def validate_cinema_identity(value: str) -> Optional[int]:
    if not _secret():
        return None
    parts = str(value or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        uid = int(parts[0])
        expires = int(parts[1])
    except Exception:
        return None
    now = int(time.time())
    if uid <= 0 or expires < now or expires > now + 30 * 24 * 60 * 60 + 60:
        return None
    expected = _identity_signature(uid, expires)
    if not hmac.compare_digest(expected, parts[2]):
        return None
    return uid


def cinema_guilds_value(
    user_id: int,
    guild_ids: list[int] | tuple[int, ...],
    *,
    ttl_seconds: int = CINEMA_GUILDS_TTL_SECONDS,
) -> str:
    secret = _secret()
    uid = int(user_id)
    normalized = sorted({int(value) for value in guild_ids if int(value) > 0})[:50]
    if not secret or uid <= 0 or not normalized:
        return ""
    ttl = max(60, min(int(ttl_seconds), 3600))
    expires = int(time.time()) + ttl
    encoded = ",".join(str(value) for value in normalized)
    signature = _guilds_signature(uid, expires, encoded)
    return f"{uid}.{expires}.{encoded}.{signature}"


def validate_cinema_guilds(
    user_id: int,
    value: str,
) -> tuple[int, ...]:
    if not _secret():
        return ()
    parts = str(value or "").strip().split(".", 3)
    if len(parts) != 4:
        return ()
    try:
        cookie_uid = int(parts[0])
        expires = int(parts[1])
        expected_uid = int(user_id)
    except Exception:
        return ()
    now = int(time.time())
    encoded = parts[2]
    if (
        expected_uid <= 0
        or cookie_uid != expected_uid
        or expires < now
        or expires > now + 3660
    ):
        return ()
    expected = _guilds_signature(cookie_uid, expires, encoded)
    if not hmac.compare_digest(expected, parts[3]):
        return ()
    output: list[int] = []
    for raw in encoded.split(","):
        try:
            guild_id = int(raw)
        except Exception:
            continue
        if guild_id > 0 and guild_id not in output:
            output.append(guild_id)
    return tuple(output[:50])


def cinema_session_value(
    guild_id: int,
    user_id: int,
    *,
    ttl_seconds: int = CINEMA_SESSION_TTL_SECONDS,
) -> str:
    secret = _secret()
    gid = int(guild_id)
    uid = int(user_id)
    if not secret or gid <= 0 or uid <= 0:
        return ""
    ttl = max(3600, min(int(ttl_seconds), 30 * 24 * 60 * 60))
    expires = int(time.time()) + ttl
    signature = _session_signature(gid, uid, expires)
    return f"{gid}.{uid}.{expires}.{signature}"


def validate_cinema_session(
    guild_id: int,
    value: str,
) -> Optional[int]:
    if not _secret():
        return None
    parts = str(value or "").strip().split(".")
    if len(parts) != 4:
        return None
    try:
        cookie_gid = int(parts[0])
        uid = int(parts[1])
        expires = int(parts[2])
        expected_gid = int(guild_id)
    except Exception:
        return None
    now = int(time.time())
    if (
        expected_gid <= 0
        or cookie_gid != expected_gid
        or uid <= 0
        or expires < now
        or expires > now + CINEMA_SESSION_TTL_SECONDS + 60
    ):
        return None
    expected = _session_signature(cookie_gid, uid, expires)
    if not hmac.compare_digest(expected, parts[3]):
        return None
    return uid


__all__ = [
    "CINEMA_GUILDS_COOKIE",
    "CINEMA_GUILDS_TTL_SECONDS",
    "CINEMA_IDENTITY_COOKIE",
    "CINEMA_IDENTITY_TTL_SECONDS",
    "CINEMA_OAUTH_STATE_COOKIE",
    "CINEMA_SESSION_COOKIE",
    "CINEMA_SESSION_TTL_SECONDS",
    "cinema_guilds_value",
    "cinema_identity_value",
    "cinema_public_base",
    "cinema_session_value",
    "cinema_site_url",
    "validate_cinema_guilds",
    "validate_cinema_identity",
    "validate_cinema_session",
    "validate_cinema_site_access",
]
