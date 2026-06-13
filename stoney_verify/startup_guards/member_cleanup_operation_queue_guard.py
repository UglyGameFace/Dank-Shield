from __future__ import annotations

"""Route confirmed member cleanup removals through the shared operation queue.

The public cleanup commands already do confirmation and final safety checks. This
guard adds the shared queue/idempotency layer so cleanup-user, cleanup-queue,
and purge-all execution cannot race through duplicate clicks or parallel staff
runs in the same guild.
"""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__


def _log(message: str) -> None:
    try:
        print(f"🧱 member_cleanup_operation_queue_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ member_cleanup_operation_queue_guard {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _fallback_result(service_mod: Any, request: Any, status: str, reason: str) -> Any:
    cls = getattr(service_mod, "MemberCleanupResult", None)
    target_id = _safe_int(getattr(request, "target_user_id", 0), 0)
    try:
        if callable(cls):
            return cls(
                False,
                status,
                target_id,
                str(target_id or "Unknown member"),
                [reason],
                [],
            )
    except Exception:
        pass
    return {"ok": False, "status": status, "target_user_id": target_id, "reasons": [reason]}


def _wrap_service(service_mod: Any) -> bool:
    original = getattr(service_mod, "execute_member_cleanup", None)
    if not callable(original) or getattr(original, "_member_cleanup_operation_queue_wrapped", False):
        return False

    async def wrapped(guild: Any, request: Any) -> Any:
        gid = _safe_int(getattr(guild, "id", getattr(request, "guild_id", 0)), 0)
        actor_id = _safe_int(getattr(request, "actor_user_id", 0), 0)
        target_id = _safe_int(getattr(request, "target_user_id", 0), 0)
        reason = str(getattr(request, "reason", "") or "")[:180]
        try:
            from ..operation_queue import run_exclusive

            state, result, _job = await run_exclusive(
                guild_id=gid or "global",
                actor_id=actor_id or None,
                operation_type="inactive_purge_execute" if "purge" in reason.lower() else "member_cleanup_execute",
                risk_level="dangerous",
                source="discord_command",
                payload={"target_user_id": target_id, "reason": reason},
                concurrency_class="member_role_mutation",
                concurrency_key=f"member:{target_id or 'unknown'}",
                timeout_seconds=180.0,
                reject_if_busy=True,
                factory=lambda: original(guild, request),
            )
            if state in {"succeeded", "partial", "failed"} and result is not None:
                return result
            if state == "duplicate":
                return _fallback_result(
                    service_mod,
                    request,
                    "Duplicate cleanup blocked",
                    "That cleanup was already submitted moments ago. Refresh the queue/result before pressing again.",
                )
            if state == "busy":
                return _fallback_result(
                    service_mod,
                    request,
                    "Cleanup already running",
                    "A cleanup operation is already running for this member. Wait for it to finish before trying again.",
                )
            return _fallback_result(
                service_mod,
                request,
                "Cleanup did not finish",
                "The cleanup operation did not return a final result. Check the bot logs and try again.",
            )
        except Exception as e:
            _warn(f"cleanup queue unavailable; running original: {e!r}")
            return await original(guild, request)

    setattr(wrapped, "_member_cleanup_operation_queue_wrapped", True)
    setattr(wrapped, "_member_cleanup_operation_queue_original", original)
    setattr(service_mod, "execute_member_cleanup", wrapped)
    return True


def _patch_public_group(group_mod: Any, service_mod: Any) -> bool:
    wrapped = getattr(service_mod, "execute_member_cleanup", None)
    if callable(wrapped):
        try:
            setattr(group_mod, "execute_member_cleanup", wrapped)
            return True
        except Exception:
            return False
    return False


def _patch_loaded() -> None:
    try:
        service_mod = sys.modules.get("stoney_verify.members_new.cleanup_service")
        if service_mod is not None:
            _wrap_service(service_mod)
            group_mod = sys.modules.get("stoney_verify.commands_ext.public_members_cleanup_group")
            if group_mod is not None:
                _patch_public_group(group_mod, service_mod)
    except Exception as e:
        _warn(f"patch loaded cleanup modules failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.members_new.cleanup_service" or name.endswith(".members_new.cleanup_service"):
            target = sys.modules.get("stoney_verify.members_new.cleanup_service") or sys.modules.get(name)
            if target is not None:
                _wrap_service(target)
        _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_member_cleanup_operation_queue_import_hook", False):
        _patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_member_cleanup_operation_queue_import_hook", True)
        _patch_loaded()
        _log("loaded; member cleanup operation queue guard active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
