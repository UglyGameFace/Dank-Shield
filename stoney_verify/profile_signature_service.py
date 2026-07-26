from __future__ import annotations

"""Service-role-only storage for member-owned profile signature appearance."""

import asyncio
import time
from typing import Any, Mapping

from .globals import get_supabase, reset_supabase
from .profile_card_service import PROFILE_USER_TABLE, ProfileStorageUnavailable, utc_now_iso
from .profile_signature_style import (
    apply_profile_appearance_updates,
    normalize_profile_appearance,
    reset_profile_appearance,
)

_DB_ATTEMPTS = 3
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX = 5000
_LOCKS: dict[int, asyncio.Lock] = {}
_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}


def _is_retryable(error: Exception) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "temporarily unavailable",
            "server disconnected",
            "remoteprotocolerror",
            "broken pipe",
            "eof",
        )
    )


def _execute_sync(label: str, operation: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, _DB_ATTEMPTS + 1):
        try:
            client = get_supabase()
            if client is None:
                raise ProfileStorageUnavailable("Supabase service-role storage is unavailable.")
            return operation(client)
        except ProfileStorageUnavailable:
            raise
        except Exception as exc:
            last_error = exc
            if _is_retryable(exc) and attempt < _DB_ATTEMPTS:
                reset_supabase()
                time.sleep(0.15 * attempt)
                continue
            break
    raise ProfileStorageUnavailable(
        f"{label} failed safely: {type(last_error).__name__ if last_error else 'unknown error'}"
    )


async def _execute(label: str, operation: Any) -> Any:
    return await asyncio.to_thread(_execute_sync, label, operation)


def invalidate_profile_signature_appearance(user_id: int | None = None) -> None:
    if user_id is None:
        _CACHE.clear()
        return
    _CACHE.pop(int(user_id), None)


def _cache_get(user_id: int) -> dict[str, Any] | None:
    found = _CACHE.get(int(user_id))
    if not found:
        return None
    timestamp, payload = found
    if time.monotonic() - timestamp > _CACHE_TTL_SECONDS:
        _CACHE.pop(int(user_id), None)
        return None
    return dict(payload)


def _cache_put(user_id: int, appearance: Mapping[str, Any]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = sorted(_CACHE.items(), key=lambda item: item[1][0])[:1000]
        for key, _value in oldest:
            _CACHE.pop(key, None)
    _CACHE[int(user_id)] = (time.monotonic(), dict(appearance))


async def get_profile_signature_appearance(
    user_id: int,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    uid = int(user_id)
    if not refresh:
        cached = _cache_get(uid)
        if cached is not None:
            return cached

    def read(client: Any):
        return (
            client.table(PROFILE_USER_TABLE)
            .select("appearance")
            .eq("user_id", str(uid))
            .limit(1)
            .execute()
        )

    response = await _execute(f"read profile signature appearance {uid}", read)
    rows = getattr(response, "data", None) or []
    raw = rows[0].get("appearance") if rows and isinstance(rows[0], Mapping) else {}
    normalized = normalize_profile_appearance(raw if isinstance(raw, Mapping) else {})
    _cache_put(uid, normalized)
    return dict(normalized)


async def upsert_profile_signature_appearance(
    user_id: int,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    uid = int(user_id)
    lock = _LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        current = await get_profile_signature_appearance(uid, refresh=True)
        appearance = apply_profile_appearance_updates(current, updates)
        payload = {
            "user_id": str(uid),
            "appearance": appearance,
            "updated_at": utc_now_iso(),
        }

        def write(client: Any):
            try:
                return (
                    client.table(PROFILE_USER_TABLE)
                    .upsert(payload, on_conflict="user_id")
                    .execute()
                )
            except TypeError:
                return client.table(PROFILE_USER_TABLE).upsert(payload).execute()

        await _execute(f"write profile signature appearance {uid}", write)
        invalidate_profile_signature_appearance(uid)
        return await get_profile_signature_appearance(uid, refresh=True)


async def reset_profile_signature_appearance(user_id: int) -> dict[str, Any]:
    return await upsert_profile_signature_appearance(
        int(user_id),
        reset_profile_appearance(),
    )


__all__ = [
    "get_profile_signature_appearance",
    "invalidate_profile_signature_appearance",
    "reset_profile_signature_appearance",
    "upsert_profile_signature_appearance",
]
