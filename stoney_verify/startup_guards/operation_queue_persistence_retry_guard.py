from __future__ import annotations

"""Retry operation queue persistence after Supabase/PostgREST schema-cache lag.

After a migration, Supabase SQL can succeed while PostgREST still returns
PGRST205 for a short period. The base queue degrades to memory-only on first
failure; this guard makes that specific failure retryable instead of permanent.
"""

import asyncio
import time
from typing import Any

_DONE = False
_RETRY_SECONDS = 90.0


def _looks_like_schema_cache_lag(reason: Any) -> bool:
    text = str(reason or "").lower()
    return "pgrst205" in text or "schema cache" in text or "bot_operation_jobs" in text and "could not find" in text


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        import stoney_verify.operation_queue as oq

        cls = getattr(oq, "_OperationPersistence", None)
        if cls is None or getattr(cls, "_schema_cache_retry_wrapped", False):
            return False

        original_disable = cls._disable
        original_upsert = cls.upsert_job
        original_update = cls.update_job

        def _disable(self, reason: str) -> None:
            original_disable(self, reason)
            if _looks_like_schema_cache_lag(reason):
                try:
                    self._retry_after_monotonic = time.monotonic() + _RETRY_SECONDS
                    print(
                        "🧱 operation_queue persistence retry scheduled "
                        f"after_schema_cache_lag_seconds={int(_RETRY_SECONDS)}"
                    )
                except Exception:
                    pass

        def _maybe_reenable(self) -> bool:
            try:
                retry_after = float(getattr(self, "_retry_after_monotonic", 0.0) or 0.0)
                if retry_after and time.monotonic() >= retry_after and _looks_like_schema_cache_lag(getattr(self, "_disabled_reason", "")):
                    self._disabled_reason = ""
                    self._warned = False
                    self._retry_after_monotonic = 0.0
                    print("🧱 operation_queue persistence retrying after Supabase schema-cache reload window")
                    return True
            except Exception:
                pass
            return not bool(getattr(self, "_disabled_reason", ""))

        async def upsert_job(self, job):
            if not _maybe_reenable(self):
                return
            return await original_upsert(self, job)

        async def update_job(self, job):
            if not _maybe_reenable(self):
                return
            return await original_update(self, job)

        cls._disable = _disable
        cls.upsert_job = upsert_job
        cls.update_job = update_job
        setattr(cls, "_schema_cache_retry_wrapped", True)
        _DONE = True
        print("🧱 operation_queue_persistence_retry_guard active; PGRST205 schema-cache misses are retryable")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ operation_queue_persistence_retry_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
