from __future__ import annotations

"""Non-blocking token-store safety layer.

Problem fixed:
- Discord component handlers are async.
- Some older paths still call the synchronous Supabase token helpers directly
  from the event-loop thread.
- When Supabase/httpx waits on a sync HTTP/2 lock, the Discord heartbeat blocks
  and the gateway starts warning/reconnecting.

This guard makes the imported token helpers event-loop safe without requiring a
large rewrite of every old call site:
- On the event loop, reads return in-memory/cache/VC-request data immediately.
- Slow Supabase reads are scheduled in a tiny background executor.
- Writes update memory immediately and persist in the executor.
- Outside the event loop, the original sync behavior is preserved.

This is intentionally conservative: it never performs blocking network I/O on
Discord's loop thread.
"""

import asyncio
import concurrent.futures
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

_PATCHED = False
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="stoney-store")
_PENDING: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"✅ nonblocking_store_runtime: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ nonblocking_store_runtime: {message}")
    except Exception:
        pass


def _in_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
    except Exception:
        return False


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _utc_iso(minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=int(minutes or 0))).isoformat()


def _normalize(store: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        fn = getattr(store, "_normalize_row", None)
        if callable(fn):
            return dict(fn(dict(row)))
    except Exception:
        pass
    return dict(row)


def _mem(store: Any) -> Dict[str, Dict[str, Any]]:
    try:
        mem = getattr(store, "_MEM_TOKENS", None)
        if isinstance(mem, dict):
            return mem
    except Exception:
        pass
    return {}


def _cached_row(store: Any, token: str) -> Optional[Dict[str, Any]]:
    try:
        row = _mem(store).get(str(token))
        if isinstance(row, dict):
            return _normalize(store, row)
    except Exception:
        pass
    return None


def _set_cache(store: Any, token: str, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        norm = _normalize(store, dict(row, token=str(token)))
        _mem(store)[str(token)] = norm
        return norm
    except Exception:
        return None


def _vc_request_row(token: str) -> Optional[Dict[str, Any]]:
    try:
        from stoney_verify.commands_ext.common import VC_REQUESTS
    except Exception:
        VC_REQUESTS = {}

    try:
        req = VC_REQUESTS.get(str(token)) or {}
        if not isinstance(req, dict) or not req:
            return None
    except Exception:
        return None

    guild_id = _safe_int(
        req.get("guild_id")
        or req.get("server_id")
        or 0,
        0,
    )
    channel_id = _safe_int(
        req.get("channel_id")
        or req.get("ticket_channel_id")
        or req.get("ticket_id")
        or 0,
        0,
    )
    requester_id = _safe_int(
        req.get("requester_id")
        or req.get("requested_by")
        or req.get("owner_id")
        or req.get("user_id")
        or 0,
        0,
    )

    # A VC staff action can work from the VC request cache even when the DB is
    # slow. Keep the row shaped like verification_tokens.
    return {
        "token": str(token),
        "guild_id": str(guild_id) if guild_id else None,
        "channel_id": str(channel_id) if channel_id else None,
        "requester_id": str(requester_id) if requester_id else None,
        "user_id": str(requester_id) if requester_id else None,
        "expires_at": _safe_str(req.get("expires_at")) or _utc_iso(240),
        "used": False,
        "submitted": False,
        "submitted_at": None,
        "decision": _safe_str(req.get("decision")) or "PENDING",
        "decided_by": None,
        "decided_at": None,
        "created_at": _safe_str(req.get("created_at")) or _safe_str(req.get("requested_at")) or _utc_iso(0),
        "webhook_url": _safe_str(req.get("webhook_url")) or (f"bot://channel/{channel_id}" if channel_id else "bot://channel/0"),
        "approved_user_id": None,
        "ai_status": None,
        "identity_fingerprint": None,
        "fingerprint_version": "v1",
        "identity_source": "voice_verification",
        "verification_source": "voice_verification",
        "proof_captured_at": None,
        "submission_meta": {"source": "vc_request_cache", "status": _safe_str(req.get("status"))},
    }


def _schedule(key: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Run slow sync work off-loop, coalescing repeated jobs."""
    try:
        job_key = str(key or "job")[:240]
        if job_key in _PENDING:
            return
        _PENDING.add(job_key)

        def _runner() -> None:
            try:
                fn(*args, **kwargs)
            except Exception as e:
                _warn(f"background store job failed key={job_key}: {e!r}")
            finally:
                try:
                    _PENDING.discard(job_key)
                except Exception:
                    pass

        _EXECUTOR.submit(_runner)
    except Exception as e:
        _warn(f"could not schedule background store job key={key}: {e!r}")


def _make_get_token_info(store: Any, original: Callable[..., Any]) -> Callable[[str], Optional[Dict[str, Any]]]:
    def sb_get_token_info_nonblocking(token: str) -> Optional[Dict[str, Any]]:
        tok = _safe_str(token)
        if not tok:
            return None

        cached = _cached_row(store, tok)
        if cached:
            return cached

        vc_row = _vc_request_row(tok)
        if vc_row:
            return _set_cache(store, tok, vc_row) or vc_row

        if not _in_event_loop():
            try:
                return original(tok)
            except Exception:
                return _cached_row(store, tok)

        # Never block the Discord heartbeat. Hydrate cache in the background
        # for the next click/message and fail fast this time.
        def _fetch_and_cache() -> None:
            try:
                row = original(tok)
                if isinstance(row, dict):
                    _set_cache(store, tok, row)
            except Exception:
                pass

        _schedule(f"get:{tok}", _fetch_and_cache)
        return None

    return sb_get_token_info_nonblocking


def _make_insert_token(store: Any, original: Callable[..., Any]) -> Callable[..., bool]:
    def sb_insert_token_nonblocking(token: str, *args: Any, **kwargs: Any) -> bool:
        tok = _safe_str(token)
        if not tok:
            return False

        try:
            row = {
                "token": tok,
                "guild_id": str(_safe_int(kwargs.get("guild_id"), 0)) if _safe_int(kwargs.get("guild_id"), 0) else None,
                "channel_id": str(_safe_int(kwargs.get("channel_id"), 0)) if _safe_int(kwargs.get("channel_id"), 0) else None,
                "requester_id": str(_safe_int(kwargs.get("requester_id"), 0)) if _safe_int(kwargs.get("requester_id"), 0) else None,
                "user_id": str(_safe_int(kwargs.get("requester_id"), 0)) if _safe_int(kwargs.get("requester_id"), 0) else None,
                "expires_at": _safe_str(kwargs.get("expires_at")) or _utc_iso(240),
                "used": False,
                "submitted": False,
                "submitted_at": None,
                "decision": "PENDING",
                "decided_by": None,
                "decided_at": None,
                "created_at": _utc_iso(0),
                "webhook_url": _safe_str(kwargs.get("webhook_url")) or f"bot://channel/{_safe_int(kwargs.get('channel_id'), 0)}",
                "approved_user_id": None,
                "ai_status": None,
                "identity_fingerprint": None,
                "fingerprint_version": "v1",
                "identity_source": None,
                "verification_source": None,
                "proof_captured_at": None,
                "submission_meta": {},
            }
            _set_cache(store, tok, row)
        except Exception:
            pass

        if not _in_event_loop():
            try:
                return bool(original(tok, *args, **kwargs))
            except Exception:
                return True

        _schedule(f"insert:{tok}", original, tok, *args, **kwargs)
        return True

    return sb_insert_token_nonblocking


def _make_write_wrapper(store: Any, name: str, original: Callable[..., Any]) -> Callable[..., bool]:
    def write_nonblocking(token: str, *args: Any, **kwargs: Any) -> bool:
        tok = _safe_str(token)
        if not tok:
            return False

        try:
            row = _cached_row(store, tok) or _vc_request_row(tok) or {"token": tok}
            patch: Dict[str, Any] = {}

            if name == "sb_set_used":
                used = bool(args[0]) if args else bool(kwargs.get("used", True))
                patch["used"] = used
            elif name == "sb_set_submitted":
                submitted = bool(args[0]) if args else bool(kwargs.get("submitted", True))
                patch["submitted"] = submitted
                patch["submitted_at"] = _utc_iso(0) if submitted else None
            elif name == "sb_set_submitted_at":
                patch["submitted_at"] = _safe_str(args[0] if args else kwargs.get("submitted_at")) or _utc_iso(0)
                patch["submitted"] = True
            elif name == "sb_mark_decision":
                decision = _safe_str(args[0] if args else kwargs.get("decision")) or "PENDING"
                decided_by = _safe_int(args[1] if len(args) > 1 else kwargs.get("decided_by"), 0)
                patch["decision"] = decision
                patch["decided_by"] = str(decided_by) if decided_by else None
                patch["decided_at"] = _utc_iso(0)
                if decision.upper().startswith(("APPROVED", "DENIED")):
                    patch["used"] = True
                approved = _safe_int(kwargs.get("approved_user_id"), 0)
                if approved:
                    patch["approved_user_id"] = str(approved)

            row.update(patch)
            _set_cache(store, tok, row)
        except Exception:
            pass

        if not _in_event_loop():
            try:
                return bool(original(tok, *args, **kwargs))
            except Exception:
                return bool(_cached_row(store, tok))

        _schedule(f"{name}:{tok}:{_utc_iso(0)}", original, tok, *args, **kwargs)
        return True

    return write_nonblocking


def _patch_module_refs(store: Any) -> None:
    replacements = {
        "sb_get_token_info": getattr(store, "sb_get_token_info", None),
        "sb_insert_token": getattr(store, "sb_insert_token", None),
        "sb_set_used": getattr(store, "sb_set_used", None),
        "sb_set_submitted": getattr(store, "sb_set_submitted", None),
        "sb_set_submitted_at": getattr(store, "sb_set_submitted_at", None),
        "sb_mark_decision": getattr(store, "sb_mark_decision", None),
    }

    target_modules = (
        "stoney_verify.interaction_handlers",
        "stoney_verify.verification_new.voice_verify",
        "stoney_verify.commands_ext.vc_flow",
        "stoney_verify.verify_ui",
        "stoney_verify.commands",
    )

    for module_name in target_modules:
        module = sys.modules.get(module_name)
        if module is None:
            try:
                module = __import__(module_name, fromlist=["*"])
            except Exception:
                continue
        for attr, replacement in replacements.items():
            if callable(replacement) and hasattr(module, attr):
                try:
                    setattr(module, attr, replacement)
                except Exception:
                    pass


def patch_nonblocking_store_runtime() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify import store
    except Exception as e:
        _warn(f"store import failed: {e!r}")
        return False

    try:
        if not hasattr(store, "_STONEY_ORIGINAL_STORE_FUNCS"):
            store._STONEY_ORIGINAL_STORE_FUNCS = {  # type: ignore[attr-defined]
                "sb_get_token_info": getattr(store, "sb_get_token_info", None),
                "sb_insert_token": getattr(store, "sb_insert_token", None),
                "sb_set_used": getattr(store, "sb_set_used", None),
                "sb_set_submitted": getattr(store, "sb_set_submitted", None),
                "sb_set_submitted_at": getattr(store, "sb_set_submitted_at", None),
                "sb_mark_decision": getattr(store, "sb_mark_decision", None),
            }

        originals = getattr(store, "_STONEY_ORIGINAL_STORE_FUNCS", {}) or {}

        original_get = originals.get("sb_get_token_info")
        if callable(original_get):
            store.sb_get_token_info = _make_get_token_info(store, original_get)  # type: ignore[assignment]

        original_insert = originals.get("sb_insert_token")
        if callable(original_insert):
            store.sb_insert_token = _make_insert_token(store, original_insert)  # type: ignore[assignment]

        for name in ("sb_set_used", "sb_set_submitted", "sb_set_submitted_at", "sb_mark_decision"):
            original = originals.get(name)
            if callable(original):
                setattr(store, name, _make_write_wrapper(store, name, original))

        _patch_module_refs(store)
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False

    _PATCHED = True
    _log("Supabase token helpers are event-loop safe")
    return True


patch_nonblocking_store_runtime()


__all__ = ["patch_nonblocking_store_runtime"]
