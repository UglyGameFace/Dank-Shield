from __future__ import annotations

"""Signed Discord-linked identity for the general Dank Cinema website.

This intentionally mirrors the existing signed Watch-link trust model. It does
not claim to be Discord OAuth. Links are short-lived bearer credentials issued
from Discord interactions and are scoped to one guild + Discord user.
"""

import hashlib
import hmac
import os
import time
from typing import Optional
from urllib.parse import urlencode


def _secret() -> str:
    return str(os.getenv("DANK_TORRENT_STREAM_SECRET", "") or "").strip()


def _public_base() -> str:
    return str(os.getenv("DANK_MEDIA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")


def _signature(guild_id: int, user_id: int, expires: int) -> str:
    payload = f"dank-cinema-site:{int(guild_id)}:{int(user_id)}:{int(expires)}".encode(
        "utf-8"
    )
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


__all__ = ["cinema_site_url", "validate_cinema_site_access"]
