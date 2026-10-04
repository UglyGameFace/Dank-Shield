from __future__ import annotations

"""Shared service-role storage helpers for Dank Cinema persistence."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from .globals import get_supabase, reset_supabase

_DB_ATTEMPTS = 3


class CinemaStorageUnavailable(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rows(response: Any) -> list[dict[str, Any]]:
    raw = getattr(response, "data", None) or []
    return [dict(row) for row in raw if isinstance(row, Mapping)]


def _is_retryable(exc: Exception) -> bool:
    text = repr(exc).casefold()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "temporarily unavailable",
            "remoteprotocolerror",
            "broken pipe",
            "eof",
        )
    )


def _execute_sync(label: str, operation: Callable[[Any], Any]) -> Any:
    last: Optional[Exception] = None
    for attempt in range(1, _DB_ATTEMPTS + 1):
        try:
            client = get_supabase()
            if client is None:
                raise CinemaStorageUnavailable(
                    "Dank Cinema storage is unavailable."
                )
            return operation(client)
        except CinemaStorageUnavailable:
            raise
        except Exception as exc:
            last = exc
            if _is_retryable(exc) and attempt < _DB_ATTEMPTS:
                reset_supabase()
                time.sleep(0.12 * attempt)
                continue
            break
    raise CinemaStorageUnavailable(
        f"{label} failed safely: {type(last).__name__ if last else 'unknown error'}"
    )


async def execute(label: str, operation: Callable[[Any], Any]) -> Any:
    return await asyncio.to_thread(_execute_sync, label, operation)


__all__ = ["CinemaStorageUnavailable", "execute", "rows", "utc_now"]
